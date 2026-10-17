"""CO2 saved by coordination vs two self-optimizing baselines, over many real days and synthetic
company populations (pitch numbers). Writes ``docs/results/scenario_savings_q4_2024.json``
(summary) and ``scenario_savings_q4_2024_runs.csv`` (one row per day x draw x share).

Rule (fixed before any result was seen):

* Days: every CST day 2024-10-01..2024-12-31 (92 days, the forecast's test period: the M10 models
  never saw them).
* Companies: ``make_population(share, actual peak, seed, N=30)`` (the stated synthetic
  distributions in ``population.py``); K = 10 draws per day with fresh seeds
  ``SEED_BASE + 100 x day_index + k``; shares 5, 10 and 20 % of the day's actual peak (the same
  draw rescaled). Headline share: 10 %. No background population.
* Strategies, exactly as the adoption chart (``build_adoption_chart.draw_totals``): default
  (business as usual), independent_average (baseline 1: every company picks the cleanest hours
  of the public average-intensity forecast), independent_marginal (baseline 2: every company
  self-optimizes its own marginal CO2), coordinated (greedy over all companies with the
  planning-time fallback to baseline 2).
* Planned on the forecast provider's inputs (Chalk when configured, else the local M10 model;
  the two give identical forecasts), scored on the day's actual inputs, calibrated params.
* Saved vs a baseline = E(baseline) - E(coordinated), fossil CO2 (t) of the whole day; positive =
  coordination emits less. Reported per share: mean, median, p10, p90 over all day x draw runs,
  the share of runs with saved > 0 (and < 0), and saved per MWh of flexible energy.

Run from ``backend/``: ``uv run python scripts/scenario_savings.py``.
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_adoption_chart import draw_totals  # noqa: E402

from clearinghouse.api import REGION_CODE  # noqa: E402
from clearinghouse.config import RESULTS_DIR  # noqa: E402
from clearinghouse.forecast import get_provider  # noqa: E402
from clearinghouse.inputs import actual_inputs, forecast_inputs  # noqa: E402
from clearinghouse.params import load_params  # noqa: E402
from clearinghouse.population import DEFAULT_N, distribution_summary, make_population  # noqa: E402
from clearinghouse.simulator import DaySimulator  # noqa: E402

OUT_FILE = RESULTS_DIR / "scenario_savings_q4_2024.json"
RUNS_FILE = RESULTS_DIR / "scenario_savings_q4_2024_runs.csv"
DAYS = tuple(pd.date_range("2024-10-01", "2024-12-31").strftime("%Y-%m-%d"))
SHARES = (5.0, 10.0, 20.0)
HEADLINE_SHARE = 10.0
K = 10
N = DEFAULT_N
SEED_BASE = 7000
BASELINES = {"vs_cleanest_slot": "independent_average",
             "vs_self_optimizing": "independent_marginal",
             "vs_business_as_usual": "default"}
T_PER_CAR_YEAR = 4.6  # EPA: typical passenger vehicle, metric t CO2 per year


def _stats(v: np.ndarray) -> dict:
    return {"mean_t": float(v.mean()), "median_t": float(np.median(v)),
            "p10_t": float(np.percentile(v, 10)), "p90_t": float(np.percentile(v, 90)),
            "pct_runs_positive": float(100 * (v > 0).mean()),
            "pct_runs_negative": float(100 * (v < 0).mean())}


def main() -> dict:
    t0 = time.perf_counter()
    params, params_source = load_params()
    provider = get_provider()
    runs, sources = [], Counter()
    for di, day in enumerate(DAYS):
        fc = provider.get_forecast(REGION_CODE, day)
        sources[fc.source] += 1
        plan_sim = DaySimulator(forecast_inputs(fc), params)
        score_sim = DaySimulator(actual_inputs(day), params)
        peak = score_sim.inputs.peak_demand_mw
        no_flex = score_sim.baseline["total_co2_t"]
        for k in range(K):
            seed = SEED_BASE + 100 * di + k
            for share in SHARES:
                bids = make_population(share, peak, seed, N)
                totals, fired = draw_totals(bids, plan_sim, score_sim)
                runs.append({"day": day, "share_pct": share, "seed": seed,
                             "flex_mwh": float(sum(b.energy_mwh for b in bids)),
                             "no_flex_t": no_flex, "fallback_fired": fired, **totals})

    summary = {}
    for share in SHARES:
        rs = [r for r in runs if r["share_pct"] == share]
        coord = np.array([r["coordinated"] for r in rs])
        flex = np.array([r["flex_mwh"] for r in rs])
        row = {"runs": len(rs), "mean_flex_mwh_per_day": float(flex.mean()),
               "fallback_fired_pct": float(100 * np.mean([r["fallback_fired"] for r in rs]))}
        for name, base in BASELINES.items():
            saved = np.array([r[base] for r in rs]) - coord
            row[name] = {**_stats(saved), "t_per_mwh_flex": float(saved.sum() / flex.sum()),
                         "q4_total_t_of_mean_day": float(saved.mean() * len(DAYS)),
                         "car_years_per_mean_day": float(saved.mean() / T_PER_CAR_YEAR)}
        summary[f"{share:g}"] = row

    out = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "rule": __doc__.split("Rule (fixed before any result was seen):", 1)[1]
                .split("Run from", 1)[0].strip(),
        "days": [DAYS[0], DAYS[-1], len(DAYS)], "shares_pct": list(SHARES),
        "headline_share_pct": HEADLINE_SHARE, "k_draws": K, "n_bids": N,
        "seed_rule": f"{SEED_BASE} + 100 x day_index + k",
        "forecast_sources": dict(sources), "params_source": params_source.split(":", 1)[0],
        "baselines": BASELINES, "t_per_car_year": T_PER_CAR_YEAR,
        "population": distribution_summary(),
        "summary_by_share_pct": summary,
        "runtime_s": time.perf_counter() - t0,
        "runs_file": RUNS_FILE.name,
    }
    OUT_FILE.write_text(json.dumps(out, indent=1) + "\n")
    pd.DataFrame(runs).round(1).to_csv(RUNS_FILE, index=False)
    print(f"{len(DAYS)} days x {K} draws x {len(SHARES)} shares; forecast sources "
          f"{dict(sources)}; runtime {out['runtime_s']:.0f} s -> {OUT_FILE}")
    for share, row in summary.items():
        print(f"\nshare {share}% of peak: mean flexible energy {row['mean_flex_mwh_per_day']:,.0f} "
              f"MWh/day, fallback {row['fallback_fired_pct']:.0f}% of runs")
        for name in BASELINES:
            s = row[name]
            print(f"  {name:<21} mean {s['mean_t']:8.1f} t/day  median {s['median_t']:8.1f}  "
                  f"p10..p90 {s['p10_t']:8.1f}..{s['p90_t']:8.1f}  saved>0 in "
                  f"{s['pct_runs_positive']:5.1f}%  <0 in {s['pct_runs_negative']:5.1f}%  "
                  f"{s['t_per_mwh_flex']:.4f} t/MWh")
    return out


if __name__ == "__main__":
    main()
