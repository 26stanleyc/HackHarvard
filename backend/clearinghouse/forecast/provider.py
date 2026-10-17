"""Forecast provider interface (design B2, B7) and the shared result / bid-feature definitions.

Two implementations sit behind ``ForecastProvider``:

* ``LocalForecastProvider`` (``local.py``): trained models + processed EIA-930, in process.
* ``ChalkForecastProvider`` (``chalk.py``): Chalk online queries (written, not deployed).

``clearinghouse.forecast.get_provider()`` picks Chalk iff every ``CHALK_*`` variable is set.

What a ``ForecastResult`` holds (24 fixed-CST slots of the target day ``date``):

* ML forecasts issued at ``as_of`` = 00:00 CST on ``date`` (next-24h; all of ``date - 1``
  observed): ``demand_mw``, ``wind_mw``, ``solar_mw``.
* Planning inputs that are NOT ML-forecast, by persistence: ``nuclear_mw``, ``hydro_mw``,
  ``other_mw``, ``storage_mw`` and ``net_imports_mw`` (= -EIA-930 total interchange) are the
  previous CST day's actual same-hour values. ``curtailed_mw`` = previous day's same-hour
  curtailment fraction ``(wind_curtailed + solar_curtailed) / (wind + solar)`` applied to the
  forecast ``wind + solar``, clipped to ``[0, forecast wind + solar]``. The previous day has
  fully elapsed at ``as_of``, so these inputs (like the ML features) use only data available
  then; they match M2's "yesterday-as-forecast" planning inputs.
* ``avg_intensity`` is deliberately absent: per premise 3 it is computed by running the simulator
  on these inputs, which the API layer does.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

HOURS = 24
Source = Literal["chalk", "local"]
REGION_ALIASES = {"ERCO": "ERCO", "ERCOT": "ERCO"}

ML_SERIES = ("demand_mw", "wind_mw", "solar_mw")
PERSISTENCE_SERIES = ("nuclear_mw", "hydro_mw", "other_mw", "storage_mw", "net_imports_mw")
HOURLY_FIELDS = (*ML_SERIES, *PERSISTENCE_SERIES, "curtailed_mw")

BID_FEATURE_NAMES = (
    "window_hours",
    "min_run_hours",
    "slack_hours",
    "flexibility_ratio",
    "avg_power_mw",
    "feasible",
    # day-specific (None when no forecast date is given)
    "energy_share_of_day_demand",
    "max_mw_share_of_peak_demand",
    "window_mean_renewable_share",
    "window_curtailed_mwh",
    "window_lowest_net_load_hour",
)


def normalize_region(region: str) -> str:
    try:
        return REGION_ALIASES[region.strip().upper()]
    except KeyError:
        raise ValueError(f"unsupported region {region!r}; only ERCOT (EIA BA code ERCO)") from None


def _hourly(v: Any) -> np.ndarray:
    if (isinstance(v, np.ndarray) and v.dtype == np.float64 and v.shape == (HOURS,)
            and not v.flags.writeable):
        return v  # already validated (e.g. dataclasses.replace on a cached result): share it
    a = np.array(v, dtype="float64").reshape(-1)
    if a.shape != (HOURS,):
        raise ValueError(f"expected {HOURS} hourly values, got shape {a.shape}")
    if not np.isfinite(a).all():
        raise ValueError("hourly values must be finite")
    a.setflags(write=False)  # results are cached and shared: keep them immutable
    return a


@dataclass(frozen=True)
class ForecastResult:
    region: str
    date: str  # target CST date, YYYY-MM-DD
    as_of: str  # ISO timestamp with offset, e.g. 2024-10-14T00:00:00-06:00
    source: Source
    latency_ms: float
    demand_mw: np.ndarray
    wind_mw: np.ndarray
    solar_mw: np.ndarray
    nuclear_mw: np.ndarray
    hydro_mw: np.ndarray
    other_mw: np.ndarray
    storage_mw: np.ndarray
    net_imports_mw: np.ndarray
    curtailed_mw: np.ndarray
    methods: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in HOURLY_FIELDS:
            object.__setattr__(self, name, _hourly(getattr(self, name)))

    @property
    def fixed_nonimport_mw(self) -> np.ndarray:
        """Non-fossil generation (same definition as ``clearinghouse.inputs``)."""
        return (self.nuclear_mw + self.hydro_mw + self.wind_mw + self.solar_mw + self.other_mw
                + self.storage_mw)

    @property
    def fixed_supply_mw(self) -> np.ndarray:
        return self.fixed_nonimport_mw + self.net_imports_mw

    def with_latency(self, latency_ms: float) -> ForecastResult:
        return dataclasses.replace(self, latency_ms=float(latency_ms))

    def api_hourly(self) -> dict[str, list[float]]:
        """``forecast.hourly`` minus ``avg_intensity`` (the API layer adds it via the simulator)."""
        return {"demand": self.demand_mw.tolist(), "wind": self.wind_mw.tolist(),
                "solar": self.solar_mw.tolist()}

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {k: getattr(self, k) for k in
                               ("region", "date", "as_of", "source", "latency_ms")}
        out.update({k: getattr(self, k).tolist() for k in HOURLY_FIELDS})
        out["methods"] = dict(self.methods)
        return out


def _bid_value(bid: Any, name: str) -> Any:
    if isinstance(bid, Mapping):
        return bid[name]
    return getattr(bid, name)


def bid_features(bid: Any, forecast: ForecastResult | None = None) -> dict[str, float | int | bool
                                                                             | None]:
    """Simple derived bid features (local implementation; Chalk serves the same names).

    ``bid`` is a ``schemas.Bid`` or a mapping with ``energy_mwh``, ``earliest_hour`` (inclusive),
    ``deadline_hour`` (exclusive) and ``max_mw``. Day-specific features need ``forecast``.
    """
    energy = float(_bid_value(bid, "energy_mwh"))
    lo, hi = int(_bid_value(bid, "earliest_hour")), int(_bid_value(bid, "deadline_hour"))
    pmax = float(_bid_value(bid, "max_mw"))
    window = max(hi - lo, 0)
    min_run = energy / pmax if pmax > 0 else None
    out: dict[str, float | int | bool | None] = dict.fromkeys(BID_FEATURE_NAMES)
    out.update(
        window_hours=window,
        min_run_hours=min_run,
        slack_hours=(window - min_run) if min_run is not None else None,
        flexibility_ratio=(window * pmax / energy) if energy > 0 else None,
        avg_power_mw=(energy / window) if window > 0 else None,
        feasible=bool(window > 0 and energy <= pmax * window + 1e-6),
    )
    if forecast is not None and window > 0:
        sl = slice(lo, hi)
        d, w, s = forecast.demand_mw, forecast.wind_mw, forecast.solar_mw
        net = d - w - s
        out.update(
            energy_share_of_day_demand=energy / float(d.sum()),
            max_mw_share_of_peak_demand=pmax / float(d.max()),
            window_mean_renewable_share=float(np.mean((w[sl] + s[sl]) / d[sl])),
            window_curtailed_mwh=float(forecast.curtailed_mw[sl].sum()),
            window_lowest_net_load_hour=int(lo + np.argmin(net[sl])),
        )
    return out


class ForecastProvider(ABC):
    """Same signatures for every backend (design B2): the API reports ``source``."""

    source: Source

    @abstractmethod
    def get_forecast(self, region: str, date: str) -> ForecastResult:
        """Forecast for the 24 CST slots of ``date``, issued at 00:00 CST on ``date``."""

    @abstractmethod
    def get_bid_features(self, bid: Any, date: str | None = None) -> dict[str, Any]:
        """Derived features of one bid; day-specific ones need ``date``."""

    def get_bid_features_many(self, bids: list[Any], date: str | None = None
                              ) -> tuple[Source, list[dict[str, Any]]]:
        """``(source that served them, features per bid in order)``."""
        return self.source, [self.get_bid_features(b, date) for b in bids]
