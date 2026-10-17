"""Precompute the adoption chart (design B4; M6 pinned decision 6) for GET /api/adoption-chart.

Writes ``backend/data/processed/adoption_chart.json`` (the ``AdoptionChartResponse``, committed)
and ``backend/data/processed/adoption_chart_meta.json`` (sidecar: day, definitions, runtime; the
response model forbids extra keys, so these cannot go in the response).

Rule (fixed before any result was seen):

* Day: the API's ``default_day`` (first pre-registered demo day in days.json order).
* x = 0, 2.5, ..., 30 % of the day's ACTUAL peak demand (the M2 pre-registered share definition:
  ``sum(max_mw) = share x actual peak``).
* K = 5 population draws, ``make_population(share, peak, seed=1000 + k)``, N = 30 bids; no
  background population.
* Strategies: default, independent_average, independent_marginal and coordinated = greedy over all
  population bids with the planning-time fallback to strategy 3 (exactly the clearing engine's
  full-coalition plan, ``settlement.CoalitionGame``).
* Planned on the forecast provider's planning simulator (``forecast_inputs``), scored on the day's
  actual inputs, calibrated params (``load_params``).
* ``mean_co2_t`` = mean over the K draws of the day's total fossil CO2 (t); ``band_low`` /
  ``band_high`` = min / max over the K draws. At x = 0 every value is the no-flex baseline.

Run from ``backend/``: ``uv run python scripts/build_adoption_chart.py``.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

import numpy as np

from clearinghouse.api import REGION_CODE, default_day, demo_days
from clearinghouse.config import DATA_PROCESSED
from clearinghouse.forecast import get_provider
from clearinghouse.inputs import HOURS, actual_inputs, forecast_inputs
from clearinghouse.params import load_params
from clearinghouse.population import DEFAULT_N, distribution_summary, make_population
from clearinghouse.schemas import STRATEGY_NAMES, AdoptionChartResponse
from clearinghouse.settlement import CoalitionGame, canon_sum
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import (
    default_schedules,
    independent_average,
    independent_marginal,
    validate_schedules,
)

OUT_FILE = DATA_PROCESSED / "adoption_chart.json"
META_FILE = DATA_PROCESSED / "adoption_chart_meta.json"
SHARES = [round(2.5 * i, 1) for i in range(13)]  # 0 .. 30
K = 5
N = DEFAULT_N
SEED_BASE = 1000


def draw_totals(bids, plan_sim: DaySimulator, score_sim: DaySimulator) -> tuple[dict, bool]:
    """Scored total CO2 per strategy for one population draw, plus whether the fallback fired."""
    ids = [b.id for b in bids]
    zero = np.zeros(HOURS)
    s1 = default_schedules(bids)
    s2 = independent_average(bids, plan_sim, None)
    s3 = independent_marginal(bids, plan_sim, None)
    game = CoalitionGame(bids, s3, [zero], plan_sim, score_sim)
    full = game.plan(game.full)
    s4 = {i: full.schedules[i] for i in ids}
    scheds = {"default": s1, "independent_average": s2, "independent_marginal": s3,
              "coordinated": s4}
    for name, s in scheds.items():
        bad = validate_schedules(bids, s)
        if bad:
            raise AssertionError(f"{name}: invalid schedules {bad}")
    rows = np.vstack([canon_sum([zero] + [scheds[k][i] for i in ids]) for k in STRATEGY_NAMES])
    e = score_sim.emissions_batch(rows)
    return {k: float(e[j]) for j, k in enumerate(STRATEGY_NAMES)}, bool(full.fallback_fired)


def main() -> dict:
    t0 = time.perf_counter()
    day = default_day()
    params, params_source = load_params()
    provider = get_provider()
    fc = provider.get_forecast(REGION_CODE, day)
    plan_sim = DaySimulator(forecast_inputs(fc), params)
    score_sim = DaySimulator(actual_inputs(day), params)
    peak = score_sim.inputs.peak_demand_mw
    base = score_sim.baseline["total_co2_t"]

    series = {k: {"mean_co2_t": [], "band_low": [], "band_high": []} for k in STRATEGY_NAMES}
    fallback = []
    for share in SHARES:
        if share == 0:
            per = {k: [base] for k in STRATEGY_NAMES}
            fallback.append(0)
        else:
            per = {k: [] for k in STRATEGY_NAMES}
            fired = 0
            for k in range(K):
                bids = make_population(share, peak, SEED_BASE + k, N)
                totals, f = draw_totals(bids, plan_sim, score_sim)
                fired += int(f)
                for name in STRATEGY_NAMES:
                    per[name].append(totals[name])
            fallback.append(fired)
        for name in STRATEGY_NAMES:
            a = np.asarray(per[name], dtype=float)
            series[name]["mean_co2_t"].append(float(a.mean()))
            series[name]["band_low"].append(float(a.min()))
            series[name]["band_high"].append(float(a.max()))

    chart = {"x_pct_of_peak": [float(x) for x in SHARES], "strategies": series}
    AdoptionChartResponse.model_validate(chart)
    runtime = time.perf_counter() - t0
    label = next(d["label"] for d in demo_days() if d["date"] == day)
    meta = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "day": day,
        "day_label": label,
        "x_definition": "flexible load as % of the day's ACTUAL peak demand: sum of the "
                        "population's max_mw = x% x peak (M2 pre-registered share definition)",
        "actual_peak_mw": peak,
        "baseline_no_flex_co2_t": base,
        "k_draws": K,
        "n_bids": N,
        "seeds": [SEED_BASE + k for k in range(K)],
        "background": "none",
        "planning": f"forecast provider ({fc.source}) inputs via inputs.forecast_inputs",
        "scoring": "the day's actual EIA-930 + curtailment inputs",
        "params_source": params_source.split(":", 1)[0],
        "coordinated": "greedy over all population bids with the planning-time fallback to "
                       "strategy 3 (settlement.CoalitionGame full-coalition plan)",
        "mean": "mean over the K draws of total daily fossil CO2 (t)",
        "band": "band_low / band_high = min / max over the K draws",
        "fallback_fired_draws_per_x": fallback,
        "population": distribution_summary(),
        "runtime_s": runtime,
    }
    OUT_FILE.write_text(json.dumps(chart, indent=2) + "\n")
    META_FILE.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"day {day} ({label}); forecast source {fc.source}; params {meta['params_source']}")
    print(f"baseline {base:,.0f} t; runtime {runtime:.1f} s; fallback per x {fallback}")
    print("x%    " + "  ".join(f"{k:>22}" for k in STRATEGY_NAMES))
    for j, x in enumerate(SHARES):
        print(f"{x:4.1f}  " + "  ".join(f"{series[k]['mean_co2_t'][j]:22,.0f}"
                                        for k in STRATEGY_NAMES))
    return {"chart": chart, "meta": meta}


if __name__ == "__main__":
    main()
