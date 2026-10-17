"""ERCOT hourly wind and solar curtailment from the 60-day SCED disclosure (NP3-965-ER).

Source: ``data/raw/ercot/ercot_sced_wind_solar_hourly_2023-12_2025-01.csv``, aggregated by
``scripts/download_data.py`` from the per-resource 15-minute SCED snapshots: for each hour and
technology (Resource Type WIND / PVGR) the mean over snapshots of the sum over resources of HSL
(high sustained limit = available output), Base Point (ERCOT dispatch instruction) and
Telemetered Net Output, plus ``curt_hsl_minus_bp_mw`` = mean over snapshots of
sum over resources of max(0, HSL - Base Point).

Curtailment used: HSL - Base Point (ERCOT-instructed curtailment). This is conservative: it is
~25% below NP4-732/737 (HSL - actual output), which also counts under-performance vs HSL.

Time base: SCED stamps are Central prevailing wall-clock hour BEGINNING with a repeated-hour flag
for the fall-back hour. They are localized to America/Chicago, converted to UTC hour starts, then
to fixed CST slots (``clearinghouse.data.timebase``). Output covers CST days
2023-12-01..2025-01-31, one row per UTC hour; hours absent from the source are NaN rows.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from clearinghouse.config import DATA_PROCESSED, DATA_RAW, RESULTS_DIR
from clearinghouse.data.timebase import add_cst_columns, central_local_to_utc, utc_hour_index

RAW_FILE = DATA_RAW / "ercot" / "ercot_sced_wind_solar_hourly_2023-12_2025-01.csv"
NP4_FILES = {
    "wind": DATA_RAW / "ercot" / "wind" / "ercot_wind_hourly_actual_median_2023-12_2025-12.csv",
    "solar": DATA_RAW / "ercot" / "solar" / "ercot_solar_hourly_actual_median_2023-12_2025-12.csv",
}
OUT_FILE = DATA_PROCESSED / "curtailment_hourly.parquet"
VALIDATION_FILE = RESULTS_DIR / "curtailment_validation.json"

VALUE_COLS = (
    "wind_curtailed_mw", "solar_curtailed_mw", "wind_hsl_mw", "solar_hsl_mw",
    "wind_telemetered_mw", "solar_telemetered_mw",
)
_RENAME = {"curt_hsl_minus_bp_mw": "curtailed_mw", "hsl_mw": "hsl_mw",
           "telemetered_mw": "telemetered_mw"}


def read_sced(raw_file=RAW_FILE) -> pd.DataFrame:
    s = pd.read_csv(raw_file)
    s["ts_utc"] = central_local_to_utc(s["hour_start_local"], s["repeated_hour_flag"])
    return s


def build_curtailment(raw_file=RAW_FILE, out_file=OUT_FILE) -> pd.DataFrame:
    s = read_sced(raw_file)
    if s.duplicated(["ts_utc", "tech"]).any():
        raise ValueError("duplicate (UTC hour, tech) rows in SCED aggregate")
    wide = {}
    for tech in ("wind", "solar"):
        t = s[s["tech"] == tech].set_index("ts_utc")
        for src, dst in _RENAME.items():
            wide[f"{tech}_{dst}"] = t[src]
    df = pd.DataFrame(wide).reindex(utc_hour_index()).rename_axis("ts_utc").reset_index()
    df = add_cst_columns(df)[["ts_utc", "ts_cst", "date_cst", "hour_cst", *VALUE_COLS]]
    df[list(VALUE_COLS)] = df[list(VALUE_COLS)].astype("float64")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_file, index=False)
    load_curtailment.cache_clear()
    return df


def validate_against_np4(curt: pd.DataFrame, out_file=VALIDATION_FILE) -> dict | None:
    """Compare SCED aggregates with NP4-732 (wind) / NP4-737 (solar) system-wide reports.

    NP4 rows on DST transition days are dropped: on fall-back days HSL is summed over the doubled
    hour, and on spring-forward days the hour-ending labels around the skipped hour are
    inconsistent between reports. The NP4 files already exclude the corrupt 2024-08-22/23
    postings. Returns None if NP4 files are absent.
    """
    if not all(p.is_file() for p in NP4_FILES.values()):
        return None
    result = {"note": "SCED (NP3-965-ER) vs NP4-732/737 system-wide hourly reports, overlapping "
                      "hours only. Curtailment: SCED = sum(max(0, HSL - Base Point)); "
                      "NP4 = max(0, HSLSystemWide - genSystemWide)."}
    c = curt.set_index("ts_utc")
    for tech, path in NP4_FILES.items():
        n = pd.read_csv(path)
        n["DSTFlag"] = n["DSTFlag"].astype(str).str.lower() == "true"
        per_day = n.groupby("deliveryDate")["hourEnding"].transform("size")
        transition = set(n.loc[n["DSTFlag"], "deliveryDate"]) | set(
            n.loc[per_day < 24, "deliveryDate"][
                pd.to_datetime(n.loc[per_day < 24, "deliveryDate"]).dt.month == 3])
        n = n[~n["deliveryDate"].isin(transition)]
        local = pd.to_datetime(n["deliveryDate"]) + pd.to_timedelta(n["hourEnding"] - 1, unit="h")
        n["ts_utc"] = central_local_to_utc(local, n["DSTFlag"].map({True: "Y", False: "N"}))
        n = n.set_index("ts_utc")
        m = c.join(n[["genSystemWide", "HSLSystemWide"]], how="inner").dropna(
            subset=[f"{tech}_telemetered_mw", "genSystemWide"])
        mm = m.dropna(subset=["HSLSystemWide", f"{tech}_hsl_mw"])
        np4_curt = (mm["HSLSystemWide"] - mm["genSystemWide"]).clip(lower=0)
        result[tech] = {
            "overlap_hours_output": int(len(m)),
            "corr_telemetered_vs_np4_gen": round(float(m[f"{tech}_telemetered_mw"].corr(
                m["genSystemWide"])), 4),
            "overlap_hours_hsl": int(len(mm)),
            "hsl_overlap_first_utc": str(mm.index.min()) if len(mm) else None,
            "corr_sced_hsl_vs_np4_hsl": round(float(mm[f"{tech}_hsl_mw"].corr(
                mm["HSLSystemWide"])), 4) if len(mm) else None,
            "curtailment_sced_twh": round(float(mm[f"{tech}_curtailed_mw"].sum()) / 1e6, 3),
            "curtailment_np4_twh": round(float(np4_curt.sum()) / 1e6, 3),
            "corr_curtailment": round(float(mm[f"{tech}_curtailed_mw"].corr(np4_curt)), 4)
            if len(mm) else None,
        }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, indent=2) + "\n")
    return result


@lru_cache(maxsize=1)
def load_curtailment(path=OUT_FILE) -> pd.DataFrame:
    """Processed hourly curtailment. Returns the cached frame: do not mutate it (copy first)."""
    return pd.read_parquet(path)


def complete_hours(df: pd.DataFrame) -> pd.Series:
    return df[list(VALUE_COLS)].notna().all(axis=1) & np.isfinite(df[list(VALUE_COLS)]).all(axis=1)
