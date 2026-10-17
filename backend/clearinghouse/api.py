"""HTTP API (design B6, milestone M6): FastAPI ``app``.

Run from ``backend/``::

    uv run uvicorn clearinghouse.api:app --port 8000

Endpoints (the contract in ``schemas.py`` is frozen; every response validates against it):

* ``GET  /api/meta``           -> ``MetaResponse``
* ``GET  /api/presets``        -> ``PresetsResponse``
* ``GET  /api/adoption-chart`` -> ``AdoptionChartResponse`` (precomputed by
  ``scripts/build_adoption_chart.py``)
* ``POST /api/clear``          -> ``ClearResponse``
* ``POST /api/evaluate``       -> ``EvaluateResponse``
* ``POST /api/bid-features``   -> ``BidFeaturesBlock`` (bid profiles from the forecast provider;
  ``/api/clear`` also returns them as ``bid_features``)
* ``GET  /api/health``         -> ``{status, params_source, forecast_source}`` (extra, not in B6)

Planning vs scoring (pinned M6 decision 1): for a request day ``D``,
``plan_sim = DaySimulator(forecast_inputs(provider.get_forecast("ERCO", D)), params)`` and
``score_sim = DaySimulator(actual_inputs(D), params)`` with the calibrated ``load_params()``.
Planning never sees actuals. Simulators are cached per day (the plan simulator per forecast
fingerprint, so a provider returning a different forecast gets a new one) and the background
population per (day, background_pct). The ``forecast`` block reports the provider's ``source``,
``latency_ms`` = wall time of the ``get_forecast`` call made in THIS request (near 0 when the
provider has the day cached), the forecast demand / wind / solar and ``avg_intensity`` =
``plan_sim.avg_intensity(0)`` (the simulator on forecast inputs, premise 3).

Errors (pinned decision 7): unknown day, more than ``MAX_BIDS`` bids, duplicate ids, infeasible
or non-finite bids -> 422 with ``{"detail": [{"bid_id": <id or null>, "message": ...}]}``.
Other request-model validation errors keep FastAPI's default 422 shape. ``/api/evaluate`` with a
hand schedule that breaks a bid returns 200 with ``feasible=false`` and per-bid errors.

CORS: http://localhost:3000, http://127.0.0.1:3000 and any origins in ``FRONTEND_ORIGINS``
(comma-separated, environment or repo-root ``.env``).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

import numpy as np
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from clearinghouse import config
from clearinghouse.clearing import (
    Background,
    BidValidationError,
    background_load,
    bid_errors,
    clear,
    evaluate,
)
from clearinghouse.config import DATA_PROCESSED, RESULTS_DIR
from clearinghouse.data import load_days
from clearinghouse.forecast import get_provider
from clearinghouse.forecast.provider import HOURLY_FIELDS, ForecastResult
from clearinghouse.inputs import actual_inputs, forecast_inputs
from clearinghouse.params import load_params
from clearinghouse.presets import PRESETS
from clearinghouse.schemas import (
    AdoptionChartResponse,
    Bid,
    BidFeaturesBlock,
    BidFeaturesRequest,
    ClearRequest,
    ClearResponse,
    EvaluateRequest,
    EvaluateResponse,
    MetaResponse,
    PresetsResponse,
)
from clearinghouse.settlement import EXACT_MAX_PLAYERS
from clearinghouse.simulator import DaySimulator

REGION = "ERCOT"
REGION_CODE = "ERCO"  # EIA-930 balancing-authority code used by the forecast providers
MAX_BIDS = 30
# Exact Shapley re-plans all 2^n coalitions; above this many bids /api/clear skips the settlement
# (payouts, receipts, leave-one-out) and still reports everything else.
SHAPLEY_MAX_BIDS = EXACT_MAX_PLAYERS
MAX_BACKGROUND_PCT = 50.0

# Illustrative defaults (stated as such in meta.data_sources and the README).
CARBON_PRICE_DEFAULT = 50.0  # $/t CO2
POOL_DEFAULT_USD = 100_000.0
BACKGROUND_PCT_DEFAULT = 10.0  # % of the day's forecast peak demand

ADOPTION_CHART_FILE = DATA_PROCESSED / "adoption_chart.json"
CALIBRATION_FILE = RESULTS_DIR / "calibration.json"
CALIBRATION_KEYS = ("coal_gas_split_error", "mef_simulated", "mef_observed", "fossil_co2_error")
DEFAULT_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


class ApiRequestError(Exception):
    """422 with the per-bid body ``{"detail": [{"bid_id", "message"}]}``."""

    def __init__(self, errors: list[dict]):
        self.errors = [{"bid_id": e.get("bid_id"), "message": str(e["message"])} for e in errors]
        super().__init__("; ".join(e["message"] for e in self.errors))


# --- days, meta ------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def demo_days() -> tuple[dict, ...]:
    """The 4 pre-registered demo days in days.json order: ``{date, label, description}``."""
    return tuple({"date": d["date"], "label": d["label"], "description": d["description"]}
                 for d in load_days()["demo_days"])


def allowed_days() -> list[str]:
    return [d["date"] for d in demo_days()]


def default_day() -> str:
    """First demo day in days.json order (pre-registered; not chosen by results)."""
    return demo_days()[0]["date"]


def check_day(day: str) -> None:
    if day not in allowed_days():
        raise ApiRequestError([{
            "bid_id": None,
            "message": (f"day {day!r} is not available; choose one of the pre-registered demo "
                        f"days {', '.join(allowed_days())}"),
        }])


def calibration() -> dict:
    raw = json.loads(CALIBRATION_FILE.read_text()) if CALIBRATION_FILE.is_file() else {}
    return {k: (float(raw[k]) if raw.get(k) is not None else None) for k in CALIBRATION_KEYS}


def data_sources(forecast_source: str, model_set: str = "M4") -> list[str]:
    """``model_set``: the local provider's forecast artifacts ("M4" or "M10", design M10)."""
    m10 = forecast_source == "local" and model_set == "M10"
    fc = ("Forecast and bid features: Chalk online queries (day-ahead demand, wind and solar "
          "from the gradient-boosted model served by Chalk; the local model answers if Chalk "
          "fails, and each response says which served it)"
          if forecast_source == "chalk" else
          "Forecast: local gradient-boosted model on EIA-930 history, issued 00:00 CST on the "
          "day (day-ahead demand, wind, solar; Chalk credentials pending)"
          + ("; inputs also include ERCOT's own wind and solar forecasts (NP4-732/737, latest "
             "posting before the issue time) and archived GFS weather forecasts (lead >= 48 h)"
             if m10 else ""))
    weather = [
        "Weather data by Open-Meteo.com (https://open-meteo.com/, CC BY 4.0): archived GFS "
        "forecasts from the Previous Runs API (100 m wind, solar radiation, 2 m temperature) at "
        "capacity-weighted ERCOT wind and solar sites and 4 load centers"] if m10 else []
    return [
        "EIA-930 hourly demand, net generation by fuel and interchange for ERCOT (BA code ERCO), "
        "EIA bulk six-month files, 2024",
        "EPA eGRID2023 rev2 plant data (PLNT23 + GEN23): ERCOT fossil plant capacity, heat rate, "
        "CO2 rate and names",
        "EIA API v2 monthly Texas fuel cost for power plants (coal and gas: Electric Power "
        "sector 98; oil: sector 1)",
        "ERCOT NP3-965-ER 60-day SCED disclosure: modeled wind and solar curtailment "
        "(HSL minus base point), validated against ERCOT NP4-732/737",
        fc,
        *weather,
        "CAMPD validation skipped (api.data.gov key pending); validation (b) uses EIA-930 fuel "
        "mix x eGRID emission factors",
        "Simulator calibrated on 248 training days, validated on 104 held-out days (2024)",
        "Illustrative defaults: carbon price $50/t, payout pool $100,000, background flexible "
        "load 10% of forecast peak",
    ]


