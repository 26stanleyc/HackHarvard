"""Pre-registered, deterministic day split (made in M1, before any simulator or result exists).

Candidate days: 2024 CST dates (``clearinghouse.data.timebase``) with all 24 hourly slots complete
in both EIA-930 (``eia930.CORE_COLS``) and SCED curtailment (``curtailment.VALUE_COLS``), minus
2024-08-22 and 2024-08-23 (ERCOT's corrupt NP4 posting window; SCED is unaffected, excluded
conservatively).

Selection uses only input data and fixed rules / seeds:

* ``demo_days``: four curated days by weather type, picked in the order listed by ``DEMO_RULES``
  (a later rule skips days already chosen).
* ``herding_gate_days``: 7 days drawn at random (``numpy.random.default_rng(2024)``) from the
  candidates left after demo days; used for the M2/M3 herding gate and NOTHING else.
* ``calibration_train_days`` / ``calibration_holdout_days``: the remaining pool split 70/30 per
  calendar month (stratified) with ``numpy.random.default_rng(2024)``, months in order.
* ``forecast_split``: the M4 forecast model uses a time-based split instead (train before
  2024-10-01, test 2024-10-01..2024-12-31).

Demo, gate and holdout days are never used to fit calibration parameters (``never_trained_days``).
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data import curtailment, eia930

OUT_FILE = DATA_PROCESSED / "days.json"
YEAR = "2024"
EXCLUDED_DATES = {
    "2024-08-22": "ERCOT NP4 corrupt posting window (SCED unaffected; excluded conservatively)",
    "2024-08-23": "ERCOT NP4 corrupt posting window (SCED unaffected; excluded conservatively)",
}
GATE_SEED = 2024
GATE_N = 7
SPLIT_SEED = 2024
HOLDOUT_FRACTION = 0.30
PRE_REGISTERED_ON = "2026-10-17"

DEMO_RULES = {
    "windy_spring_night": (
        "Mar-May day with the highest mean wind share of demand over CST slots 0-5 "
        "(00:00-06:00)."
    ),
    "sunny_mild_day": (
        "Mar-May or Oct-Nov day with the highest daily solar energy share of demand among days "
        "whose daily-mean demand is below the median daily-mean demand of all 2024 candidate "
        "days."
    ),
    "summer_heat_peak": "Jul-Aug day with the maximum hourly peak demand.",
    "cold_winter_morning": "Jan-Feb day with the highest mean demand over CST slots 6-8 (06:00-09:00).",
}
DEMO_LABELS = {
    "windy_spring_night": "Windy spring night",
    "sunny_mild_day": "Sunny mild day",
    "summer_heat_peak": "Summer heat peak",
    "cold_winter_morning": "Cold winter morning",
}


def candidate_days(eia: pd.DataFrame, curt: pd.DataFrame) -> tuple[list[str], dict[str, str]]:
    e = eia[eia["date_cst"].str.startswith(YEAR)]
    c = curt[curt["date_cst"].str.startswith(YEAR)]
    e_ok = eia930.complete_hours(e).groupby(e["date_cst"]).sum()
    c_ok = curtailment.complete_hours(c).groupby(c["date_cst"]).sum()
    all_dates = pd.date_range(f"{YEAR}-01-01", f"{YEAR}-12-31").strftime("%Y-%m-%d")
    keep, dropped = [], {}
    for d in all_dates:
        ne, nc = int(e_ok.get(d, 0)), int(c_ok.get(d, 0))
        if d in EXCLUDED_DATES:
            dropped[d] = EXCLUDED_DATES[d]
        elif ne < 24 or nc < 24:
            dropped[d] = f"incomplete: EIA-930 {ne}/24 complete hours, curtailment {nc}/24"
        else:
            keep.append(d)
    return keep, dropped


def _daily_features(eia: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    e = eia[eia["date_cst"].isin(days)]
    g = e.groupby("date_cst")
    night = e[e["hour_cst"] <= 5].groupby("date_cst")
    morning = e[e["hour_cst"].between(6, 8)].groupby("date_cst")
    f = pd.DataFrame({
        "mean_demand_mw": g["demand_mw"].mean(),
        "peak_demand_mw": g["demand_mw"].max(),
        "solar_share": g["solar_mw"].sum() / g["demand_mw"].sum(),
        "mean_solar_mw": g["solar_mw"].mean(),
        "peak_solar_mw": g["solar_mw"].max(),
        "night_wind_mw": night["wind_mw"].mean(),
        "night_wind_share": night["wind_mw"].mean() / night["demand_mw"].mean(),
        "morning_demand_mw": morning["demand_mw"].mean(),
    })
    f["month"] = f.index.str[5:7].astype(int)
    return f


def select_demo_days(eia: pd.DataFrame, days: list[str]) -> list[dict]:
    f = _daily_features(eia, days)
    median_mean_demand = float(f["mean_demand_mw"].median())
    chosen: list[dict] = []

    def pick(key: str, pool: pd.DataFrame, col: str, describe) -> None:
        pool = pool[~pool.index.isin([c["date"] for c in chosen])]
        # idxmax returns the first (earliest) date on ties: deterministic
        d = pool[col].idxmax()
        row = f.loc[d]
        chosen.append({"key": key, "date": d, "label": DEMO_LABELS[key], "rule": DEMO_RULES[key],
                       "description": describe(row)})

    pick("windy_spring_night", f[f["month"].isin([3, 4, 5])], "night_wind_share",
         lambda r: (f"Mean overnight (00:00-06:00 CST) wind {r.night_wind_mw / 1000:.1f} GW "
                    f"({r.night_wind_share:.0%} of demand)"))
    mild = f[f["month"].isin([3, 4, 5, 10, 11]) & (f["mean_demand_mw"] < median_mean_demand)]
    pick("sunny_mild_day", mild, "solar_share",
         lambda r: (f"Solar {r.solar_share:.0%} of daily demand (peak {r.peak_solar_mw / 1000:.1f} "
                    f"GW); mean demand {r.mean_demand_mw / 1000:.1f} GW vs 2024 median "
                    f"{median_mean_demand / 1000:.1f} GW"))
    pick("summer_heat_peak", f[f["month"].isin([7, 8])], "peak_demand_mw",
         lambda r: (f"Peak demand {r.peak_demand_mw / 1000:.1f} GW (daily mean "
                    f"{r.mean_demand_mw / 1000:.1f} GW)"))
    pick("cold_winter_morning", f[f["month"].isin([1, 2])], "morning_demand_mw",
         lambda r: (f"Mean 06:00-09:00 CST demand {r.morning_demand_mw / 1000:.1f} GW "
                    f"(daily peak {r.peak_demand_mw / 1000:.1f} GW)"))
    return chosen


def split_days(pool: list[str]) -> tuple[list[str], list[str]]:
    rng = np.random.default_rng(SPLIT_SEED)
    train, hold = [], []
    by_month: dict[str, list[str]] = {}
    for d in sorted(pool):
        by_month.setdefault(d[:7], []).append(d)
    for month in sorted(by_month):
        ds = by_month[month]
        perm = rng.permutation(len(ds))
        n_hold = int(round(HOLDOUT_FRACTION * len(ds)))
        hold += [ds[i] for i in perm[:n_hold]]
        train += [ds[i] for i in perm[n_hold:]]
    return sorted(train), sorted(hold)


def build_days(eia: pd.DataFrame | None = None, curt: pd.DataFrame | None = None,
               out_file=OUT_FILE) -> dict:
    eia = eia930.load_eia930() if eia is None else eia
    curt = curtailment.load_curtailment() if curt is None else curt
    cands, dropped = candidate_days(eia, curt)
    demo = select_demo_days(eia, cands)
    demo_dates = [d["date"] for d in demo]
    pool = [d for d in cands if d not in demo_dates]
    gate = sorted(np.random.default_rng(GATE_SEED).choice(pool, size=GATE_N, replace=False).tolist())
    rest = [d for d in pool if d not in gate]
    train, hold = split_days(rest)

    sets = {"demo": set(demo_dates), "gate": set(gate), "train": set(train), "holdout": set(hold)}
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not sets[a] & sets[b], f"overlap between {a} and {b}: {sets[a] & sets[b]}"
    assert set().union(*sets.values()) == set(cands)

    payload = {
        "pre_registered_on": PRE_REGISTERED_ON,
        "time_base": "Fixed CST (UTC-6) days, 24 hourly slots, slot h = [h:00, h+1:00) CST.",
        "candidate_rule": (
            "2024 CST dates with 24/24 complete hours in EIA-930 (demand, net generation, "
            "interchange, coal, gas, nuclear, hydro, solar, wind, other) and in SCED curtailment "
            "(wind/solar curtailed, HSL, telemetered), excluding " + ", ".join(EXCLUDED_DATES) +
            "."
        ),
        "n_candidates": len(cands),
        "excluded_days": dropped,
        "demo_rules": DEMO_RULES,
        "demo_days": demo,
        "herding_gate_rule": (f"{GATE_N} days drawn without replacement by "
                              f"numpy.random.default_rng({GATE_SEED}).choice from the sorted "
                              "candidates minus demo days. Used for the herding gate only."),
        "herding_gate_days": gate,
        "calibration_split_rule": (
            "Remaining days split per calendar month (stratified): numpy.random."
            f"default_rng({SPLIT_SEED}).permutation per month in month order; first "
            f"round({HOLDOUT_FRACTION} x n) to holdout, rest to train."
        ),
        "calibration_train_days": train,
        "calibration_holdout_days": hold,
        "never_trained_days": sorted(sets["demo"] | sets["gate"] | sets["holdout"]),
        "never_trained_note": "Demo, herding-gate and holdout days are never used for fitting.",
        "forecast_split": {
            "rule": ("Time-based split for the M4 forecast model: train on hours before "
                     "2024-10-01 CST, test on 2024-10-01..2024-12-31 CST (no days excluded)."),
            "train_end_exclusive": "2024-10-01",
            "test_start": "2024-10-01",
            "test_end": "2024-12-31",
        },
        "counts": {"demo": len(demo), "gate": len(gate), "train": len(train),
                   "holdout": len(hold)},
    }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2) + "\n")
    load_days.cache_clear()
    return payload


@lru_cache(maxsize=1)
def load_days(path=OUT_FILE) -> dict:
    return json.loads(path.read_text())
