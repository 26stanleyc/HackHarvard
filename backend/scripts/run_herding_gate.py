"""M2 herding GO/NO-GO gate, exactly as pre-registered in
``docs/results/herding_gate_preregistration.md``. Writes ``docs/results/herding_gate.json``.

* Days: the 7 ``herding_gate_days`` in ``days.json`` (used for nothing else).
* Shares 0, 2.5, ..., 30 % of the day's actual peak demand; K = 5 population draws
  (``make_population(share, peak, seed=SEED_BASE + k)``, N = 30 bids); no background load.
* Planning on yesterday-as-forecast inputs, scoring on the day's actual inputs.
* Simulator params: ``--params default`` (the M2 run; ``DEFAULT_PARAMS`` explicitly, not
  ``load_params``) writes ``herding_gate.json``; ``--params calibrated`` (the M3 re-run; the M3
  ``calibrated_params.json`` via ``load_params``, which must exist) writes
  ``herding_gate_calibrated.json``. Rule, days, shares, K, seeds and populations are identical.
* Primary metric: strategy 3 (independent-marginal); secondary: strategy 2 (independent-average).
* Decision: GO if, on >= 4 of 7 days, some share <= 20 % has primary gap % >= 10 %, where the
  per-(day, share) value is the mean over the K draws of each draw's gap % (= 100 x gap /
  predicted, defined only for draws with predicted > 0; draws where it is undefined are left out
  of the mean and counted in ``n_defined``). The ratio of means (100 x mean gap / mean predicted)
  is also reported as a robustness check; it is not part of the decision.

Run: ``uv run python scripts/run_herding_gate.py [--params default|calibrated]``.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime

import numpy as np

from clearinghouse.config import REPO_ROOT, RESULTS_DIR
from clearinghouse.data import load_days, load_emission_factors
from clearinghouse.inputs import actual_inputs, yesterday_forecast_inputs
from clearinghouse.params import CALIBRATED_PARAMS_FILE, DEFAULT_PARAMS, load_params
from clearinghouse.population import DEFAULT_N, distribution_summary, make_population
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import herding_metric, validate_schedules

OUT_FILES = {"default": RESULTS_DIR / "herding_gate.json",
             "calibrated": RESULTS_DIR / "herding_gate_calibrated.json"}
OUT_FILE = OUT_FILES["default"]
SHARES = [round(2.5 * i, 1) for i in range(13)]  # 0 .. 30
K = 5
N = DEFAULT_N
SEED_BASE = 1000
GO_THRESHOLD_PCT = 10.0
MAX_SHARE_FOR_GO = 20.0
DAYS_NEEDED = 4
STRATS = {"primary": "independent_marginal", "secondary": "independent_average"}

DEFINITION = (
    "Predicted savings = sum_i [E(all default) - E(bid i runs the strategy alone, others "
    "default)]; realized = E(all default) - E(all run the strategy together); gap = predicted - "
    "realized; gap % = 100 x gap / predicted (only where predicted > 0). Every E is fossil CO2 "
    "(t) from the {which} simulator on the day's actual EIA-930 inputs; plans use "
    "yesterday-as-forecast inputs. GO if the primary (strategy 3, independent-marginal) gap % "
    "(mean over K draws of per-draw gap %) >= 10% at some share <= 20% on >= 4 of 7 days."
)


def _stats(values: list[float]) -> dict:
    a = np.asarray(values, dtype=float)
    return {"mean": float(a.mean()), "min": float(a.min()), "max": float(a.max())}


def _summarize(results: list) -> dict:
    pred = [r.predicted_t for r in results]
    real = [r.realized_t for r in results]
    gap = [r.gap_t for r in results]
    pcts = [r.gap_pct for r in results if r.gap_pct is not None]
    out = {"predicted_t": _stats(pred), "realized_t": _stats(real), "gap_t": _stats(gap),
           "together_t": _stats([r.e_together_t for r in results])}
    out["gap_pct"] = ({**_stats(pcts), "n_defined": len(pcts)} if pcts
                      else {"mean": None, "min": None, "max": None, "n_defined": 0})
    mp = float(np.mean(pred))
    out["gap_pct_ratio_of_means"] = 100.0 * float(np.mean(gap)) / mp if mp > 0 else None
    return out


def baseline_sanity(date: str, sim: DaySimulator) -> dict:
    """Informational only: simulator baseline vs EIA-930 actuals (fuel mix x eGRID factors)."""
    f = load_emission_factors()
    act = sim.inputs.actual_fossil
    act_co2 = float(sum((act[k] * f[k]).sum() for k in act))
    split = sim.baseline["fuel_split_mw"]
    act_fossil = float(sum(v.sum() for v in act.values()))
    sim_fossil = float(sim.baseline["fossil_generation_mw"].sum())
    return {
        "sim_fossil_co2_t": sim.baseline["total_co2_t"],
        "actual_fossil_co2_t": act_co2,
        "co2_ratio_sim_over_actual": sim.baseline["total_co2_t"] / act_co2,
        "sim_coal_share_of_fossil": float(split["coal"].sum()) / sim_fossil,
        "actual_coal_share_of_fossil": float(act["coal"].sum()) / act_fossil,
        "sim_coal_mwh": float(split["coal"].sum()),
        "actual_coal_mwh": float(act["coal"].sum()),
        "sim_gas_mwh": float(split["gas"].sum()),
        "actual_gas_mwh": float(act["gas"].sum()),
        "fossil_mwh": sim_fossil,
        "unserved_mwh": sim.baseline["unserved_mwh"],
        "curtailed_mwh": float(sim.inputs.curtailed_mw.sum()),
    }


def resolve_params(which: str):
    """``(params, source, out_file)`` for ``--params default|calibrated``."""
    if which == "default":
        return DEFAULT_PARAMS, "uncalibrated_default", OUT_FILES["default"]
    if which == "calibrated":
        if not CALIBRATED_PARAMS_FILE.is_file():
            raise SystemExit(f"{CALIBRATED_PARAMS_FILE} missing: run scripts/calibrate.py first")
        params, source = load_params()
        return params, source, OUT_FILES["calibrated"]
    raise ValueError(f"unknown params choice {which!r}")


def main(which: str = "default") -> dict:
    t_start = time.perf_counter()
    params, params_source, out_file = resolve_params(which)
    gate_days = load_days()["herding_gate_days"]
    days_out, sanity = {}, {}
    for date in gate_days:
        score_in, plan_in = actual_inputs(date), yesterday_forecast_inputs(date)
        plan_sim = DaySimulator(plan_in, params)
        score_sim = DaySimulator(score_in, params)
        sanity[date] = baseline_sanity(date, score_sim)
        peak = score_in.peak_demand_mw
        per_share = []
        for share in SHARES:
            row = {"share_pct": share, "baseline_no_flex_t": score_sim.baseline["total_co2_t"]}
            if share == 0:
                row.update({"primary": None, "secondary": None,
                            "totals_mean_t": {"default": score_sim.baseline["total_co2_t"],
                                              "independent_average": None,
                                              "independent_marginal": None},
                            "note": "no flexible load; savings undefined"})
                per_share.append(row)
                continue
            res = {k: [] for k in STRATS}
            for k in range(K):
                bids = make_population(share, peak, SEED_BASE + k, N)
                for key, strat in STRATS.items():
                    r = herding_metric(bids, plan_sim, score_sim, strat)
                    errs = validate_schedules(bids, r.schedules)
                    if errs:
                        raise AssertionError(f"{date} {share}% {strat}: invalid schedules {errs}")
                    res[key].append(r)
            row["primary"] = _summarize(res["primary"])
            row["secondary"] = _summarize(res["secondary"])
            row["totals_mean_t"] = {
                "default": float(np.mean([r.e_default_t for r in res["primary"]])),
                "independent_average": float(np.mean([r.e_together_t for r in res["secondary"]])),
                "independent_marginal": float(np.mean([r.e_together_t for r in res["primary"]])),
            }
            per_share.append(row)
        days_out[date] = {"peak_demand_mw": peak, "forecast_source_date": plan_in.source_date,
                          "shares": per_share}

    def first_passing(date: str, field: str) -> float | None:
        for row in days_out[date]["shares"]:
            if row["primary"] is None or row["share_pct"] > MAX_SHARE_FOR_GO:
                continue
            v = (row["primary"]["gap_pct"]["mean"] if field == "mean"
                 else row["primary"]["gap_pct_ratio_of_means"])
            if v is not None and v >= GO_THRESHOLD_PCT:
                return row["share_pct"]
        return None

    per_day = {d: first_passing(d, "mean") for d in gate_days}
    robust = {d: first_passing(d, "ratio") for d in gate_days}
    passing = sum(v is not None for v in per_day.values())
    passing_r = sum(v is not None for v in robust.values())
    decision = "GO" if passing >= DAYS_NEEDED else "NO-GO"
    runtime = time.perf_counter() - t_start
    payload = {
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "config": {
            "preregistration": "docs/results/herding_gate_preregistration.md",
            "days": gate_days, "shares_pct": SHARES, "K": K, "N": N,
            "population_seeds": [SEED_BASE + k for k in range(K)],
            "seed_note": "same K populations on every day; a share only rescales them",
            "background": "none",
            "planning_inputs": "yesterday_forecast_inputs (previous CST day, target month prices)",
            "scoring_inputs": "actual_inputs",
            "params_choice": which,
            "params_source": params_source.replace(str(REPO_ROOT) + "/", ""),
            "params": params.to_dict(),
            "population": distribution_summary(),
            "strategies": STRATS,
            "definition": DEFINITION.format(
                which="uncalibrated" if which == "default" else "calibrated (M3)"),
            "decision_aggregation": ("mean over K draws of per-draw gap %, over draws with "
                                     "predicted > 0 (n_defined); ratio of means reported only "
                                     "as a robustness check"),
        },
        "days": days_out,
        "verdict": {
            "rule": f"primary gap % >= {GO_THRESHOLD_PCT}% at some share <= {MAX_SHARE_FOR_GO}% "
                    f"on >= {DAYS_NEEDED} of {len(gate_days)} days",
            "smallest_passing_share_pct": per_day,
            "days_passing": passing,
            "decision": decision,
            "robustness_ratio_of_means": {"smallest_passing_share_pct": robust,
                                          "days_passing": passing_r,
                                          "decision": "GO" if passing_r >= DAYS_NEEDED
                                          else "NO-GO"},
        },
        "baseline_sanity_informational": sanity,
        "runtime_s": runtime,
    }
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(payload, indent=2) + "\n")
    _print_summary(payload, which, out_file)
    return payload


def _fmt(v) -> str:
    return "   n/a" if v is None else f"{v:6.1f}"


def _print_summary(p: dict, which: str = "default", out_file=OUT_FILE) -> None:
    show = [2.5, 5.0, 10.0, 15.0, 20.0, 30.0]
    label = "uncalibrated" if which == "default" else "calibrated"
    print(f"Herding gate ({label}). gap % = mean over K draws; P = strategy 3 (primary), "
          "S = strategy 2 (secondary)")
    print(f"{'day':<11} {'':2} " + " ".join(f"{s:>6}" for s in show) + "   first<=20%")
    for date, d in p["days"].items():
        rows = {r["share_pct"]: r for r in d["shares"]}
        for key, tag in (("primary", "P"), ("secondary", "S")):
            vals = [rows[s][key]["gap_pct"]["mean"] for s in show]
            first = p["verdict"]["smallest_passing_share_pct"][date] if key == "primary" else ""
            print(f"{date:<11} {tag:2} " + " ".join(_fmt(v) for v in vals) + f"   {first}")
    print("\nPredicted / realized savings (t, mean over draws), primary:")
    for date, d in p["days"].items():
        rows = {r["share_pct"]: r for r in d["shares"]}
        cells = [f"{rows[s]['primary']['predicted_t']['mean']:.0f}/"
                 f"{rows[s]['primary']['realized_t']['mean']:.0f}" for s in show]
        print(f"{date:<11} " + "  ".join(cells))
    v = p["verdict"]
    print(f"\nVerdict: {v['decision']} ({v['days_passing']}/{len(p['days'])} days pass). "
          f"Robustness (ratio of means): {v['robustness_ratio_of_means']['decision']} "
          f"({v['robustness_ratio_of_means']['days_passing']} days).")
    print("\nBaseline sanity (informational): sim vs actual fossil CO2 (kt), coal share sim/actual")
    for date, s in p["baseline_sanity_informational"].items():
        print(f"{date}  {s['sim_fossil_co2_t'] / 1e3:7.1f} vs {s['actual_fossil_co2_t'] / 1e3:7.1f}"
              f"  ratio {s['co2_ratio_sim_over_actual']:.2f}"
              f"   coal {s['sim_coal_share_of_fossil']:.0%}"
              f" vs {s['actual_coal_share_of_fossil']:.0%}   unserved {s['unserved_mwh']:.0f} MWh")
    print(f"\nruntime {p['runtime_s']:.1f} s -> {out_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--params", choices=sorted(OUT_FILES), default="default",
                    help="simulator parameters (default: the M2 uncalibrated defaults)")
    main(ap.parse_args().params)
