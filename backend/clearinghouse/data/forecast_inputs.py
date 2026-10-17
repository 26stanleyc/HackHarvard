"""M10 forecast inputs: forecasts that were already published at the issue time.

Issue time ``I`` = 00:00 CST on target day D (= D 06:00 UTC; ``forecast.features``).

* **ERCOT, point in time** (``ercot_*``): NP4-732-CD wind (STWPF, WGRPP, COP HSL) and NP4-737-CD
  solar (STPPF, PVGRPP, COP HSL), system-wide. For each target day D, the 24 CST slots of D from
  the latest posting whose ``postedDatetime`` (Central prevailing time -> UTC) is in
  ``[I - 6 h, I)`` and that has STWPF / STPPF for all 24 slots (other columns may be NaN). The
  posting time is kept per row (``ercot_{wind,solar}_posted_utc``). No such posting -> NaN for
  that day. ERCOT forecasts are of uncurtailed potential output; the EIA-930 target is after
  curtailment.
* **Open-Meteo Previous Runs API** (``om_*``), model ``gfs_global``, ``*_previous_day2`` only:
  the value at valid time T comes from a run's lead steps >= 48 h, so its run was initialized at
  or before T - 48 h. Instantaneous variables (100 m wind speed, 2 m temperature) use the mean of
  the values at the slot's start and end; radiation uses the value stamped at the slot end
  (Open-Meteo radiation is the mean over the preceding hour). The latest valid time read for a
  slot is its end (``om_valid_max_utc``); for day D that is at most D+1 06:00 UTC, so every run
  used was initialized by D-1 06:00 UTC, 24 h before ``I``. Never used: ``_previous_day0`` /
  ``_previous_day1``, observed or reanalysis weather.

Points (``weather_points.json``): 6 capacity-weighted k-means centroids of eGRID2023 ERCOT wind
plants and 6 of solar plants (weights = cluster nameplate share), and 4 load centers weighted by
2020 Census metro population.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from clearinghouse.config import DATA_PROCESSED, DATA_RAW, RESULTS_DIR
from clearinghouse.data.timebase import add_cst_columns, utc_hour_index

OUT_FILE = DATA_PROCESSED / "forecast_inputs.parquet"
COVERAGE_FILE = RESULTS_DIR / "forecast_inputs_coverage.json"
POINTS_FILE = DATA_PROCESSED / "weather_points.json"
EGRID_FILE = DATA_RAW / "egrid" / "egrid2023_data_rev2.xlsx"
PIT_DIR = DATA_RAW / "ercot" / "np4_pit"
PIT_FILES = {r: PIT_DIR / f"ercot_{r}_postings_before_issue_2023-12-12_2025-01-31.csv"
             for r in ("wind", "solar")}
OPEN_METEO_FILE = DATA_RAW / "open_meteo" / "gfs_global_previous_day2_hourly.csv"

ISSUE_OFFSET = pd.Timedelta(hours=6)  # 00:00 CST on D = D 06:00 UTC
PIT_WINDOW = pd.Timedelta(hours=6)
OM_LEAD = pd.Timedelta(hours=48)  # _previous_day2
OM_SUFFIX = "_previous_day2"
OM_VARS = {"wind_speed_100m": "wind", "shortwave_radiation": "ghi", "temperature_2m": "temp"}

ERCOT_COLS = {  # report -> {processed column: raw NP4 column}
    "wind": {"ercot_stwpf": "STWPFSystemWide", "ercot_wgrpp": "WGRPPSystemWide",
             "ercot_wind_cophsl": "COPHSLSystemWide"},
    "solar": {"ercot_stppf": "STPPFSystemWide", "ercot_pvgrpp": "PVGRPPSystemWide",
              "ercot_solar_cophsl": "COPHSLSystemWide"},
}
OM_COLS = ("om_wind100", "om_wind_power", "om_ghi", "om_temp")

N_CLUSTERS = 6
# 2020 Census metropolitan statistical area populations (U.S. Census Bureau, Aug 2021 release).
LOAD_CENTERS = (
    ("dallas_fort_worth", 32.897, -97.038, 7_637_387),  # DFW airport, between the two cities
    ("houston", 29.760, -95.370, 7_122_240),
    ("san_antonio", 29.424, -98.494, 2_558_143),
    ("austin", 30.267, -97.743, 2_283_371),
)
# Generic normalized power curve (not any specific turbine): cut-in, rated, cut-out (m/s).
CUT_IN, RATED, CUT_OUT = 3.0, 12.0, 25.0


# --- points ----------------------------------------------------------------------------------


def build_points(egrid_file=EGRID_FILE, out_file=POINTS_FILE) -> dict:
    from sklearn.cluster import KMeans

    p = pd.read_excel(egrid_file, sheet_name="PLNT23", header=1,
                      usecols=["BACODE", "PLFUELCT", "NAMEPCAP", "LAT", "LON"])
    p = p[p["BACODE"] == "ERCO"]
    out: dict = {"method": f"{N_CLUSTERS} k-means centroids (lat/lon, weighted by nameplate MW, "
                           "random_state=0) of eGRID2023 ERCOT plants per fuel; weight = "
                           "cluster nameplate share. Load centers weighted by 2020 Census MSA "
                           "population."}
    for fuel, key in (("WIND", "wind"), ("SOLAR", "solar")):
        f = p[(p["PLFUELCT"] == fuel) & (p["NAMEPCAP"] > 0)].dropna(subset=["LAT", "LON"])
        xy, cap = f[["LAT", "LON"]].to_numpy(float), f["NAMEPCAP"].to_numpy(float)
        km = KMeans(N_CLUSTERS, n_init=10, random_state=0).fit(xy, sample_weight=cap)
        pts = []
        for k in range(N_CLUSTERS):
            m = km.labels_ == k
            pts.append({"id": f"{key}_{k}", "lat": round(float(km.cluster_centers_[k, 0]), 3),
                        "lon": round(float(km.cluster_centers_[k, 1]), 3),
                        "capacity_mw": round(float(cap[m].sum()), 1), "n_plants": int(m.sum())})
        total = sum(q["capacity_mw"] for q in pts)
        for q in sorted(pts, key=lambda q: -q["capacity_mw"]):
            q["weight"] = round(q["capacity_mw"] / total, 6)
        out[key] = sorted(pts, key=lambda q: q["id"])
    pop = sum(c[3] for c in LOAD_CENTERS)
    out["load"] = [{"id": f"load_{n}", "lat": lat, "lon": lon, "population_2020": int(n_),
                    "weight": round(n_ / pop, 6)} for n, lat, lon, n_ in LOAD_CENTERS]
    out_file.write_text(json.dumps(out, indent=2) + "\n")
    return out


def load_or_build_points() -> list[dict]:
    pts = json.loads(POINTS_FILE.read_text()) if POINTS_FILE.is_file() else build_points()
    return [q for key in ("wind", "solar", "load") for q in pts[key]]


# --- ERCOT point in time ---------------------------------------------------------------------


def _local_to_utc(local: pd.Series, dst_flag: pd.Series | None = None) -> pd.Series:
    """Central prevailing wall-clock times -> UTC. ``dst_flag`` True marks ERCOT's repeated
    (standard-time) fall-back hour; without it ambiguous times become NaT (never guessed)."""
    t = pd.to_datetime(local)
    if dst_flag is None:
        aware = t.dt.tz_localize("America/Chicago", ambiguous="NaT", nonexistent="NaT")
    else:
        aware = t.dt.tz_localize("America/Chicago", ambiguous=~dst_flag.to_numpy(bool),
                                 nonexistent="NaT")
    return aware.dt.tz_convert("UTC")


def ercot_point_in_time(postings: pd.DataFrame, cols: dict[str, str],
                        posted_col: str) -> pd.DataFrame:
    """Latest complete posting strictly before the issue time, per target CST day.

    ``postings``: raw NP4 rows with ``target_day_cst``, ``postedDatetime``, ``deliveryDate``,
    ``hourEnding`` (1-24, Central prevailing), ``DSTFlag`` and the raw columns in ``cols``.
    Returns one row per CST slot of each target day that has such a posting: ``ts_utc`` (slot
    start), the processed columns and ``posted_col`` (the posting time, UTC).
    """
    df = postings.copy()
    df["posted_utc"] = _local_to_utc(df["postedDatetime"])
    issue = pd.to_datetime(df["target_day_cst"]).dt.tz_localize("UTC") + ISSUE_OFFSET
    df = df[df["posted_utc"].notna() & (df["posted_utc"] < issue)
            & (df["posted_utc"] >= issue - PIT_WINDOW)]
    flag = df["DSTFlag"].astype(str).str.lower() == "true"
    local = pd.to_datetime(df["deliveryDate"]) + pd.to_timedelta(df["hourEnding"] - 1, unit="h")
    # Spring-forward day: the hour 01:00-02:00 CST is "HE2" in NP4-732 but "HE3" in NP4-737.
    # A start that does not exist on the wall clock is placed by its end (03:00 CDT) instead.
    hour = pd.Timedelta(hours=1)
    ts = _local_to_utc(local, flag).fillna(_local_to_utc(local + hour, flag) - hour)
    df = df.assign(ts_utc=ts).dropna(subset=["ts_utc"])
    cst_day = (df["ts_utc"] - ISSUE_OFFSET).dt.strftime("%Y-%m-%d")
    df = df[cst_day == df["target_day_cst"]]
    df = df.dropna(subset=[next(iter(cols.values()))])  # STWPF / STPPF; others may be NaN
    n_slots = df.groupby(["target_day_cst", "posted_utc"])["ts_utc"].transform("nunique")
    df = df[n_slots == 24]
    latest = df.groupby("target_day_cst")["posted_utc"].transform("max")
    df = df[df["posted_utc"] == latest].drop_duplicates("ts_utc", keep="last")
    out = df[["ts_utc", *cols.values(), "posted_utc"]].rename(
        columns={**{v: k for k, v in cols.items()}, "posted_utc": posted_col})
    return out.sort_values("ts_utc").reset_index(drop=True)


# --- Open-Meteo ------------------------------------------------------------------------------


def power_curve(v: np.ndarray) -> np.ndarray:
    """Generic normalized power curve: 0 below cut-in, cubic to 1 at rated, 1 to cut-out, then 0."""
    v = np.asarray(v, dtype=float)
    ramp = (v**3 - CUT_IN**3) / (RATED**3 - CUT_IN**3)
    out = np.where(v < CUT_IN, 0.0, np.where(v < RATED, ramp, np.where(v <= CUT_OUT, 1.0, 0.0)))
    return np.where(np.isfinite(v), out, np.nan)


def _weighted(frame: pd.DataFrame, points: list[dict]) -> pd.Series:
    """Weighted mean across point columns; NaN if any point is missing (never reweighted)."""
    w = pd.Series({p["id"]: p["weight"] for p in points})
    vals = frame[w.index]
    return (vals * w).sum(axis=1, min_count=len(w)).where(vals.notna().all(axis=1))


def open_meteo_slots(raw: pd.DataFrame, points: dict, slots_utc: pd.DatetimeIndex) -> pd.DataFrame:
    """Slot features from ``*_previous_day2`` values. ``slots_utc``: UTC slot starts."""
    value_cols = [c for c in raw.columns if c not in ("point_id", "time_utc")]
    bad = [c for c in value_cols if not c.endswith(OM_SUFFIX)]
    if bad:
        raise ValueError(f"only {OM_SUFFIX} (archived forecast) columns are allowed: {bad}")
    t = pd.to_datetime(raw["time_utc"]).dt.tz_localize("UTC")
    wide = {var: raw.assign(t=t).pivot(index="t", columns="point_id", values=var + OM_SUFFIX)
            for var in OM_VARS}
    start, end = slots_utc, slots_utc + pd.Timedelta(hours=1)

    def at(var: str, times: pd.DatetimeIndex, fn=None) -> pd.DataFrame:
        f = wide[var].reindex(times)
        return f.apply(fn) if fn is not None else f

    def inst_mean(var: str, fn=None) -> pd.DataFrame:  # mean of the slot's start and end values
        a, b = at(var, start, fn), at(var, end, fn)
        return pd.DataFrame((a.to_numpy() + b.to_numpy()) / 2, index=slots_utc, columns=a.columns)

    ghi = at("shortwave_radiation", end)
    ghi.index = slots_utc
    out = pd.DataFrame(index=slots_utc)
    out["om_wind100"] = _weighted(inst_mean("wind_speed_100m"), points["wind"])
    out["om_wind_power"] = _weighted(inst_mean("wind_speed_100m", power_curve), points["wind"])
    out["om_ghi"] = _weighted(ghi, points["solar"])
    out["om_temp"] = _weighted(inst_mean("temperature_2m"), points["load"])
    out["om_valid_max_utc"] = end  # latest valid time read for this slot's values
    out.index.name = "ts_utc"
    return out


# --- build / load ----------------------------------------------------------------------------


def build_forecast_inputs(out_file=OUT_FILE, coverage_file=COVERAGE_FILE) -> tuple[pd.DataFrame,
                                                                                  dict]:
    slots = utc_hour_index()
    df = pd.DataFrame(index=slots)
    coverage: dict = {}
    for report, cols in ERCOT_COLS.items():
        raw = pd.read_csv(PIT_FILES[report])
        pit = ercot_point_in_time(raw, cols, f"ercot_{report}_posted_utc").set_index("ts_utc")
        df = df.join(pit)
        days = (df.index - ISSUE_OFFSET).strftime("%Y-%m-%d")
        have = df[next(iter(cols))].notna().groupby(days).all()
        issue = (pit.index - ISSUE_OFFSET).floor("D") + ISSUE_OFFSET
        lag_min = (issue - pit[f"ercot_{report}_posted_utc"]).dt.total_seconds() / 60
        coverage[f"ercot_{report}"] = {
            "days_with_posting": int(have.sum()), "days_total": int(len(have)),
            "missing_days": sorted(have.index[~have]),
            "posting_minutes_before_issue": {"min": round(float(lag_min.min()), 1),
                                             "median": round(float(lag_min.median()), 1),
                                             "max": round(float(lag_min.max()), 1)}}
    points = json.loads(POINTS_FILE.read_text())
    om = open_meteo_slots(pd.read_csv(OPEN_METEO_FILE), points, slots)
    df = df.join(om)
    for col in OM_COLS:
        ok = df[col].notna()
        cst = (df.index - ISSUE_OFFSET).strftime("%Y-%m-%d %H:%M CST")
        coverage[col] = {"hours": int(ok.sum()), "of_hours": int(len(ok)),
                         "first": cst[ok.to_numpy()][0], "last_missing": cst[~ok.to_numpy()][-1]}
    df = add_cst_columns(df.reset_index())
    df["om_run_init_max_utc"] = df["om_valid_max_utc"] - OM_LEAD
    df.to_parquet(out_file, index=False)
    coverage_file.write_text(json.dumps(coverage, indent=2) + "\n")
    return df, coverage


@lru_cache(maxsize=1)
def load_forecast_inputs(path=OUT_FILE) -> pd.DataFrame:
    return pd.read_parquet(path)
