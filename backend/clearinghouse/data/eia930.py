"""EIA-930 hourly demand, interchange and net generation by fuel for ERCOT (BA code ERCO).

Source: EIA Hourly Electric Grid Monitor six-month bulk files (no key),
``data/raw/eia930/bulk/EIA930_BALANCE_{2023_Jul_Dec,2024_Jan_Jun,2024_Jul_Dec,2025_Jan_Jun}.csv``.

Time base: see ``clearinghouse.data.timebase``. EIA-930's ``UTC Time at End of Hour`` minus 1 h is
the UTC hour start; CST slot = UTC - 6 h. Output covers CST days 2023-12-01..2025-01-31, one row
per UTC hour (24 per CST day); hours absent from the source are present as rows of NaN.

Layout change: from 2024 H2 the files split Solar and Wind into "without/with Integrated Battery
Storage", Hydro into "Excluding Pumped Storage"/"Pumped Storage", and add Battery Storage, Other
Energy Storage, Unknown Energy Storage and Geothermal. Split columns are mapped back to one
column per category by summing the parts with ``min_count=1`` (NaN only if every part is NaN).

Missing values are NaN and never filled. Categories ERCO does not report at all, or only for part
of the window, are therefore NaN where unreported: ``oil_mw`` and ``unknown_mw`` (never
reported), ``storage_mw`` (reported from late Oct 2024; before that batteries are inside
``other_mw``). ``CORE_COLS`` are the columns that must be present for an hour to count as complete.
Consumers that sum generation should treat NaN in ``OPTIONAL_FUEL_COLS`` as zero explicitly.
"""

from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from clearinghouse.config import DATA_PROCESSED, DATA_RAW
from clearinghouse.data.timebase import add_cst_columns, eia930_utc_end_to_start, utc_hour_index

RAW_DIR = DATA_RAW / "eia930" / "bulk"
BULK_FILES = (
    "EIA930_BALANCE_2023_Jul_Dec.csv",
    "EIA930_BALANCE_2024_Jan_Jun.csv",
    "EIA930_BALANCE_2024_Jul_Dec.csv",
    "EIA930_BALANCE_2025_Jan_Jun.csv",
)
OUT_FILE = DATA_PROCESSED / "eia930_erco_hourly.parquet"

_G = "Net Generation (MW) from "
# output column -> source column names (any layout); parts are summed with min_count=1
COLUMN_MAP: dict[str, tuple[str, ...]] = {
    "demand_mw": ("Demand (MW)",),
    "demand_forecast_mw": ("Demand Forecast (MW)",),
    "net_generation_mw": ("Net Generation (MW)",),
    "interchange_mw": ("Total Interchange (MW)",),
    "coal_mw": (_G + "Coal",),
    "gas_mw": (_G + "Natural Gas",),
    "nuclear_mw": (_G + "Nuclear",),
    "oil_mw": (_G + "All Petroleum Products",),
    "hydro_mw": (
        _G + "Hydropower and Pumped Storage",
        _G + "Hydropower Excluding Pumped Storage",
        _G + "Pumped Storage",
    ),
    "solar_mw": (
        _G + "Solar",
        _G + "Solar without Integrated Battery Storage",
        _G + "Solar with Integrated Battery Storage",
    ),
    "wind_mw": (
        _G + "Wind",
        _G + "Wind without Integrated Battery Storage",
        _G + "Wind with Integrated Battery Storage",
    ),
    "storage_mw": (
        _G + "Battery Storage",
        _G + "Other Energy Storage",
        _G + "Unknown Energy Storage",
    ),
    # Geothermal is a new 2024-H2 column (always empty for ERCO); folded into "other".
    "other_mw": (_G + "Other Fuel Sources", _G + "Geothermal"),
    "unknown_mw": (_G + "Unknown Fuel Sources",),
}
FUEL_COLS = (
    "coal_mw", "gas_mw", "nuclear_mw", "oil_mw", "hydro_mw", "solar_mw", "wind_mw",
    "storage_mw", "other_mw", "unknown_mw",
)
OPTIONAL_FUEL_COLS = ("oil_mw", "storage_mw", "unknown_mw")
CORE_COLS = (
    "demand_mw", "net_generation_mw", "interchange_mw",
    "coal_mw", "gas_mw", "nuclear_mw", "hydro_mw", "solar_mw", "wind_mw", "other_mw",
)
OUTPUT_COLS = ("ts_utc", "ts_cst", "date_cst", "hour_cst", *COLUMN_MAP)


def _read_bulk(path) -> pd.DataFrame:
    df = pd.read_csv(path, thousands=",", low_memory=False)
    df = df[df["Balancing Authority"] == "ERCO"]
    out = pd.DataFrame({"ts_utc": eia930_utc_end_to_start(df["UTC Time at End of Hour"])})
    for col, sources in COLUMN_MAP.items():
        present = [s for s in sources if s in df.columns]
        if not present:
            out[col] = np.nan
            continue
        parts = df[present].apply(pd.to_numeric, errors="coerce")
        out[col] = parts.sum(axis=1, min_count=1)
    return out


def build_eia930(raw_dir=RAW_DIR, out_file=OUT_FILE) -> pd.DataFrame:
    frames = [_read_bulk(raw_dir / name) for name in BULK_FILES]
    df = pd.concat(frames, ignore_index=True)
    # Six-month files are split by local date; a boundary hour could appear twice. Require any
    # duplicate to agree on demand, then keep the last.
    dups = df[df.duplicated("ts_utc", keep=False)]
    if len(dups):
        spread = dups.groupby("ts_utc")["demand_mw"].agg(lambda s: s.max() - s.min())
        if (spread > 1).any():
            raise ValueError(f"conflicting duplicate EIA-930 hours: {list(spread[spread > 1].index)}")
    df = df.drop_duplicates("ts_utc", keep="last").set_index("ts_utc").sort_index()
    df = df.reindex(utc_hour_index()).rename_axis("ts_utc").reset_index()
    df = add_cst_columns(df)[list(OUTPUT_COLS)]
    df[list(COLUMN_MAP)] = df[list(COLUMN_MAP)].astype("float64")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_file, index=False)
    load_eia930.cache_clear()
    return df


@lru_cache(maxsize=1)
def load_eia930(path=OUT_FILE) -> pd.DataFrame:
    """Processed hourly table. Returns the cached frame: do not mutate it (copy first)."""
    return pd.read_parquet(path)


def complete_hours(df: pd.DataFrame) -> pd.Series:
    """Boolean per row: every ``CORE_COLS`` value present."""
    return df[list(CORE_COLS)].notna().all(axis=1)


def fuel_sum(df: pd.DataFrame) -> pd.Series:
    """Sum of reported generation by fuel (NaN categories skipped; NaN if none reported)."""
    return df[list(FUEL_COLS)].sum(axis=1, min_count=1)
