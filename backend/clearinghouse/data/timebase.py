"""Model time base: 24 hourly slots per day in FIXED Central Standard Time (UTC-6).

Pinned decision (M1): the model "day" runs midnight to midnight in fixed CST (UTC-6) for every
day of the year. There is no daylight saving time, so every day has exactly 24 slots. Slot ``h``
covers ``[h:00, h+1:00)`` CST, which is ``[h+6:00, h+7:00)`` UTC. During daylight saving time
(mid-March to early November) the CST clock is one hour behind the Texas wall clock, so slot
``h`` is wall-clock hour ``h+1`` CDT.

Conversions used by every source:

* EIA-930 stamps each row with the UTC time at the END of the hour. UTC hour start = end - 1 h;
  CST = UTC - 6 h.
* ERCOT 60-day SCED stamps are Central *prevailing* (wall-clock) time with a ``Repeated Hour
  Flag`` for the fall-back hour. They are localized to ``America/Chicago`` (the flag picks
  standard time for the repeated hour), converted to UTC, then to CST.

All internal timestamps are UTC hour starts (tz-aware). ``ts_cst`` columns are naive datetimes
that mean fixed CST.
"""

from __future__ import annotations

import pandas as pd

CST_OFFSET = pd.Timedelta(hours=6)  # CST = UTC - 6 h, fixed all year
SLOTS_PER_DAY = 24

# Full model window: CST days 2023-12-01 .. 2025-01-31 (inclusive), as UTC hour starts.
WINDOW_FIRST_CST_DAY = "2023-12-01"
WINDOW_LAST_CST_DAY = "2025-01-31"


def utc_hour_index(first_cst_day: str = WINDOW_FIRST_CST_DAY,
                   last_cst_day: str = WINDOW_LAST_CST_DAY) -> pd.DatetimeIndex:
    """Every UTC hour start covering the CST days ``first..last`` inclusive (24 per day)."""
    start = pd.Timestamp(first_cst_day, tz="UTC") + CST_OFFSET
    end = pd.Timestamp(last_cst_day, tz="UTC") + CST_OFFSET + pd.Timedelta(hours=23)
    return pd.date_range(start, end, freq="h", name="ts_utc")


def eia930_utc_end_to_start(utc_end: pd.Series) -> pd.Series:
    """EIA-930 ``UTC Time at End of Hour`` strings (``%m/%d/%Y %I:%M:%S %p``) -> UTC hour start."""
    end = pd.to_datetime(utc_end, format="%m/%d/%Y %I:%M:%S %p").dt.tz_localize("UTC")
    return end - pd.Timedelta(hours=1)


def central_local_to_utc(local_hour_start: pd.Series, repeated_hour_flag: pd.Series) -> pd.Series:
    """Central prevailing wall-clock hour starts (naive) -> UTC hour starts (tz-aware).

    ``repeated_hour_flag`` is ERCOT's Y/N flag: "Y" marks the second (standard-time) occurrence
    of the fall-back hour. The spring-forward hour does not exist in the data; if it appears,
    localization raises.
    """
    local = pd.to_datetime(local_hour_start)
    is_dst = (repeated_hour_flag.astype(str).str.upper() != "Y").to_numpy()
    aware = local.dt.tz_localize("America/Chicago", ambiguous=is_dst, nonexistent="raise")
    return aware.dt.tz_convert("UTC")


def add_cst_columns(df: pd.DataFrame, ts_col: str = "ts_utc") -> pd.DataFrame:
    """Add ``ts_cst`` (naive, fixed CST), ``date_cst`` (YYYY-MM-DD string) and ``hour_cst``."""
    ts = pd.DatetimeIndex(df[ts_col])
    cst = (ts.tz_convert("UTC") - CST_OFFSET).tz_localize(None)
    out = df.copy()
    out["ts_cst"] = cst
    out["date_cst"] = cst.strftime("%Y-%m-%d")
    out["hour_cst"] = cst.hour.astype("int8")
    return out
