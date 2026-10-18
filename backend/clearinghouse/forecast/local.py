"""Local forecast provider: trained models (``scripts/train_forecast.py``) + processed EIA-930.

Used whenever any ``CHALK_*`` variable is empty (design B7). Models and data load once in the
constructor; forecasts are cached per date, so a repeat call for the same day is a dict lookup.
``latency_ms`` is measured on every call (cache hits included).

Issue time: 00:00 CST on the target day (``features.AS_OF_RULE``). The persistence inputs come
from the previous CST day, which has fully elapsed by then, so every number in a result uses
only data available at ``as_of``. The M10 forecast inputs (ERCOT postings before ``as_of``,
Open-Meteo GFS forecasts with lead >= 48 h) are always loaded; the models in the artifact
directory decide which features are used (``model.specs_from``).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from clearinghouse.data.curtailment import load_curtailment
from clearinghouse.data.eia930 import load_eia930
from clearinghouse.data.forecast_inputs import load_forecast_inputs
from clearinghouse.forecast import features as F
from clearinghouse.forecast import model as M
from clearinghouse.forecast.provider import (
    HOURS,
    ForecastProvider,
    ForecastResult,
    bid_features,
    normalize_region,
)

PERSISTENCE_COLS = {  # ForecastResult field -> (EIA-930 column, sign); NaN in optional cols -> 0
    "nuclear_mw": ("nuclear_mw", 1.0),
    "hydro_mw": ("hydro_mw", 1.0),
    "other_mw": ("other_mw", 1.0),
    "storage_mw": ("storage_mw", 1.0),
    "net_imports_mw": ("interchange_mw", -1.0),  # EIA-930 interchange > 0 means net exports
}
OPTIONAL = {"storage_mw"}  # not reported by ERCO before late Oct 2024 (batteries in other_mw)


class LocalForecastProvider(ForecastProvider):
    source = "local"

    def __init__(self, artifact_dir: Path = M.ARTIFACT_DIR, eia: pd.DataFrame | None = None,
                 curtailment: pd.DataFrame | None = None,
                 forecast_inputs: pd.DataFrame | None = None):
        if F.AS_OF_HOUR != HOURS:
            raise RuntimeError("persistence inputs assume as_of = 00:00 CST on the target day")
        self.models, self.spec = M.load(artifact_dir)
        self.specs = M.specs_from(self.spec)
        eia = load_eia930() if eia is None else eia
        curt = load_curtailment() if curtailment is None else curtailment
        fin = load_forecast_inputs() if forecast_inputs is None else forecast_inputs
        self.grid = F.make_grid(eia)
        cgrid = F.make_grid(curt, ("wind_curtailed_mw", "solar_curtailed_mw"))
        if cgrid.dates != self.grid.dates:
            raise ValueError("EIA-930 and curtailment tables cover different CST days")
        self.grid.series.update(cgrid.series)
        F.add_forecast_inputs(self.grid, fin)
        used = {f for s in self.specs.values() for f in (*s.features, s.base)}
        inputs = [text for prefix, text in (
            ("ercot_st", "ERCOT wind/solar forecasts from the latest posting before as_of"),
            ("om_", "Open-Meteo GFS forecasts (lead >= 48 h)"),
            ("ercot_load", "ERCOT's load forecast from the latest posting before as_of"),
            ("dam_", "ERCOT day-ahead market prices posted before as_of"),
        ) if any(f.startswith(prefix) for f in used)]
        self._ml_method = ("HistGradientBoosting day-ahead model, features from slots before "
                           "as_of" + (" plus " + "; ".join(inputs) if inputs
                                      else " (no weather data)"))
        self._cache: dict[str, ForecastResult] = {}
        self._lock = threading.Lock()

    # --- public API --------------------------------------------------------------------------

    def get_forecast(self, region: str, date: str) -> ForecastResult:
        t0 = time.perf_counter()
        region = normalize_region(region)
        key = pd.Timestamp(date).strftime("%Y-%m-%d")
        result = self._cache.get(key)
        if result is None:
            result = self._compute(region, key)
            with self._lock:
                self._cache.setdefault(key, result)
        return result.with_latency((time.perf_counter() - t0) * 1000.0)

    def get_bid_features(self, bid: Any, date: str | None = None) -> dict[str, Any]:
        fc = self.get_forecast("ERCO", date) if date is not None else None
        return bid_features(bid, fc)

    def cache_clear(self) -> None:
        with self._lock:
            self._cache.clear()

    # --- internals ---------------------------------------------------------------------------

    @staticmethod
    def _source_index(d: int) -> np.ndarray:
        """Persistence source slots: the previous CST day's same hour (all before as_of)."""
        src = d * HOURS + np.arange(HOURS) - HOURS
        assert (src < F.as_of_index(d)).all()
        return src

    def _persist(self, values: np.ndarray, src: np.ndarray, optional: bool) -> np.ndarray:
        v = values[src].copy()
        gap = ~np.isfinite(v)
        if gap.any():  # single missing slot (e.g. 2024-03-28 14:00 interchange): one day earlier
            v[gap] = values[src[gap] - HOURS]
        if optional:
            v = np.nan_to_num(v, nan=0.0)
        if not np.isfinite(v).all():
            raise ValueError("missing persistence input values")
        return v

    def _compute(self, region: str, date: str) -> ForecastResult:
        g = self.grid
        d = g.day_index(date)
        if d < F.MIN_HISTORY_DAYS:
            raise ValueError(f"{date}: needs {F.MIN_HISTORY_DAYS} days of history "
                             f"(first forecastable date {g.dates[F.MIN_HISTORY_DAYS]})")
        days = np.array([d])
        fc = {}
        for target in F.TARGETS:
            X, _ = F.build_features(g, target, days)
            fc[target] = M.predict(self.models[target], self.specs[target], X)

        src = self._source_index(d)
        persisted = {name: sign * self._persist(g.series[col], src, name in OPTIONAL)
                     for name, (col, sign) in PERSISTENCE_COLS.items()}
        ren_prev = self._persist(g.series["wind_mw"], src, False) + self._persist(
            g.series["solar_mw"], src, False)
        curt_prev = self._persist(g.series["wind_curtailed_mw"], src, False) + self._persist(
            g.series["solar_curtailed_mw"], src, False)
        frac = np.divide(curt_prev, ren_prev, out=np.zeros(HOURS), where=ren_prev > 0)
        ren_fc = fc["wind"] + fc["solar"]
        curtailed = np.clip(frac * ren_fc, 0.0, ren_fc)

        as_of = g.as_of_ts(d).strftime("%Y-%m-%dT%H:%M:%S") + "-06:00"
        src_note = "previous CST day's actual same hour (fully elapsed at as_of)"
        return ForecastResult(
            region=region, date=date, as_of=as_of, source="local", latency_ms=0.0,
            demand_mw=fc["demand"], wind_mw=fc["wind"], solar_mw=fc["solar"],
            curtailed_mw=curtailed, **persisted,
            methods={
                "demand_wind_solar": self._ml_method,
                "nuclear_hydro_other_storage_net_imports": f"persistence: {src_note}",
                "curtailed": f"persistence of curtailment fraction of wind+solar ({src_note}) "
                             "applied to forecast wind+solar, clipped to [0, wind+solar]",
                "avg_intensity": "not included: computed by the simulator from these inputs",
            },
        )
