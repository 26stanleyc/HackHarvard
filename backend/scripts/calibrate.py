"""M3 calibration and validation (design B1, M3). See ``clearinghouse/calibration.py`` for the
pre-registered fit, objective, attempts and selection rule.

* Fits availability (coal, gas) and per-month coal price scale on ``calibration_train_days`` only.
* Validates (a) coal/gas split, (b) marginal emission factor, (c) hourly fossil CO2 on
  ``calibration_holdout_days`` (never used in fitting), for the uncalibrated defaults and the
  calibrated params; the 4 demo days are reported for information.
* Writes ``backend/data/processed/calibrated_params.json`` (read by ``params.load_params()``) and
  ``docs/results/calibration.json``.

Run: ``uv run python scripts/calibrate.py``.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import UTC, datetime

import numpy as np

from clearinghouse.calibration import (
    ATTEMPT_1_REJECTION,
    ATTEMPT_1_UNCONSTRAINED,
    ATTEMPTS,
    AVAIL_BOUNDS,
    COAL_SCALE_BOUNDS,
    DEFINITIONS,
    FOSSIL_CO2_TARGET,
    OIL_NOTE,
    FitConfig,
    fit,
    headline,
    load_day_data,
    validate,
)
from clearinghouse.config import REPO_ROOT, RESULTS_DIR
from clearinghouse.data import load_days
from clearinghouse.inputs import actual_inputs
from clearinghouse.params import CALIBRATED_PARAMS_FILE, DEFAULT_PARAMS, load_params
from clearinghouse.simulator import DaySimulator

OUT_FILE = RESULTS_DIR / "calibration.json"


def _short(v: dict) -> dict:
    a, b, c = v["a_coal_gas_split"], v["b_marginal_emission_factor"], v["c_fossil_co2"]
    return {
        **headline(v),
        "coal_share_mae_pp": a["coal_share_mae_pp"],
        "coal_mw_mape": a["coal_mw_mape"],
        "gas_mw_mape": a["gas_mw_mape"],
        "mean_marginal_rate": b["mean_marginal_rate"],
        "mean_fossil_margin_rate": b["mean_fossil_margin_rate"],
        "fossil_margin_share_of_hours": b["fossil_margin_share_of_hours"],
        "daily_co2_mape": c["daily_co2_mape"],
        "daily_co2_bias": c["daily_co2_bias"],
        "total_co2_bias": c["total_co2_bias"],
        "objective": v["objective"],
    }


def main() -> dict:
    t0 = time.perf_counter()
    days = load_days()
    train = days["calibration_train_days"]
    holdout = days["calibration_holdout_days"]
    demo = {d["date"]: d["label"] for d in days["demo_days"]}
    assert not set(train) & set(holdout), "train/holdout overlap"
    assert not set(train) & set(days["never_trained_days"]), "train includes never-trained days"

    train_data = load_day_data(train)
    holdout_data = load_day_data(holdout)
    demo_data = load_day_data(list(demo))

    before_holdout = validate(DEFAULT_PARAMS, days=holdout_data)
    before_train = validate(DEFAULT_PARAMS, days=train_data)

    def run_attempt(k, cfg: FitConfig) -> dict:
        ta = time.perf_counter()
        res = fit(train, cfg, days=train_data)
        hv = validate(res.params, days=holdout_data)
        met = hv["c_fossil_co2"]["hourly_mape"] <= FOSSIL_CO2_TARGET
        rec = {
            "attempt": k,
            "name": cfg.name,
            "description": cfg.description,
            "config": asdict(cfg),
            "feasibility_constraints": res.feasibility,
            "train_objective": res.train_objective,
            "train_co2_term": res.train_co2_term,
            "train_coal_share_term": res.train_share_term,
            "iterations": res.iterations,
            "params": res.params.to_dict(),
            "coal_scale_plateaus": {str(m): v for m, v in res.scale_plateaus.items()},
            "holdout": _short(hv),
            "holdout_unserved_hours_frac": hv["unserved_hours_frac"],
            "holdout_unserved_mwh": hv["unserved_mwh"],
            "train_unserved_mwh": validate(res.params, days=train_data)["unserved_mwh"],
            "holdout_target_met": met,
            "runtime_s": time.perf_counter() - ta,
            "_result": res,
        }
        print(f"attempt {k} ({cfg.name}): train objective {res.train_objective:.5f}, holdout "
              f"fossil CO2 MAPE {hv['c_fossil_co2']['hourly_mape']:.4f} "
              f"({'met' if met else 'missed'}), holdout unserved {hv['unserved_mwh']:.0f} MWh "
              f"[{rec['runtime_s']:.1f} s]")
        return rec

    # attempt 1 (unconstrained): kept for the record, rejected for physical infeasibility
    first = run_attempt("1", ATTEMPT_1_UNCONSTRAINED)
    first.pop("_result")
    first.update({"status": "rejected", "note": ATTEMPT_1_REJECTION})
    attempts, constrained, selected = [first], [], None
    # 1b is adopted regardless of the target; 2-5 (same constraints) only if 1b misses it
    for k, cfg in zip(["1b", "2", "3", "4", "5"], ATTEMPTS, strict=True):
        rec = run_attempt(k, cfg)
        attempts.append(rec)
        constrained.append(rec)
        if rec["holdout_target_met"]:
            selected = rec
            break
        if k == "1b":
            print("  1b missed the target: running attempts 2-5 under the same constraints")
    if selected is None:  # fallback among constrained attempts: lowest TRAINING objective
        selected = min(constrained, key=lambda a: a["train_objective"])
    res = selected["_result"]
    for rec in attempts:
        rec.pop("_result", None)
        rec.setdefault("status", "adopted" if rec is selected else "not adopted")

    params = res.params
    params = type(params).from_dict({**params.to_dict(), "label": "calibrated_m3"})
    after_holdout = validate(params, days=holdout_data)
    after_train = validate(params, days=train_data)
    demo_out = {}
    for d in demo_data:
        demo_out[d.date] = {
            "label": demo[d.date],
            "default": _short(validate(DEFAULT_PARAMS, days=[d])),
            "calibrated": _short(validate(params, days=[d])),
        }
    demo_all = {"default": validate(DEFAULT_PARAMS, days=demo_data),
                "calibrated": validate(params, days=demo_data)}

    def unserved(day_list: list[str]) -> dict:
        """Hours / MWh where the fossil requirement exceeds the adopted stack (expect 0)."""
        hours = mwh = 0.0
        for d in day_list:
            sim = DaySimulator(actual_inputs(d), params)
            short = np.maximum(0.0, sim.fossil_requirement(None) - sim.stack.total_mw)
            hours += float((short > 0).sum())
            mwh += float(short.sum())
        return {"days": len(day_list), "hours": int(hours), "mwh": mwh,
                "hours_frac": hours / (24 * len(day_list))}

    adopted_unserved = {"train": unserved(train), "holdout": unserved(holdout),
                        "demo": unserved(list(demo)), "gate": unserved(days["herding_gate_days"])}

    generated = datetime.now(UTC).isoformat(timespec="seconds")
    CALIBRATED_PARAMS_FILE.write_text(json.dumps({
        "params": params.to_dict(),
        "fitted_on": "calibration_train_days (backend/data/processed/days.json)",
        "train_days_n": len(train),
        "attempt": selected["attempt"],
        "attempt_name": selected["name"],
        "feasibility_constraints": res.feasibility,
        "train_objective": res.train_objective,
        "holdout_fossil_co2_error": after_holdout["c_fossil_co2"]["hourly_mape"],
        "generated_utc": generated,
        "note": OIL_NOTE,
    }, indent=2) + "\n")
    loaded, source = load_params()
    assert loaded == params, "calibrated params do not round-trip through load_params()"

    runtime = time.perf_counter() - t0
    hl = headline(after_holdout)
    payload = {
        "generated_utc": generated,
        **hl,
        "target": {"metric": "fossil_co2_error (hourly fossil CO2 MAPE, holdout)",
                   "threshold": FOSSIL_CO2_TARGET,
                   "met": hl["fossil_co2_error"] <= FOSSIL_CO2_TARGET},
        "params": params.to_dict(),
        "params_source": source.replace(str(REPO_ROOT) + "/", ""),
        "fitted_parameters": {
            "availability": ["coal", "gas"],
            "monthly_price_scale[month]['coal']": "months 1-12 (coal price relative to gas; "
                                                 "gas fixed at 1.0)",
            "n_fitted": 2 + len(params.monthly_price_scale),
            "bounds": {"availability": list(AVAIL_BOUNDS),
                       "coal_scale": list(COAL_SCALE_BOUNDS)},
            "not_fitted": {"availability['oil']": OIL_NOTE,
                           "price_scale": "1.0 for every fuel"},
            "coal_scale_plateaus": {str(m): v for m, v in res.scale_plateaus.items()},
            "plateau_note": "Each month's objective is piecewise constant in the coal scale; "
                            "any value in the plateau gives the same merit order and output. "
                            "The reported value is the plateau's geometric midpoint.",
        },
        "selected_attempt": selected["attempt"],
        "selection_rule": "attempt 1 (unconstrained) rejected for physical infeasibility "
                          "(orchestrator decision). Attempt 1b (feasibility-constrained) is "
                          "adopted whether or not it meets the target; only if it misses do "
                          "attempts 2-5 (same constraints) run: first meeting the target, else "
                          "lowest training objective among constrained attempts.",
        "feasibility_constraints": res.feasibility,
        "adopted_unserved": adopted_unserved,
        "attempts": attempts,
        "train_days_n": len(train),
        "holdout_days_n": len(holdout),
        "holdout": {"before_default": before_holdout, "after_calibrated": after_holdout},
        "train_in_sample": {"before_default": before_train, "after_calibrated": after_train},
        "demo_days_informational": {"per_day": demo_out, "all_four": demo_all,
                                    "note": "never trained on"},
        "definitions": DEFINITIONS,
        "data_note": "Validation (b) uses EIA-930 fuel mix x eGRID fuel emission factors (no "
                     "CAMPD; DATA_GOV_API_KEY placeholder).",
        "runtime_s": runtime,
    }
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(payload, indent=2) + "\n")
    _print_summary(payload, before_holdout, after_holdout, demo_out)
    return payload


def _row(name: str, b: dict, a: dict, key: str, fmt: str = "{:.4f}") -> str:
    return f"{name:<34} {fmt.format(b[key]):>10} {fmt.format(a[key]):>10}"


def _print_summary(p: dict, before: dict, after: dict, demo_out: dict) -> None:
    sb, sa = _short(before), _short(after)
    print(f"\nCalibrated params (attempt {p['selected_attempt']}): availability "
          f"{p['params']['availability']}")
    print("  coal scale by month: " + ", ".join(
        f"{m}:{v['coal']:.3f}" for m, v in p["params"]["monthly_price_scale"].items()))
    print(f"\nHoldout ({p['holdout_days_n']} days)            {'default':>10} {'calibrated':>10}")
    for name, key in (("(a) coal share MAE (fraction)", "coal_gas_split_error"),
                      ("(a) coal MW MAPE", "coal_mw_mape"), ("(a) gas MW MAPE", "gas_mw_mape"),
                      ("(b) MEF simulated (t/MWh)", "mef_simulated"),
                      ("(b) MEF observed (t/MWh)", "mef_observed"),
                      ("(b) mean marginal rate (t/MWh)", "mean_marginal_rate"),
                      ("(b) mean fossil-margin rate", "mean_fossil_margin_rate"),
                      ("(c) hourly fossil CO2 MAPE", "fossil_co2_error"),
                      ("(c) daily CO2 MAPE", "daily_co2_mape"),
                      ("(c) daily CO2 bias", "daily_co2_bias"),
                      ("objective", "objective")):
        print(_row(name, sb, sa, key))
    for tag, v in (("default", before), ("calibrated", after)):
        b = v["b_marginal_emission_factor"]
        print(f"  {tag}: marginal resource "
              + ", ".join(f"{k} {x:.1%}" for k, x in b["marginal_resource_share_of_hours"].items())
              + " | fossil margin after curtailment "
              + ", ".join(f"{k} {x:.1%}" for k, x in b["fossil_margin_share_of_hours"].items()))
    print(f"  hours with modeled curtailment > 0: "
          f"{after['b_marginal_emission_factor']['hours_with_curtailment_frac']:.1%} (min "
          f"{after['b_marginal_emission_factor']['curtailed_mw_min']:.0f} MW, median "
          f"{after['b_marginal_emission_factor']['curtailed_mw_median']:.0f} MW)")
    print("\nDemo days (informational): hourly fossil CO2 MAPE / coal share MAE, default -> "
          "calibrated")
    for d, v in demo_out.items():
        print(f"  {d} {v['label']:<20} {v['default']['fossil_co2_error']:.3f} / "
              f"{v['default']['coal_gas_split_error']:.3f} -> "
              f"{v['calibrated']['fossil_co2_error']:.3f} / "
              f"{v['calibrated']['coal_gas_split_error']:.3f}")
    print("\nUnserved energy with adopted params: " + ", ".join(
        f"{k} {v['hours']} h / {v['mwh']:.0f} MWh" for k, v in p["adopted_unserved"].items()))
    t = p["target"]
    print(f"\nTarget fossil_co2_error <= {t['threshold']}: "
          f"{'MET' if t['met'] else 'MISSED'} ({p['fossil_co2_error']:.4f})")
    print(f"runtime {p['runtime_s']:.1f} s -> {OUT_FILE}, {CALIBRATED_PARAMS_FILE}")


if __name__ == "__main__":
    main()
