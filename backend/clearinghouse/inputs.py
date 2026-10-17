"""Per-day simulator inputs: 24 fixed-CST hourly slots for one date (design B1, B3).

Definitions (MW, one value per CST slot ``h = 0..23``; see ``clearinghouse.data.timebase``):

* ``demand_mw``: EIA-930 ERCO demand.
* ``fixed_supply_mw``: everything that is NOT coal / gas / oil, i.e. nuclear + hydro + wind +
  solar + other + storage + unknown (optional categories NaN -> 0) + net imports, where net
  imports = -``interchange_mw`` (EIA-930 total interchange is positive for net exports). With this
  definition ``demand - fixed_supply == coal + gas + oil + residual`` where the residual is
  EIA-930's own demand vs net-generation-minus-interchange mismatch (see :func:`residual_check`).
* ``fixed_nonimport_mw``: ``fixed_supply`` without net imports (denominator part for average
  intensity), ``other_mw`` separately (it has a small non-zero emission factor).
* ``wind_mw``, ``solar_mw``: EIA-930 (post-curtailment) generation.
* ``curtailed_mw``: modeled ERCOT-instructed wind + solar curtailment (SCED HSL - base point).
* ``actual_fossil``: EIA-930 coal / gas / oil MW (oil NaN -> 0), for validation only.
* ``fuel_mix``: every EIA-930 fuel column (NaN -> 0), for validation only.

Two constructors:

* :func:`actual_inputs(date)`: the day's own actuals (used for SCORING).
* :func:`yesterday_forecast_inputs(date)`: the previous CST day's actuals relabeled as ``date``
  (the yesterday-as-forecast PLANNING baseline), but priced with ``date``'s month.

Core columns that are NaN raise ``ValueError`` (no silent filling).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from clearinghouse.data.curtailment import load_curtailment
from clearinghouse.data.eia930 import CORE_COLS, FUEL_COLS, OPTIONAL_FUEL_COLS, load_eia930

HOURS = 24
FOSSIL_COLS = ("coal_mw", "gas_mw", "oil_mw")
NONFOSSIL_GEN_COLS = tuple(c for c in FUEL_COLS if c not in FOSSIL_COLS)


@dataclass(frozen=True)
class DayInputs:
    date: str  # target CST date "YYYY-MM-DD" (what the inputs are used for)
    source_date: str  # CST date the numbers were taken from (== date for actuals)
    kind: str  # "actual" | "yesterday_forecast"
    demand_mw: np.ndarray
    fixed_supply_mw: np.ndarray
    fixed_nonimport_mw: np.ndarray
    net_imports_mw: np.ndarray
    other_mw: np.ndarray
    wind_mw: np.ndarray
    solar_mw: np.ndarray
    curtailed_mw: np.ndarray
    actual_fossil: dict[str, np.ndarray] = field(repr=False)
    fuel_mix: dict[str, np.ndarray] = field(repr=False)

    @property
    def month(self) -> int:
        """Calendar month (1..12) of the TARGET date; selects fuel prices."""
        return int(self.date[5:7])

    @property
    def price_month(self) -> str:
        """``YYYY-MM`` of the target date, the key into the EIA monthly fuel price table."""
        return self.date[:7]

    @property
    def peak_demand_mw(self) -> float:
        return float(self.demand_mw.max())

    @property
    def residual_demand_mw(self) -> np.ndarray:
        """demand - fixed_supply: what the fossil stack must serve with zero added load."""
        return self.demand_mw - self.fixed_supply_mw


def _day_rows(df: pd.DataFrame, date: str) -> pd.DataFrame:
    rows = df[df["date_cst"] == date].sort_values("hour_cst")
    if len(rows) != HOURS or list(rows["hour_cst"]) != list(range(HOURS)):
        raise ValueError(f"{date}: expected 24 CST slots, found {len(rows)}")
    return rows


def _build(date: str, source_date: str, kind: str) -> DayInputs:
    e = _day_rows(load_eia930(), source_date)
    c = _day_rows(load_curtailment(), source_date)
    missing = [col for col in CORE_COLS if e[col].isna().any()]
    if missing:
        raise ValueError(f"{source_date}: NaN in EIA-930 core columns {missing}")
    curt_cols = ("wind_curtailed_mw", "solar_curtailed_mw")
    if c[list(curt_cols)].isna().any().any():
        raise ValueError(f"{source_date}: NaN curtailment")

    def col(name: str) -> np.ndarray:
        v = e[name].to_numpy(dtype=float)
        return np.nan_to_num(v, nan=0.0) if name in OPTIONAL_FUEL_COLS else v

    fuel_mix = {name.removesuffix("_mw"): col(name) for name in FUEL_COLS}
    net_imports = -e["interchange_mw"].to_numpy(dtype=float)
    fixed_nonimport = np.sum([col(n) for n in NONFOSSIL_GEN_COLS], axis=0)
    curtailed = (c["wind_curtailed_mw"].to_numpy(dtype=float)
                 + c["solar_curtailed_mw"].to_numpy(dtype=float))
    return DayInputs(
        date=date,
        source_date=source_date,
        kind=kind,
        demand_mw=e["demand_mw"].to_numpy(dtype=float),
        fixed_supply_mw=fixed_nonimport + net_imports,
        fixed_nonimport_mw=fixed_nonimport,
        net_imports_mw=net_imports,
        other_mw=col("other_mw"),
        wind_mw=col("wind_mw"),
        solar_mw=col("solar_mw"),
        curtailed_mw=np.clip(curtailed, 0.0, None),
        actual_fossil={f.removesuffix("_mw"): col(f) for f in FOSSIL_COLS},
        fuel_mix=fuel_mix,
    )


def actual_inputs(date: str) -> DayInputs:
    """The day's own EIA-930 + curtailment actuals (scoring inputs)."""
    return _build(date, date, "actual")


def previous_day(date: str) -> str:
    return (pd.Timestamp(date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")


def yesterday_forecast_inputs(date: str) -> DayInputs:
    """Previous CST day's actuals relabeled as ``date`` (planning inputs; ``date``'s prices)."""
    return _build(date, previous_day(date), "yesterday_forecast")


def residual_check(inputs: DayInputs) -> np.ndarray:
    """Hourly ``(demand - fixed_supply) - (coal + gas + oil)`` in MW (EIA-930 balance residual)."""
    fossil = sum(inputs.actual_fossil.values())
    return inputs.residual_demand_mw - fossil
