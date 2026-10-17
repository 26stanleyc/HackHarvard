"""Chalk feature classes for the Carbon Clearinghouse day-ahead forecast.

NOT imported by the app and not run by pytest. Written against Chalk's docs; deploy with
``chalk apply`` once credentials exist (see ../README.md).

Namespaces and names here are the contract with the app's REST client
(``backend/clearinghouse/forecast/chalk.py``: ``grid_day.*`` and ``flex_bid.*``). Keep in sync.

Docs: features https://docs.chalk.ai/docs/features , time https://docs.chalk.ai/docs/time ,
has-many https://docs.chalk.ai/docs/has-many , windowed aggregations
https://docs.chalk.ai/docs/aggregations , feature types https://docs.chalk.ai/docs/feature-types

Point-in-time correctness (design B2): ``GridHour.ts`` is the END of the hourly slot, i.e. the
moment its EIA-930 value is known. Chalk never returns feature values whose feature time is after
the query time, so an offline query whose ``input_times`` are the forecast issue times (00:00 CST
on each target day D) only ever sees slots that ended by then. That is exactly the app's leakage rule
(slot start < as_of  <=>  slot end <= as_of).
"""

from __future__ import annotations

from datetime import datetime

from chalk import Windowed, windowed
from chalk.features import DataFrame, FeatureTime, _, features, has_many


@features
class GridHour:
    """One fixed-CST hourly slot of EIA-930 data for one balancing authority."""

    id: str  # "ERCO:2024-10-14T15" (region : CST slot start)
    region: str  # EIA BA code, "ERCO"
    ts: FeatureTime  # slot END (UTC); the value is known from this instant
    hour_cst: int
    demand_mw: float
    wind_mw: float
    solar_mw: float
    nuclear_mw: float
    hydro_mw: float
    other_mw: float
    storage_mw: float  # NaN -> 0 (not reported by ERCO before late Oct 2024)
    net_imports_mw: float  # = -EIA-930 total interchange
    wind_curtailed_mw: float  # ERCOT SCED HSL - base point
    solar_curtailed_mw: float


@features
class GridDay:
    """Day-ahead forecast for one region and target CST date (queried at its issue time)."""

    id: str  # "ERCO:2024-10-14"
    region: str
    date: str  # target CST date
    as_of: datetime  # issue time: 00:00 CST on date (next-24h; all of date - 1 observed)

    # History visible at query time (point-in-time filtered by GridHour.ts).
    hours: DataFrame[GridHour] = has_many(lambda: GridHour.region == GridDay.region)

    # Windowed aggregations ending at query time (= as_of): the app's mean24h / mean168h /
    # max24h / min24h features. Output FQNs e.g. grid_day.demand_mean__86400__.
    demand_mean: Windowed[float] = windowed(
        "1d", "7d", expression=_.hours[_.demand_mw, _.ts > _.chalk_window, _.ts <= _.chalk_now].mean())
    wind_mean: Windowed[float] = windowed(
        "1d", "7d", expression=_.hours[_.wind_mw, _.ts > _.chalk_window, _.ts <= _.chalk_now].mean())
    solar_mean: Windowed[float] = windowed(
        "1d", "7d", expression=_.hours[_.solar_mw, _.ts > _.chalk_window, _.ts <= _.chalk_now].mean())
    demand_max: Windowed[float] = windowed(
        "1d", expression=_.hours[_.demand_mw, _.ts > _.chalk_window, _.ts <= _.chalk_now].max())
    demand_min: Windowed[float] = windowed(
        "1d", expression=_.hours[_.demand_mw, _.ts > _.chalk_window, _.ts <= _.chalk_now].min())

    # Model-serving outputs (resolvers.py: forecast_grid_day), 24 values per field, CST slots.
    demand_mw: list[float]
    wind_mw: list[float]
    solar_mw: list[float]
    # Persistence planning inputs and curtailment (same rules as LocalForecastProvider).
    nuclear_mw: list[float]
    hydro_mw: list[float]
    other_mw: list[float]
    storage_mw: list[float]
    net_imports_mw: list[float]
    curtailed_mw: list[float]


@features
class FlexBid:
    """A flexibility bid; raw fields arrive as query inputs, derived features are online."""

    id: str
    energy_mwh: float
    earliest_hour: int  # inclusive start slot
    deadline_hour: int  # exclusive end slot
    max_mw: float
    grid_day_id: str | None  # optional "ERCO:YYYY-MM-DD" for day-specific features

    window_hours: int
    min_run_hours: float | None
    slack_hours: float | None
    flexibility_ratio: float | None
    avg_power_mw: float | None
    feasible: bool
    energy_share_of_day_demand: float | None
    max_mw_share_of_peak_demand: float | None
    window_mean_renewable_share: float | None
    window_curtailed_mwh: float | None
    window_lowest_net_load_hour: int | None
