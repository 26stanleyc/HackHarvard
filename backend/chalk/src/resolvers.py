"""Chalk resolvers: EIA-930 history, forecast inputs, the model-serving resolver, bid features.

NOT imported by the app and not run by pytest. Written against Chalk's docs (not deployed).

* Python resolvers: https://docs.chalk.ai/docs/python-resolvers
* SQL resolvers (production data path, see ../sql/grid_hours.chalk.sql.example):
  https://docs.chalk.ai/docs/sql
* Online vs offline: https://docs.chalk.ai/docs/resolver-online-offline

The forecasting code is the app's own (``clearinghouse/forecast/features.py`` and ``model.py``),
vendored into ``src/vendor/`` together with the trained artifacts and parquet copies of the
processed EIA-930 + curtailment history and the M10 forecast inputs (ERCOT postings, Open-Meteo
forecasts) by ``../sync_from_app.py``. One implementation, two serving paths: the Chalk path and
the local path cannot drift apart.

Hackathon data source: the history is a static file bundled with the deployment (Chalk has no
Parquet source; https://docs.chalk.ai/docs/sql lists PostgreSQL/BigQuery/Snowflake/... sources).
In production the hourly table would live in one of those sources, refreshed from the EIA API,
and ``grid_hours_offline`` would become the SQL resolver in ``../sql/``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from chalk import offline, online
from chalk.features import DataFrame, Features

from src.features import FlexBid, ForecastInput, GridDay, GridHour
from src.vendor import features as F
from src.vendor import model as M
from src.vendor.provider import BID_FEATURE_NAMES, ForecastResult, bid_features

VENDOR = Path(__file__).resolve().parent / "vendor"
HISTORY_FILE = VENDOR / "data" / "grid_hours.parquet"
INPUTS_FILE = VENDOR / "data" / "forecast_inputs.parquet"
HOURS = 24
HOURLY_COLS = ("demand_mw", "wind_mw", "solar_mw", "nuclear_mw", "hydro_mw", "other_mw",
               "storage_mw", "net_imports_mw", "wind_curtailed_mw", "solar_curtailed_mw")


@lru_cache(maxsize=1)
def _history() -> pd.DataFrame:
    return pd.read_parquet(HISTORY_FILE)


def _as_grid_hours(h: pd.DataFrame) -> DataFrame:
    """Chalk DataFrame of GridHour rows: columns must be feature names ("grid_hour.ts")."""
    return DataFrame(h.rename(columns=lambda c: f"grid_hour.{c}"))


@lru_cache(maxsize=1)
def _forecast_inputs() -> pd.DataFrame:
    return pd.read_parquet(INPUTS_FILE)


def _as_forecast_inputs(fi: pd.DataFrame) -> DataFrame:
    """Chalk DataFrame of ForecastInput rows ("forecast_input.published_at", ...)."""
    return DataFrame(fi.rename(columns=lambda c: f"forecast_input.{c}"))


def _utc(ts) -> pd.Timestamp:
    a = pd.Timestamp(ts)
    return a.tz_localize("UTC") if a.tzinfo is None else a.tz_convert("UTC")


@lru_cache(maxsize=1)
def _models():
    """Models and the specs they were trained with (M10: forecast-input features)."""
    models, spec = M.load(VENDOR / "artifacts")
    return models, M.specs_from(spec)


# --- history -------------------------------------------------------------------------------


@offline
def grid_hours_offline() -> DataFrame[
    GridHour.id, GridHour.region, GridHour.ts, GridHour.hour_cst, GridHour.demand_mw,
    GridHour.wind_mw, GridHour.solar_mw, GridHour.nuclear_mw, GridHour.hydro_mw,
    GridHour.other_mw, GridHour.storage_mw, GridHour.net_imports_mw,
    GridHour.wind_curtailed_mw, GridHour.solar_curtailed_mw,
]:
    """All hourly rows; Chalk's point-in-time join filters them by ``ts`` in offline queries."""
    return _as_grid_hours(_history())


# Every forecast feature reads at most 15 days back (features.MIN_HISTORY_DAYS; lag14d is the
# longest) and the persistence fallback 2 days, so 21 days of history give identical forecasts
# while keeping the has-many join small (the full ~10k-row history took ~9 s per query).
HISTORY_DAYS = 21


@online
def grid_hours_online(id: GridDay.id, region: GridDay.region,
                      as_of: GridDay.as_of) -> GridDay.hours:
    """The region's hourly rows in the HISTORY_DAYS before the issue time (ts <= as_of).

    Filtering on ``as_of`` (not query time) is the leakage rule itself: slots that end after the
    forecast's issue time never reach the model. ``id`` is unused but must be an input: Chalk
    joins a has-many result back to its parent by the parent's primary key.
    """
    a = _utc(as_of)
    h = _history()
    ts = pd.to_datetime(h["ts"], utc=True)
    h = h[(h["region"] == region) & (ts <= a) & (ts > a - pd.Timedelta(days=HISTORY_DAYS))]
    return _as_grid_hours(h)


# --- forecast inputs (M10) -------------------------------------------------------------------