# --- engine (cached simulators) --------------------------------------------------------------


def _fingerprint(fc: ForecastResult) -> str:
    h = hashlib.sha1(fc.source.encode())
    for name in HOURLY_FIELDS:
        h.update(np.ascontiguousarray(getattr(fc, name), dtype=float).tobytes())
    return h.hexdigest()


class Engine:
    """Forecast provider, calibrated params and per-day caches (thread-safe)."""

    def __init__(self, provider_factory: Callable[[], Any] = get_provider):
        self.params, self.params_source = load_params()
        self._provider_factory = provider_factory
        self._lock = threading.RLock()
        self._plan: dict[tuple[str, str], tuple[DaySimulator, list[float]]] = {}
        self._score: dict[str, DaySimulator] = {}
        self._background: dict[tuple[str, str, float], Background] = {}
        self._last_fc: dict[str, ForecastResult] = {}

    @property
    def provider(self):
        return self._provider_factory()

    def forecast(self, day: str, fresh: bool = True) -> tuple[ForecastResult, float]:
        """``(result, latency_ms)`` with latency = wall time of this get_forecast call.

        ``fresh=False`` reuses the day's last served forecast (a past day's forecast never
        changes), so ``/api/evaluate`` does not pay a Chalk round trip; ``/api/clear`` always
        asks the provider, so its reported source and latency are from this request.
        """
        if not fresh:
            with self._lock:
                hit = self._last_fc.get(day)
            if hit is not None and hit.source == self.provider.source:
                return hit, 0.0
        provider = self.provider
        t0 = time.perf_counter()
        fc = provider.get_forecast(REGION_CODE, day)
        latency = (time.perf_counter() - t0) * 1000.0
        with self._lock:
            self._last_fc[day] = fc
        return fc, latency

    def plan_sim(self, fc: ForecastResult) -> tuple[DaySimulator, list[float], str]:
        key = (fc.date, _fingerprint(fc))
        with self._lock:
            hit = self._plan.get(key)
            if hit is None:
                sim = DaySimulator(forecast_inputs(fc), self.params)
                intensity = [float(x) for x in sim.avg_intensity(np.zeros(24))]
                hit = self._plan[key] = (sim, intensity)
        return hit[0], hit[1], key[1]

    def score_sim(self, day: str) -> DaySimulator:
        with self._lock:
            sim = self._score.get(day)
            if sim is None:
                sim = self._score[day] = DaySimulator(actual_inputs(day), self.params)
        return sim

    def background(self, day: str, fp: str, pct: float, plan_sim: DaySimulator) -> Background:
        key = (day, fp, float(pct))
        with self._lock:
            bg = self._background.get(key)
            if bg is None:
                vec, pop = background_load(pct, plan_sim)
                vec = np.array(vec, dtype=float)
                vec.setflags(write=False)  # shared across requests
                bg = self._background[key] = (vec, pop)
        return bg

    def prepare(self, day: str, background_pct: float, fresh: bool = True):
        fc, latency = self.forecast(day, fresh)
        plan, intensity, fp = self.plan_sim(fc)
        score = self.score_sim(day)
        bg = self.background(day, fp, background_pct, plan)
        return fc, latency, plan, intensity, score, bg

    def bid_features(self, day: str, bids: list[Bid]) -> dict:
        """Served bid features (``BidFeaturesBlock``): one provider call for all bids."""
        t0 = time.perf_counter()
        source, feats = self.provider.get_bid_features_many(bids, day)
        return {"source": source, "latency_ms": (time.perf_counter() - t0) * 1000.0,
                "per_bid": {b.id: f for b, f in zip(bids, feats, strict=True)}}

    def warm(self, day: str, background_pct: float = BACKGROUND_PCT_DEFAULT) -> None:
        self.prepare(day, background_pct)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return Engine()


