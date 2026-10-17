"""Chalk-backed forecast provider over Chalk's REST API (written, NOT deployed; design B7).

``chalkpy`` cannot be a dependency (its releases pin pandas<2.3 / requests<2.32; see M0), so
this talks to the REST endpoints directly with httpx. The features it queries are defined in the
separate Chalk project ``backend/chalk/`` (deploy with ``chalk apply``).

Endpoints and payloads. Chalk's docs describe the auth flow; the exact REST shapes come from the
public chalkpy client source (chalkpy 2.163.1 on PyPI: ``chalk/client/client_impl.py``,
``chalk/client/models.py``), since docs.chalk.ai has no REST reference page:

* Token: ``POST {api_server}/v1/oauth/token`` with JSON
  ``{"client_id", "client_secret", "grant_type": "client_credentials"}`` -> ``access_token``,
  ``expires_in`` (s), ``primary_environment``, ``engines`` {env_id: url},
  ``environment_id_to_name`` {env_id: name}.
  https://docs.chalk.ai/docs/online-authentication (grant type, Bearer token, ``/v1/who-am-i``)
* Online query: ``POST {engine_url}/v1/query/online``, headers ``Authorization: Bearer <token>``,
  ``X-Chalk-Env-Id: <env_id>``, ``X-Chalk-Deployment-Type: engine``; JSON ``{"inputs":
  {"<fqn>": value}, "outputs": ["<fqn>", ...], "include_meta": true, "query_name": ...}`` ->
  ``{"data": [{"field", "value", "error", "ts", ...}], "errors": [...] | null, "meta":
  {"execution_duration_s", ...}}``. The engine URL is ``engines[env_id]`` from the token
  response (falls back to the API server).
  https://pypi.org/project/chalkpy/ (client source; windowed output FQNs such as
  ``grid_day.demand_mean__86400__`` per https://docs.chalk.ai/docs/aggregations)
* Default API server: ``https://api.chalk.ai`` (chalkpy ``CHALK_API_SERVER`` default).

``CHALK_ENVIRONMENT`` may be an environment id or name (names are mapped through
``environment_id_to_name``). Credentials are read via ``clearinghouse.config`` and never logged.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import numpy as np

from clearinghouse.config import Settings, get_settings
from clearinghouse.forecast.provider import (
    BID_FEATURE_NAMES,
    HOURLY_FIELDS,
    ForecastProvider,
    ForecastResult,
    normalize_region,
)

DEFAULT_API_SERVER = "https://api.chalk.ai"
TOKEN_PATH = "/v1/oauth/token"
ONLINE_QUERY_PATH = "/v1/query/online"
TOKEN_REFRESH_MARGIN_S = 60

# Feature namespaces / names defined in backend/chalk/src/features.py (keep in sync).
GRID_DAY = "grid_day"
GRID_DAY_OUTPUTS = (*HOURLY_FIELDS, "as_of")  # list[float] x 24 per hourly field
FLEX_BID = "flex_bid"
FLEX_BID_INPUTS = ("energy_mwh", "earliest_hour", "deadline_hour", "max_mw")


class ChalkError(RuntimeError):
    """A Chalk request failed. Messages never include credentials."""


def grid_day_id(region: str, date: str) -> str:
    return f"{normalize_region(region)}:{date}"


class ChalkForecastProvider(ForecastProvider):
    source = "chalk"

    def __init__(self, client_id: str, client_secret: str, environment: str,
                 api_server: str = DEFAULT_API_SERVER, transport: httpx.BaseTransport | None = None,
                 timeout_s: float = 10.0):
        if not (client_id and client_secret and environment):
            raise ValueError("ChalkForecastProvider needs CHALK_CLIENT_ID, CHALK_CLIENT_SECRET "
                             "and CHALK_ENVIRONMENT")
        self._client_id = client_id
        self._client_secret = client_secret
        self.environment = environment
        self.api_server = api_server.rstrip("/")
        self._http = httpx.Client(transport=transport, timeout=timeout_s)
        self._token: str | None = None
        self._token_expiry = 0.0
        self._env_id: str | None = None
        self._engine_url: str | None = None

    def __repr__(self) -> str:  # never include credentials
        return f"ChalkForecastProvider(environment=<set>, api_server={self.api_server!r})"

    @classmethod
    def from_settings(cls, settings: Settings | None = None,
                      **kwargs: Any) -> ChalkForecastProvider:
        s = settings or get_settings()
        return cls(s.CHALK_CLIENT_ID, s.CHALK_CLIENT_SECRET, s.CHALK_ENVIRONMENT, **kwargs)

    # --- auth --------------------------------------------------------------------------------

    def _ensure_token(self) -> None:
        if self._token and time.time() < self._token_expiry - TOKEN_REFRESH_MARGIN_S:
            return
        resp = self._http.post(
            self.api_server + TOKEN_PATH,
            json={"client_id": self._client_id, "client_secret": self._client_secret,
                  "grant_type": "client_credentials"},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            raise ChalkError(f"Chalk token exchange failed: HTTP {resp.status_code}")
        body = resp.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + float(body.get("expires_in", 3600))
        names = body.get("environment_id_to_name") or {}
        by_name = {v: k for k, v in names.items()}
        env_id = (self.environment if self.environment in names
                  else by_name.get(self.environment, self.environment))
        self._env_id = env_id or body.get("primary_environment")
        engines = body.get("engines") or {}
        self._engine_url = (engines.get(self._env_id) or body.get("api_server")
                            or self.api_server).rstrip("/")

    # --- query -------------------------------------------------------------------------------

    def online_query(self, inputs: dict[str, Any], outputs: list[str],
                     query_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Run one online query; returns ({output fqn: value}, meta)."""
        self._ensure_token()
        payload = {"inputs": inputs, "outputs": outputs, "include_meta": True,
                   "query_name": query_name}
        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Chalk-Env-Id": str(self._env_id),
            "X-Chalk-Deployment-Type": "engine",
            "X-Chalk-Query-Name": query_name,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        resp = self._http.post(f"{self._engine_url}{ONLINE_QUERY_PATH}", json=payload,
                               headers=headers)
        if resp.status_code in (401, 403):  # token revoked/expired early: refresh once
            self._token = None
            self._ensure_token()
            headers["Authorization"] = f"Bearer {self._token}"
            resp = self._http.post(f"{self._engine_url}{ONLINE_QUERY_PATH}", json=payload,
                                   headers=headers)
        if resp.status_code != 200:
            raise ChalkError(f"Chalk online query {query_name!r} failed: HTTP {resp.status_code}")
        body = resp.json()
        if body.get("errors"):
            msgs = "; ".join(str(e.get("message", e)) for e in body["errors"])
            raise ChalkError(f"Chalk online query {query_name!r} returned errors: {msgs}")
        values = {row["field"]: row.get("value") for row in body.get("data", [])}
        missing = [o for o in outputs if o not in values]
        if missing:
            raise ChalkError(f"Chalk response missing outputs: {missing}")
        return values, body.get("meta") or {}

    def get_forecast(self, region: str, date: str) -> ForecastResult:
        t0 = time.perf_counter()
        region = normalize_region(region)
        outputs = [f"{GRID_DAY}.{name}" for name in GRID_DAY_OUTPUTS]
        values, meta = self.online_query({f"{GRID_DAY}.id": grid_day_id(region, date)}, outputs,
                                         query_name="clearinghouse_grid_day_forecast")
        hourly = {name: np.asarray(values[f"{GRID_DAY}.{name}"], dtype="float64")
                  for name in HOURLY_FIELDS}
        latency = (time.perf_counter() - t0) * 1000.0
        server_s = meta.get("execution_duration_s")
        return ForecastResult(
            region=region, date=date, as_of=str(values[f"{GRID_DAY}.as_of"]), source="chalk",
            latency_ms=latency, **hourly,
            methods={"served_by": "Chalk online query (grid_day features + model resolver)",
                     "chalk_execution_ms": "" if server_s is None else f"{server_s * 1000:.1f}",
                     "avg_intensity": "not included: computed by the simulator from these inputs"},
        )

    def get_bid_features(self, bid: Any, date: str | None = None) -> dict[str, Any]:
        get = bid.get if isinstance(bid, dict) else (lambda k: getattr(bid, k))
        inputs: dict[str, Any] = {f"{FLEX_BID}.id": str(get("id"))}
        inputs.update({f"{FLEX_BID}.{k}": get(k) for k in FLEX_BID_INPUTS})
        if date is not None:
            inputs[f"{FLEX_BID}.grid_day_id"] = grid_day_id("ERCO", date)
        outputs = [f"{FLEX_BID}.{name}" for name in BID_FEATURE_NAMES]
        values, _ = self.online_query(inputs, outputs, query_name="clearinghouse_bid_features")
        return {name: values[f"{FLEX_BID}.{name}"] for name in BID_FEATURE_NAMES}

    def get_bid_features_many(self, bids: list[Any], date: str | None = None
                              ) -> tuple[str, list[dict[str, Any]]]:
        """One online query per bid, run concurrently (httpx.Client is thread-safe)."""
        if not bids:
            return self.source, []
        self._ensure_token()  # once, before the workers share it
        with ThreadPoolExecutor(max_workers=min(len(bids), 8)) as pool:
            out = list(pool.map(lambda b: self.get_bid_features(b, date), bids))
        return self.source, out

    def close(self) -> None:
        self._http.close()
