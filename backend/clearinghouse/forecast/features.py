"""Leakage-free day-ahead features for the M4 forecast models (demand, wind, solar).

Forecast timing (adopted in M4, design B2 "next-24h"):

* Target: the 24 fixed-CST slots of day ``D`` (``clearinghouse.data.timebase``).
* Issue time ``as_of`` = 00:00 CST on day ``D`` (= hour 24 of ``D-1`` in this module's
  ``as_of_hour`` convention). Every slot of ``D-1`` is available, which is exactly the
  information the pre-registered "yesterday-as-forecast" baseline uses, so model and baseline
  are compared on the same information set. Horizon from the last observed slot (``D-1 23:00``)
  to target slot ``h`` is ``h + 1`` hours.
* A data slot is *available* iff it has fully elapsed by ``as_of``: slot start + 1 h <= as_of,
  equivalently slot start < as_of (slots are whole hours and ``as_of`` is on the hour).
* Rejected alternative, kept on record: ``as_of`` = 10:00 CST on ``D-1`` (``as_of_hour=10``).
  It made the comparison inconsistent: the yesterday baseline then uses ``D-1`` slots after the
  issue time. The builder still supports it (``as_of_hour`` parameter) for that record.

Every data feature is built from explicit source slot indices, and the builder returns, for
each row and feature, the latest source slot it read (``source_max_idx``). Any lag whose source
slot is not available at ``as_of`` is set to NaN instead of being read (with ``as_of_hour=10``:
"same hour 1 day earlier" for target hours >= 10). Calendar features (hour, day of week, day of
year, US federal holiday) are known in advance and read no data.

No weather data is used (none is in the dataset), so wind and solar forecasts rely on
persistence-type lags and calendar only.

Series live on a flat hourly grid: index ``i = day * 24 + hour`` where ``day`` counts CST days
from the first day of the processed EIA-930 window (2023-12-01).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

HOURS = 24
AS_OF_HOUR = 24  # issue time = hour 24 of D-1 = 00:00 CST on day D
AS_OF_RULE = ("as_of = 00:00 CST on target day D (next-24h forecast; all of D-1 available); "
              "only slots with start < as_of (i.e. ended by as_of) are used")
REJECTED_AS_OF_HOUR = 10  # 10:00 CST on D-1: rejected, kept on record (see module docstring)
TARGETS = ("demand", "wind", "solar")
SERIES_COL = {"demand": "demand_mw", "wind": "wind_mw", "solar": "solar_mw"}

LAG_DAYS = (1, 2, 3, 7, 14)
SAME_HOUR_MEAN_DAYS = tuple(range(2, 9))  # D-2 .. D-8, always available
WINDOWS_H = (24, 168)  # rolling windows ending at as_of
MIN_HISTORY_DAYS = 15  # first target day index with every lag/window inside the data

CALENDAR_FEATURES = ("hour", "dow", "is_weekend", "is_holiday", "doy_sin", "doy_cos")
DATA_FEATURES = (
    *(f"lag{k}d" for k in LAG_DAYS),
    "same_hour_latest",
    "same_hour_mean7",
    "last_obs",
    "last_obs_delta24",
    *(f"mean{w}h" for w in WINDOWS_H),
    "max24h",
    "min24h",
    "last_obs_minus_same_hour_mean7",
    "recent10h_diff",
    "recent10h_ratio",
    "lag2d_plus_diff",
    "lag2d_times_ratio",
)
FEATURES = (*CALENDAR_FEATURES, *DATA_FEATURES)


def as_of_index(day: np.ndarray | int, as_of_hour: int = AS_OF_HOUR) -> np.ndarray:
    """First UNAVAILABLE flat slot index for target day(s): slots ``< as_of_index`` are usable.

    ``as_of_hour`` is the issue hour counted on day ``D-1``: 24 (default) = 00:00 CST on ``D``,
    the adopted issue time; 10 = 10:00 CST on ``D-1``, the rejected setting kept on record.
    """
    if not 1 <= as_of_hour <= HOURS:
        raise ValueError("as_of_hour must be in 1..24")
    return (np.asarray(day) - 1) * HOURS + as_of_hour


@dataclass(frozen=True)
class Grid:
    """Flat hourly grid of the processed EIA-930 table (24 fixed-CST slots per day)."""

    first_day: pd.Timestamp  # CST date of day index 0
    dates: tuple[str, ...]  # CST date string per day index
    series: dict[str, np.ndarray]  # column -> flat float array (len n_days * 24)

    @property
    def n_days(self) -> int:
        return len(self.dates)

    def day_index(self, date: str) -> int:
        d = (pd.Timestamp(date) - self.first_day).days
        if not 0 <= d < self.n_days or self.dates[d] != pd.Timestamp(date).strftime("%Y-%m-%d"):
            raise KeyError(f"date {date} outside data window {self.dates[0]}..{self.dates[-1]}")
        return d

    def slot_ts(self, idx: np.ndarray) -> np.ndarray:
        """Flat slot index -> naive CST slot-start timestamps (NaT for negative/NaN)."""
        idx = np.asarray(idx, dtype="float64")
        out = np.full(idx.shape, np.datetime64("NaT", "ns"), dtype="datetime64[ns]")
        ok = np.isfinite(idx) & (idx >= 0)
        out[ok] = (np.datetime64(self.first_day.to_datetime64(), "ns")
                   + idx[ok].astype("int64") * np.timedelta64(1, "h"))
        return out

    def as_of_ts(self, day: int, as_of_hour: int = AS_OF_HOUR) -> pd.Timestamp:
        return self.first_day + pd.Timedelta(days=day - 1, hours=as_of_hour)


def make_grid(eia: pd.DataFrame, columns: tuple[str, ...] | None = None) -> Grid:
    """Build the flat grid. Requires the processed table's complete 24-slot-per-day layout."""
    df = eia.sort_values("ts_utc")
    if len(df) % HOURS or not (df["hour_cst"].to_numpy() == np.tile(np.arange(HOURS),
                                                                  len(df) // HOURS)).all():
        raise ValueError("EIA-930 table must have 24 consecutive CST slots per day")
    dates = tuple(df["date_cst"].to_numpy()[::HOURS])
    cols = columns or tuple(c for c in df.columns if c.endswith("_mw"))
    series = {c: df[c].to_numpy(dtype="float64").copy() for c in cols}
    return Grid(first_day=pd.Timestamp(dates[0]), dates=dates, series=series)


def _take(values: np.ndarray, idx: np.ndarray, avail_before: np.ndarray) -> np.ndarray:
    """values[idx] where 0 <= idx < avail_before (per row), else NaN. Never reads idx >= limit."""
    ok = (idx >= 0) & (idx < avail_before) & (idx < len(values))
    out = np.full(idx.shape, np.nan)
    out[ok] = values[idx[ok]]
    return out


def _holidays(first: pd.Timestamp, last: pd.Timestamp) -> set[str]:
    cal = USFederalHolidayCalendar()
    return set(cal.holidays(first - pd.Timedelta(days=1), last + pd.Timedelta(days=1))
               .strftime("%Y-%m-%d"))


def build_features(grid: Grid, target: str, days: np.ndarray,
                   as_of_hour: int = AS_OF_HOUR) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Features for every (day, hour) of the target days.

    Returns ``(X, source_max_idx)``: ``X`` has columns ``FEATURES`` (rows ordered day-major,
    hour-minor) plus ``day``/``hour``; ``source_max_idx`` has the latest flat slot index read
    by each data feature for that row (NaN if the feature read nothing, e.g. calendar features
    or a masked lag). Leakage contract: ``source_max_idx < as_of_index(day)`` everywhere.
    """
    values = grid.series[SERIES_COL[target]]
    days = np.asarray(days, dtype="int64")
    d = np.repeat(days, HOURS)
    h = np.tile(np.arange(HOURS), len(days))
    t = d * HOURS + h  # target slot index
    a = as_of_index(d, as_of_hour)  # first unavailable slot index per row
    feats: dict[str, np.ndarray] = {}
    src: dict[str, np.ndarray] = {}

    def single(name: str, idx: np.ndarray) -> None:
        v = _take(values, idx, a)
        feats[name] = v
        src[name] = np.where(np.isfinite(v), idx, np.nan).astype("float64")

    def window(name: str, idx2d: np.ndarray, how: str) -> None:
        ok = (idx2d >= 0) & (idx2d < a[:, None])
        vals = np.where(ok, values[np.clip(idx2d, 0, len(values) - 1)], np.nan)
        with np.errstate(all="ignore"):
            agg = {"mean": np.nanmean, "max": np.nanmax, "min": np.nanmin}[how]
            v = agg(vals, axis=1) if vals.shape[1] else np.full(len(t), np.nan)
        any_ok = np.isfinite(vals).any(axis=1)
        v = np.where(any_ok, v, np.nan)
        feats[name] = v
        src[name] = np.where(any_ok, np.where(ok, idx2d, -1).max(axis=1), np.nan).astype(float)

    for k in LAG_DAYS:
        single(f"lag{k}d", t - HOURS * k)
    # Latest same-hour value that is available: D-1 if its slot is before as_of, else D-2.
    lat_idx = np.where(t - HOURS < a, t - HOURS, t - 2 * HOURS)
    single("same_hour_latest", lat_idx)
    window("same_hour_mean7", t[:, None] - HOURS * np.array(SAME_HOUR_MEAN_DAYS)[None, :], "mean")
    single("last_obs", a - 1)
    prev = _take(values, a - 1 - HOURS, a)
    feats["last_obs_delta24"] = feats["last_obs"] - prev
    src["last_obs_delta24"] = np.where(np.isfinite(feats["last_obs_delta24"]), a - 1, np.nan)
    for w in WINDOWS_H:
        window(f"mean{w}h", a[:, None] - np.arange(1, w + 1)[None, :], "mean")
    window("max24h", a[:, None] - np.arange(1, 25)[None, :], "max")
    window("min24h", a[:, None] - np.arange(1, 25)[None, :], "min")
    feats["last_obs_minus_same_hour_mean7"] = feats["last_obs"] - feats["same_hour_mean7"]
    src["last_obs_minus_same_hour_mean7"] = np.fmax(src["last_obs"], src["same_hour_mean7"])

    # Level shift: last 10 available slots (D-1 14:00-23:00 at the adopted as_of) vs the same 10
    # slots one day earlier.
    window("_recent10", a[:, None] - np.arange(1, 11)[None, :], "mean")
    window("_prior10", a[:, None] - HOURS - np.arange(1, 11)[None, :], "mean")
    r10, p10 = feats.pop("_recent10"), feats.pop("_prior10")
    r10_src = src.pop("_recent10")
    src.pop("_prior10")
    feats["recent10h_diff"] = r10 - p10
    with np.errstate(all="ignore"):
        feats["recent10h_ratio"] = np.where(np.abs(p10) > 1.0, r10 / p10, np.nan)
    feats["lag2d_plus_diff"] = feats["lag2d"] + feats["recent10h_diff"]
    feats["lag2d_times_ratio"] = feats["lag2d"] * feats["recent10h_ratio"]
    for name in ("recent10h_diff", "recent10h_ratio"):
        src[name] = np.where(np.isfinite(feats[name]), r10_src, np.nan)
    for name in ("lag2d_plus_diff", "lag2d_times_ratio"):
        src[name] = np.where(np.isfinite(feats[name]), np.fmax(src["lag2d"], r10_src), np.nan)

    # Calendar (known in advance; reads no data).
    target_dates = pd.DatetimeIndex(grid.first_day + pd.to_timedelta(d, unit="D"))
    hol = _holidays(target_dates.min(), target_dates.max())
    doy = target_dates.dayofyear.to_numpy()
    feats["hour"] = h.astype(float)
    feats["dow"] = target_dates.dayofweek.to_numpy().astype(float)
    feats["is_weekend"] = (feats["dow"] >= 5).astype(float)
    feats["is_holiday"] = np.isin(target_dates.strftime("%Y-%m-%d"), list(hol)).astype(float)
    feats["doy_sin"] = np.sin(2 * np.pi * doy / 365.25)
    feats["doy_cos"] = np.cos(2 * np.pi * doy / 365.25)
    for name in CALENDAR_FEATURES:
        src[name] = np.full(len(t), np.nan)

    X = pd.DataFrame({name: feats[name] for name in FEATURES})
    X.insert(0, "hour_slot", h)
    X.insert(0, "day", d)
    S = pd.DataFrame({name: src[name] for name in FEATURES})
    return X, S


def target_values(grid: Grid, target: str, days: np.ndarray) -> np.ndarray:
    values = grid.series[SERIES_COL[target]]
    t = np.repeat(np.asarray(days, dtype="int64"), HOURS) * HOURS + np.tile(
        np.arange(HOURS), len(days))
    return values[t]


def leakage_violations(X: pd.DataFrame, S: pd.DataFrame,
                       as_of_hour: int = AS_OF_HOUR) -> list[str]:
    """Human-readable list of any feature whose source slot is not strictly before as_of."""
    a = as_of_index(X["day"].to_numpy(), as_of_hour)
    bad = []
    for name in S.columns:
        s = S[name].to_numpy()
        viol = np.isfinite(s) & (s >= a)
        if viol.any():
            i = int(np.argmax(viol))
            bad.append(f"{name}: row day={int(X['day'].iloc[i])} hour={int(X['hour_slot'].iloc[i])}"
                       f" source idx {int(s[i])} >= as_of idx {int(a[i])}")
    return bad