# --- request checks --------------------------------------------------------------------------


def _check_bids(bids: list[Bid], check_feasible: bool) -> None:
    errors: list[dict] = []
    if len(bids) > MAX_BIDS:
        errors.append({"bid_id": None,
                       "message": f"at most {MAX_BIDS} bids per request (got {len(bids)})"})
    for b in bids:
        bad = [f for f in ("energy_mwh", "max_mw") if not math.isfinite(getattr(b, f))]
        if bad:
            errors.append({"bid_id": b.id, "message": f"{', '.join(bad)} must be finite"})
    if errors:
        raise ApiRequestError(errors)
    if check_feasible:
        errs = bid_errors(bids)  # duplicate ids + infeasible bids, per bid
        if errs:
            raise ApiRequestError(errs)


def _check_scenario(background_pct: float, *money: tuple[str, float]) -> None:
    errors = []
    if not math.isfinite(background_pct) or background_pct > MAX_BACKGROUND_PCT:
        errors.append({"bid_id": None, "message": (
            f"background_pct must be between 0 and {MAX_BACKGROUND_PCT:g} (% of peak demand)")})
    for name, v in money:
        if not math.isfinite(v):
            errors.append({"bid_id": None, "message": f"{name} must be finite"})
    if errors:
        raise ApiRequestError(errors)


def _duplicate_errors(body: Any) -> list[dict]:
    """Per-bid duplicate-id errors from a raw request body (for the 422 handler)."""
    if not isinstance(body, dict) or not isinstance(body.get("bids"), list):
        return []
    ids = [b.get("id") for b in body["bids"] if isinstance(b, dict)]
    seen, dupes = set(), []
    for i in ids:
        if isinstance(i, str):
            if i in seen and i not in dupes:
                dupes.append(i)
            seen.add(i)
    return [{"bid_id": i, "message": f"duplicate bid id {i!r} (ids must be unique)"}
            for i in dupes]


# --- app -------------------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI):
    engine = get_engine()
    engine.warm(default_day())  # first request on the default day is fast
    AdoptionChartResponse.model_validate(adoption_chart())
    yield


def cors_origins() -> list[str]:
    config.get_settings()  # loads repo-root .env into the environment (existing vars win)
    extra = [o.strip().rstrip("/") for o in os.environ.get("FRONTEND_ORIGINS", "").split(",")]
    return list(dict.fromkeys([*DEFAULT_ORIGINS, *[o for o in extra if o]]))


