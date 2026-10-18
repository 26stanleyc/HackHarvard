"""M13 synthetic grid-day check (rule pre-registered in docs/design.md, M13).

For each dev day D of 2024-10-01..12-31 (even date index): plan on the real forecast(D) with the
configured planning mode / coordinator (env PLANNING_MODE, COORDINATOR, IMPROVE_Q, ROBUST_DAYS),
and score on M synthetic "actual" days = forecast(D) + the full 24-hour forecast error of another
Q4 day d with |d - D| > 21 days (never a planning scenario), demand/wind/solar/curtailment clipped
at 0. K company draws (N = 30, 20 % of the real day's peak), fixed seeds.
Metric: % of runs where coordinated emits strictly less than independent marginal.

Run from ``backend/``: ``SYNTH_TAG=<name> uv run python scripts/synthetic_check.py``.
Writes ``docs/results/m13_synthetic_<tag>.json``.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
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
from clearinghouse.population import make_population  # noqa: E402
from clearinghouse.robust import build_planning_sim, planning_mode, shifted_inputs  # noqa: E402
from clearinghouse.simulator import DaySimulator  # noqa: E402

DAYS = tuple(pd.date_range("2024-10-01", "2024-12-31").strftime("%Y-%m-%d"))
DEV = DAYS[::2]
SHARE, N, K, M = 20.0, 30, 4, 5
MIN_GAP_DAYS = 21
SEED = 13000


def main() -> dict:
    tag = os.environ.get("SYNTH_TAG", "run")
    params, _ = load_params()
    provider = get_provider()
    mode = planning_mode()
    fc_cache: dict[str, object] = {}

    def fc_inputs(d: str):
        if d not in fc_cache:
            fc_cache[d] = forecast_inputs(provider.get_forecast(REGION_CODE, d))
        return fc_cache[d]

    rng = np.random.default_rng(SEED)
    rows = []
    for di, day in enumerate(DEV):
        fc = fc_inputs(day)
        plan = build_planning_sim(day, fc, params, mode, forecast_for=fc_inputs,
                                  actual_for=actual_inputs)
        pool = [d for d in DAYS
                if abs((date.fromisoformat(d) - date.fromisoformat(day)).days) > MIN_GAP_DAYS]
        donors = [str(x) for x in rng.choice(pool, size=M, replace=False)]
        peak = actual_inputs(day).peak_demand_mw
        for m, d in enumerate(donors):
            synth = shifted_inputs(fc, actual_inputs(d), fc_inputs(d))
            score = DaySimulator(synth, params)
            for k in range(K):
                bids = make_population(SHARE, peak, SEED + 1000 * di + 10 * m + k, N)
                totals, fired = draw_totals(bids, plan, score)
                rows.append({"day": day, "donor": str(d), "k": k, "fallback": fired, **totals})
    df = pd.DataFrame(rows)
    sv = df["independent_marginal"] - df["coordinated"]
    out = {"tag": tag, "planning_mode": mode,
           "coordinator": os.environ.get("COORDINATOR", "greedy"),
           "improve_q": os.environ.get("IMPROVE_Q"), "robust_days": os.environ.get("ROBUST_DAYS"),
           "runs": int(len(df)),
           "win_pct": float(100 * (sv > 0).mean()), "loss_pct": float(100 * (sv < 0).mean()),
           "mean_saved_vs_self_t": float(sv.mean()),
           "mean_saved_vs_bau_t": float((df["default"] - df["coordinated"]).mean())}
    (RESULTS_DIR / f"m13_synthetic_{tag}.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out))
    return out


if __name__ == "__main__":
    main()