@offline
def forecast_inputs_offline() -> DataFrame[
    ForecastInput.id, ForecastInput.region, ForecastInput.date_cst, ForecastInput.hour_cst,
    ForecastInput.published_at, ForecastInput.ercot_stwpf, ForecastInput.ercot_wgrpp,
    ForecastInput.ercot_wind_cophsl, ForecastInput.ercot_stppf, ForecastInput.ercot_pvgrpp,
    ForecastInput.ercot_solar_cophsl, ForecastInput.om_wind100, ForecastInput.om_wind_power,
    ForecastInput.om_ghi, ForecastInput.om_temp, ForecastInput.ercot_wind_posted_utc,
    ForecastInput.ercot_solar_posted_utc, ForecastInput.om_run_init_max_utc,
]:
    """All rows; Chalk's point-in-time join filters them by ``published_at`` in offline queries."""
    return _as_forecast_inputs(_forecast_inputs())


def _inputs_for(region: str, date: str, as_of: pd.Timestamp) -> pd.DataFrame:
    """The rows a forecast for ``date`` reads: valid slots of D and D-1, published by as_of."""
    fi = _forecast_inputs()
    prev = (pd.Timestamp(date) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    pub = pd.to_datetime(fi["published_at"], utc=True)
    return fi[(fi["region"] == region) & fi["date_cst"].isin([prev, date]) & (pub <= as_of)]


@online
def forecast_inputs_online(id: GridDay.id, region: GridDay.region, date: GridDay.date,
                           as_of: GridDay.as_of) -> GridDay.forecast_inputs:
    """Forecasts for D's slots (the model inputs) and D-1's (``om_temp_prev``) published by as_of.

    Same rule as ``grid_hours_online``: filtered on ``as_of``, not query time. The vendored
    feature code then masks each source whose own time is not before as_of, as the app does.
    """
    return _as_forecast_inputs(_inputs_for(region, date, _utc(as_of)))


# --- grid day --------------------------------------------------------------------------------


def _parse_grid_day_id(id: str) -> tuple[str, str, pd.Timestamp]:
    """``"ERCO:2024-10-14"`` -> (region, date, issue time = 00:00 CST on date = 06:00 UTC)."""
    region, date = id.split(":", 1)
    # F.AS_OF_HOUR = 24 counted on date - 1, i.e. midnight CST at the start of the target date.
    as_of = (pd.Timestamp(date, tz="UTC") + pd.Timedelta(hours=6)
             - pd.Timedelta(days=1) + pd.Timedelta(hours=F.AS_OF_HOUR))
    return region, date, as_of


@online
def grid_day_keys(id: GridDay.id) -> Features[GridDay.region, GridDay.date, GridDay.as_of]:
    region, date, as_of = _parse_grid_day_id(id)
    return GridDay(region=region, date=date, as_of=as_of.to_pydatetime())


def _grid_from_hours(hours: pd.DataFrame, as_of: pd.Timestamp,
                     date: str) -> tuple[F.Grid, int]:
    """Rebuild the app's flat CST grid from point-in-time rows; returns (grid, target day idx)."""
    h = hours.copy()
    h["ts"] = pd.to_datetime(h["ts"], utc=True)
    # Belt and braces: drop anything not yet known at as_of even if the store returned it.
    h = h[h["ts"] <= as_of]
    start_cst = (h["ts"] - pd.Timedelta(hours=1) - pd.Timedelta(hours=6)).dt.tz_localize(None)
    first_day = start_cst.min().normalize()
    target_day = pd.Timestamp(date)
    n_days = (target_day - first_day).days + 1
    idx = ((start_cst - first_day) / pd.Timedelta(hours=1)).astype(int).to_numpy()
    series = {}
    for col in HOURLY_COLS:
        arr = np.full(n_days * HOURS, np.nan)
        arr[idx] = h[col].to_numpy(dtype="float64")
        series[col] = arr
    dates = tuple((first_day + pd.Timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n_days))
    return F.Grid(first_day=first_day, dates=dates, series=series), n_days - 1


def _add_inputs(grid: F.Grid, rows: pd.DataFrame) -> None:
    """``F.add_forecast_inputs`` as in LocalForecastProvider, on the grid's slots: ``rows`` (D-1
    and D) placed by CST date and hour, NaN everywhere else (no other day is read)."""
    slots = pd.DataFrame({"date_cst": np.repeat(grid.dates, HOURS),
                          "hour_cst": np.tile(np.arange(HOURS), grid.n_days)})
    slots["ts_utc"] = (pd.to_datetime(slots["date_cst"]) + pd.to_timedelta(
        slots["hour_cst"] + 6, unit="h")).dt.tz_localize("UTC")  # fixed CST = UTC-6
    F.add_forecast_inputs(grid, slots.merge(rows, on=["date_cst", "hour_cst"], how="left"))


def _forecast(hours: pd.DataFrame, inputs: pd.DataFrame, as_of: pd.Timestamp,
              date: str) -> dict[str, list[float]]:
    """The app's gradient-boosting models (same artifacts) on point-in-time history and inputs.

    Issue time 00:00 CST on the target date: the persistence inputs are the previous CST day's
    same-hour actuals, all of which have ended by as_of (same rule as LocalForecastProvider).
    Plain function (not a resolver) so both resolvers below can call it.
    """
    grid, d = _grid_from_hours(hours, as_of, date)
    _add_inputs(grid, inputs)
    models, specs = _models()
    fc = {}
    for target in F.TARGETS:
        X, S = F.build_features(grid, target, np.array([d]))
        assert not F.leakage_violations(X, S), "feature read a slot or input at/after as_of"
        fc[target] = M.predict(models[target], specs[target], X)
    src = d * HOURS + np.arange(HOURS) - HOURS  # previous CST day, same hour
    assert (src < F.as_of_index(d)).all()

    def persist(col: str) -> np.ndarray:
        v = grid.series[col][src]
        gap = ~np.isfinite(v)
        v[gap] = grid.series[col][src[gap] - HOURS]
        return np.nan_to_num(v, nan=0.0)

    ren_prev = persist("wind_mw") + persist("solar_mw")
    curt_prev = persist("wind_curtailed_mw") + persist("solar_curtailed_mw")
    frac = np.divide(curt_prev, ren_prev, out=np.zeros(HOURS), where=ren_prev > 0)
    ren_fc = fc["wind"] + fc["solar"]
    return dict(
        demand_mw=fc["demand"].tolist(), wind_mw=fc["wind"].tolist(),
        solar_mw=fc["solar"].tolist(), nuclear_mw=persist("nuclear_mw").tolist(),
        hydro_mw=persist("hydro_mw").tolist(), other_mw=persist("other_mw").tolist(),
        storage_mw=persist("storage_mw").tolist(),
        net_imports_mw=persist("net_imports_mw").tolist(),
        curtailed_mw=np.clip(frac * ren_fc, 0.0, ren_fc).tolist(),
    )


@online
def forecast_grid_day(
    id: GridDay.id, region: GridDay.region, date: GridDay.date, as_of: GridDay.as_of,
    hours: GridDay.hours, forecast_inputs: GridDay.forecast_inputs,
) -> Features[
    GridDay.demand_mw, GridDay.wind_mw, GridDay.solar_mw, GridDay.nuclear_mw, GridDay.hydro_mw,
    GridDay.other_mw, GridDay.storage_mw, GridDay.net_imports_mw, GridDay.curtailed_mw,
]:
    """Model serving: point-in-time ``hours`` and ``forecast_inputs`` -> the 24-hour forecast."""
    # Bare column names ("ts", not "grid_hour.ts"), as the vendored app code expects.
    h = hours.to_pandas(string_names=True, prefixed=False)
    fi = forecast_inputs.to_pandas(string_names=True, prefixed=False)
    return GridDay(**_forecast(h, fi, pd.Timestamp(as_of), date))


@lru_cache(maxsize=32)
def _cached_forecast(region: str, date: str) -> dict[str, list[float]]:
    """Forecast for bid features (8 bids per clear share one day): bundled history, ts <= as_of,
    and bundled forecast inputs (same rows as ``forecast_inputs_online``)."""
    _, _, as_of = _parse_grid_day_id(f"{region}:{date}")
    h = _history()
    h = h[(h["region"] == region) & (pd.to_datetime(h["ts"], utc=True) <= as_of)]
    return _forecast(h, _inputs_for(region, date, as_of), as_of, date)


# --- bid features -----------------------------------------------------------------------------


@online
def flex_bid_features(
    energy_mwh: FlexBid.energy_mwh, earliest_hour: FlexBid.earliest_hour,
    deadline_hour: FlexBid.deadline_hour, max_mw: FlexBid.max_mw,
    grid_day_id: FlexBid.grid_day_id,
) -> Features[
    FlexBid.window_hours, FlexBid.min_run_hours, FlexBid.slack_hours, FlexBid.flexibility_ratio,
    FlexBid.avg_power_mw, FlexBid.feasible, FlexBid.energy_share_of_day_demand,
    FlexBid.max_mw_share_of_peak_demand, FlexBid.window_mean_renewable_share,
    FlexBid.window_curtailed_mwh, FlexBid.window_lowest_net_load_hour,
]:
    """Same derived features as the app's local ``bid_features`` (one implementation).

    Outputs are exactly ``BID_FEATURE_NAMES`` (asserted below) so the REST client can rely on
    them.
    """
    bid = {"energy_mwh": energy_mwh, "earliest_hour": earliest_hour,
           "deadline_hour": deadline_hour, "max_mw": max_mw}
    fc = None
    if grid_day_id:
        # Day-specific features use the same forecast as grid_day (shared helpers, not a nested
        # resolver call; a has-one FlexBid -> GridDay is the alternative once deployed).
        region, date, as_of = _parse_grid_day_id(grid_day_id)
        fc = ForecastResult(region=region, date=date, as_of=str(as_of), source="chalk",
                            latency_ms=0.0, **_cached_forecast(region, date))
    values = bid_features(bid, fc)
    assert tuple(values) == BID_FEATURE_NAMES
    return FlexBid(**values)