app = FastAPI(title="Carbon Clearinghouse API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(), allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])


@app.exception_handler(ApiRequestError)
async def _api_error(_request: Request, exc: ApiRequestError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors})


@app.exception_handler(BidValidationError)
async def _bid_error(_request: Request, exc: BidValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": ApiRequestError(exc.errors).errors})


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Duplicate ids (the request models' only cross-bid check) -> per-bid shape when they are
    the only problem; everything else keeps FastAPI's default ``{"detail": [{loc, msg, ...}]}``."""
    errs = exc.errors()
    dupes = _duplicate_errors(exc.body)
    if dupes and all("duplicate bid ids" in str(e.get("msg", "")) for e in errs):
        return JSONResponse(status_code=422, content={"detail": dupes})
    # FastAPI's default body echoes each offending ``input``; a NaN / Infinity there (Python's
    # json accepts them) would make the 422 itself fail to serialize, so render them as strings.
    return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(errs))})


def _json_safe(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        return str(x)
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, list | tuple):
        return [_json_safe(v) for v in x]
    return x


@lru_cache(maxsize=1)
def adoption_chart() -> dict:
    if not ADOPTION_CHART_FILE.is_file():
        raise RuntimeError(f"{ADOPTION_CHART_FILE} missing: run scripts/build_adoption_chart.py")
    return json.loads(ADOPTION_CHART_FILE.read_text())


@app.get("/api/meta", response_model=MetaResponse)
def get_meta() -> dict:
    provider = get_engine().provider
    return {
        "region": REGION,
        "days": list(demo_days()),
        "default_day": default_day(),
        "carbon_price_default": CARBON_PRICE_DEFAULT,
        "pool_default_usd": POOL_DEFAULT_USD,
        "background_pct_default": BACKGROUND_PCT_DEFAULT,
        "max_bids": MAX_BIDS,
        "shapley_max_bids": SHAPLEY_MAX_BIDS,
        "calibration": calibration(),
        "data_sources": data_sources(provider.source,
                                     getattr(provider, "spec", {}).get("model_set", "M4")),
    }


@app.get("/api/presets", response_model=PresetsResponse)
def get_presets() -> list[dict]:
    return [p.model_dump() for p in PRESETS]


@app.get("/api/adoption-chart", response_model=AdoptionChartResponse)
def get_adoption_chart() -> dict:
    return adoption_chart()


@app.get("/api/health")
def get_health() -> dict:
    engine = get_engine()
    return {"status": "ok", "params_source": engine.params_source.split(":", 1)[0],
            "forecast_source": engine.provider.source}


@app.post("/api/clear", response_model=ClearResponse)
def post_clear(req: ClearRequest) -> dict:
    check_day(req.day)
    _check_bids(req.bids, check_feasible=True)
    _check_scenario(req.background_pct, ("carbon_price", req.carbon_price),
                    ("pool_usd", req.pool_usd))
    engine = get_engine()
    fc, latency, plan, intensity, score, bg = engine.prepare(req.day, req.background_pct)
    body = clear(req.day, req.bids, req.background_pct, req.carbon_price, req.pool_usd, plan,
                 score, background=bg, shapley=len(req.bids) <= SHAPLEY_MAX_BIDS)
    forecast = {"source": fc.source, "latency_ms": float(latency),
                "hourly": {**fc.api_hourly(), "avg_intensity": list(intensity)},
                "fallback_reason": fc.methods.get("fallback_reason")}
    return {"forecast": forecast, **body, "bid_features": engine.bid_features(req.day, req.bids)}


@app.post("/api/bid-features", response_model=BidFeaturesBlock)
def post_bid_features(req: BidFeaturesRequest) -> dict:
    """Bid profiles for the bid builder, served by the forecast provider (Chalk when configured).

    Infeasible bids are allowed here (``feasible: false``): the builder shows the profile while
    the bid is being edited.
    """
    check_day(req.day)
    _check_bids(req.bids, check_feasible=False)
    return get_engine().bid_features(req.day, req.bids)


@app.post("/api/evaluate", response_model=EvaluateResponse)
def post_evaluate(req: EvaluateRequest) -> dict:
    check_day(req.day)
    _check_bids(req.bids, check_feasible=False)  # infeasible bids -> feasible=false (contract)
    _check_scenario(req.background_pct)
    engine = get_engine()
    _fc, _lat, plan, _int, score, bg = engine.prepare(req.day, req.background_pct, fresh=False)
    return evaluate(req.day, req.bids, req.schedule, req.background_pct, plan, score,
                    background=bg)
