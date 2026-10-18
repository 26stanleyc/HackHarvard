"""M11 evaluation (rule pre-registered in docs/design.md, M11): robust vs plain planning.

Reads the paired run files written by ``scripts/scenario_savings.py`` in plain mode
(``scenario_savings_q4_2024_runs.csv``) and robust mode (``..._robust_runs.csv``), pairs runs by
(day, seed, share) and writes ``docs/results/m11_robust_vs_plain.json``.

* Primary days: the 65 days of 2024-10-01..12-31 that are not calibration-holdout days (the 27
  holdout days were used in the exploratory diagnostic). All 92 days are reported too.
* Primary metric (10 % share): dE = E_coordinated(plain) - E_coordinated(robust), per run.
* Adopt robust iff mean dE > 0 and its 95 % day-clustered bootstrap interval (2,000 resamples of
  days, seed 11) excludes 0 on the primary days.

Run from ``backend/`` after both scenario_savings runs: ``uv run python scripts/m11_evaluate.py``.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clearinghouse.config import RESULTS_DIR  # noqa: E402
from clearinghouse.data import load_days  # noqa: E402

PLAIN = RESULTS_DIR / "scenario_savings_q4_2024_runs.csv"
ROBUST = RESULTS_DIR / "scenario_savings_q4_2024_robust_runs.csv"
OUT = RESULTS_DIR / "m11_robust_vs_plain.json"
PRIMARY_SHARE = 10.0
N_BOOT = 2000
SEED = 11
STRATS = ("default", "independent_average", "independent_marginal", "coordinated")


def boot_ci(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """95 % interval of the run-mean of ``col``, resampling whole days."""
    rng = np.random.default_rng(SEED)
    by_day = df.groupby("day")[col].agg(["sum", "count"])
    sums, counts = by_day["sum"].to_numpy(), by_day["count"].to_numpy()
    idx = rng.integers(0, len(by_day), size=(N_BOOT, len(by_day)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def summarize(df: pd.DataFrame) -> dict:
    out = {"runs": int(len(df)), "days": int(df["day"].nunique())}
    d = df["dE"]
    lo, hi = boot_ci(df, "dE")
    out["dE_coordinated_t"] = {"mean": float(d.mean()), "median": float(d.median()),
                               "ci95": [lo, hi],
                               "pct_runs_robust_better": float(100 * (d > 0).mean()),
                               "pct_runs_robust_worse": float(100 * (d < 0).mean())}
    d3 = df["dE3"]
    out["dE_self_optimizing_t"] = {"mean": float(d3.mean()), "median": float(d3.median()),
                                   "ci95": list(boot_ci(df, "dE3"))}
    for mode in ("plain", "robust"):
        coord = df[f"coordinated_{mode}"]
        row = {}
        for name, base in (("vs_cleanest_slot", "independent_average"),
                           ("vs_self_optimizing", "independent_marginal"),
                           ("vs_business_as_usual", "default")):
            saved = df[f"{base}_{mode}"] - coord
            row[name] = {"mean_t": float(saved.mean()), "median_t": float(saved.median()),
                         "pct_runs_positive": float(100 * (saved > 0).mean()),
                         "pct_runs_negative": float(100 * (saved < 0).mean())}
        row["fallback_fired_pct"] = float(100 * df[f"fallback_fired_{mode}"].mean())
        out[mode] = row
    return out


def main() -> dict:
    plain, robust = pd.read_csv(PLAIN), pd.read_csv(ROBUST)
    keys = ["day", "seed", "share_pct"]
    cols = list(STRATS) + ["fallback_fired"]
    df = plain[keys + cols].merge(robust[keys + cols], on=keys, suffixes=("_plain", "_robust"),
                                  validate="one_to_one")
    if len(df) != len(plain) or len(df) != len(robust):
        raise ValueError("plain and robust runs do not pair one to one")
    df["dE"] = df["coordinated_plain"] - df["coordinated_robust"]
    df["dE3"] = df["independent_marginal_plain"] - df["independent_marginal_robust"]
    holdout = set(load_days()["calibration_holdout_days"])
    df["primary"] = ~df["day"].isin(holdout)

    result = {"generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
              "rule": __doc__.split("\n\n", 2)[1].strip(), "by_share_pct": {}}
    for share in sorted(df["share_pct"].unique()):
        s = df[df["share_pct"] == share]
        result["by_share_pct"][f"{share:g}"] = {
            "primary_days": summarize(s[s["primary"]]),
            "all_92_days": summarize(s),
            "exploration_days_only": summarize(s[~s["primary"]]),
        }
    prim = result["by_share_pct"][f"{PRIMARY_SHARE:g}"]["primary_days"]["dE_coordinated_t"]
    adopt = prim["mean"] > 0 and prim["ci95"][0] > 0
    result["decision"] = {"metric": "mean dE_coordinated_t, 10% share, primary days",
                          "mean": prim["mean"], "ci95": prim["ci95"],
                          "adopt_robust": bool(adopt)}
    OUT.write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result["decision"], indent=1))
    for share, block in result["by_share_pct"].items():
        p = block["primary_days"]
        print(f"\nshare {share}% primary days ({p['days']} days, {p['runs']} runs): "
              f"dE coordinated mean {p['dE_coordinated_t']['mean']:.1f} t "
              f"CI {p['dE_coordinated_t']['ci95'][0]:.1f}..{p['dE_coordinated_t']['ci95'][1]:.1f}, "
              f"robust better in {p['dE_coordinated_t']['pct_runs_robust_better']:.0f}% of runs")
        for mode in ("plain", "robust"):
            v = p[mode]["vs_self_optimizing"]
            print(f"  {mode:<6} vs self-optimizing mean {v['mean_t']:8.1f} t  median "
                  f"{v['median_t']:7.1f}  positive {v['pct_runs_positive']:4.0f}%  negative "
                  f"{v['pct_runs_negative']:4.0f}%  | vs BAU mean "
                  f"{p[mode]['vs_business_as_usual']['mean_t']:8.1f}")
    return result


if __name__ == "__main__":
    main()
