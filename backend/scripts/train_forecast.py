"""M4 + M10: train the day-ahead forecast models, save artifacts, write test metrics.

    uv run python scripts/train_forecast.py

* Forecast timing (adopted): issued at as_of = 00:00 CST on day D for the 24 fixed-CST slots of
  day D (design B2 "next-24h"; ``clearinghouse.forecast.features``). Features use only slots
  that ended by as_of, i.e. through D-1 23:00-24:00.
* Split (pre-registered in M1, ``days.json`` ``forecast_split``): train on target days before
  2024-10-01 CST (from the first day with 14 days of lag history, 2023-12-16), test on
  2024-10-01..2024-12-31 CST.
* Baselines on the test period:
  - ``yesterday``: previous CST day's actual same-hour value (the pre-registered M2/M4
    "yesterday-as-forecast"). At the adopted as_of it uses exactly the information the model has.
  - ``persistence_2d``: same hour two days earlier (for context).
  - ``ercot_day_ahead_reference`` (demand only): the BA's own day-ahead demand forecast as
    reported in EIA-930. Its issue time is not documented in the data, so it is reference only.
* Rejected setting, kept on record (``rejected_issue_time_d-1_10:00``): the same model specs
  with as_of = 10:00 CST on D-1. Rejected because the yesterday baseline then uses D-1 data after
  the issue time (inconsistent comparison). The M4 check is judged on the adopted setting only.

M10 (design M10; pre-registered there): the same split and baselines. Per target, candidates
{M4, M10-A (M4 spec + forecast inputs), M10-B (A with ERCOT's forecast as base; wind and solar)}
are compared by blocked monthly CV inside the training period only, and the lowest CV MAE is
chosen. Test metrics are then reported for every candidate (the chosen one is flagged; the test
period chooses nothing). Adoption rule: the M10 set becomes the default artifacts iff its test
wind MAE is below M4's; otherwise the M4 models are saved as the default.

Outputs: ``backend/data/processed/forecast/{demand,wind,solar}.joblib`` + ``feature_spec.json``
(the default model set); ``docs/results/forecast_metrics.json``. Deterministic (fixed seeds;
sklearn HGB is deterministic for fixed data and random_state).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clearinghouse.config import RESULTS_DIR  # noqa: E402
from clearinghouse.data import load_days, load_eia930  # noqa: E402
from clearinghouse.data.forecast_inputs import load_forecast_inputs  # noqa: E402
from clearinghouse.forecast import features as F  # noqa: E402
from clearinghouse.forecast import model as M  # noqa: E402

METRICS_FILE = RESULTS_DIR / "forecast_metrics.json"
CV_BUFFER_DAYS = 14
DAYLIGHT_FRACTION = 0.05  # solar "daylight" hours: actual > 5% of the test-period peak


def metrics(y: np.ndarray, p: np.ndarray, target: str, daylight_mw: float | None) -> dict:
    err = p - y
    out = {
        "mae_mw": float(np.mean(np.abs(err))),
        "rmse_mw": float(np.sqrt(np.mean(err**2))),
        "nmae": float(np.mean(np.abs(err)) / np.mean(y)),  # MAE / mean actual
        "bias_mw": float(np.mean(err)),
    }
    if target == "solar":
        m = y > daylight_mw
        out["mape_daylight_pct"] = float(np.mean(np.abs(err[m]) / y[m]) * 100)
        out["daylight_hours"] = int(m.sum())
    else:
        out["mape_pct"] = float(np.mean(np.abs(err) / np.abs(y)) * 100)
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in out.items()}


def shifted(grid: F.Grid, col: str, days: np.ndarray, back_days: int) -> np.ndarray:
    idx = np.repeat(days, F.HOURS) * F.HOURS + np.tile(np.arange(F.HOURS), len(days))
    return grid.series[col][idx - back_days * F.HOURS]


def blocked_monthly_cv(grid: F.Grid, spec: M.ModelSpec, days: np.ndarray,
                       as_of_hour: int) -> np.ndarray:
    """Out-of-fold predictions: hold out each calendar month, drop a 14-day buffer each side."""
    X, _ = F.build_features(grid, spec.target, days, as_of_hour)
    y = F.target_values(grid, spec.target, days)
    rowd = X["day"].to_numpy()
    months = np.array([grid.dates[d][:7] for d in rowd])
    pred = np.full(len(y), np.nan)
    for m in np.unique(months):
        te = months == m
        lo, hi = rowd[te].min(), rowd[te].max()
        tr = (rowd < lo - CV_BUFFER_DAYS) | (rowd > hi + CV_BUFFER_DAYS)
        model = M.fit(spec, X[tr], y[tr])
        pred[te] = M.predict(model, spec, X[te])
    return pred


def blocked_monthly_cv_mae(grid, spec, days, as_of_hour, daylight):
    oof = blocked_monthly_cv(grid, spec, days, as_of_hour)
    y = F.target_values(grid, spec.target, days)
    return metrics(y, oof, spec.target, daylight), oof


def run(as_of_hour: int, grid: F.Grid, train_days: np.ndarray, test_days: np.ndarray,
        with_cv: bool) -> tuple[dict, dict]:
    models, results = {}, {}
    for target in F.TARGETS:
        spec = M.SPECS[target]
        col = F.SERIES_COL[target]
        Xtr, Str = F.build_features(grid, target, train_days, as_of_hour)
        Xte, Ste = F.build_features(grid, target, test_days, as_of_hour)
        for X, S in ((Xtr, Str), (Xte, Ste)):
            bad = F.leakage_violations(X, S, as_of_hour)
            if bad:
                raise AssertionError(f"leakage in {target}: {bad[:3]}")
        ytr = F.target_values(grid, target, train_days)
        yte = F.target_values(grid, target, test_days)
        model = M.fit(spec, Xtr, ytr)
        p = M.predict(model, spec, Xte)
        models[target] = model
        daylight = DAYLIGHT_FRACTION * float(yte.max()) if target == "solar" else None
        r = {
            "model": metrics(yte, p, target, daylight),
            "yesterday": metrics(yte, shifted(grid, col, test_days, 1), target, daylight),
            "persistence_2d": metrics(yte, shifted(grid, col, test_days, 2), target, daylight),
        }
        if target == "demand":
            ercot = shifted(grid, "demand_forecast_mw", test_days, 0)
            r["ercot_day_ahead_reference"] = metrics(yte, ercot, target, daylight)
            h = Xte["hour_slot"].to_numpy()
            yest = shifted(grid, col, test_days, 1)
            r["by_hour_group_mape_pct"] = {
                grp: {"model": round(float(np.mean(np.abs(p[m] - yte[m]) / yte[m]) * 100), 4),
                      "yesterday": round(float(np.mean(np.abs(yest[m] - yte[m]) / yte[m]) * 100),
                                         4)}
                for grp, m in (("hours_0_9", h < 10), ("hours_10_23", h >= 10))}
        if daylight is not None:
            r["daylight_threshold_mw"] = round(daylight, 1)
        if with_cv:
            oof = blocked_monthly_cv(grid, spec, train_days, as_of_hour)
            ytr_all = F.target_values(grid, target, train_days)
            dl = DAYLIGHT_FRACTION * float(ytr_all.max()) if target == "solar" else None
            r["train_period_cv"] = {
                "model": metrics(ytr_all, oof, target, dl),
                "yesterday": metrics(ytr_all, shifted(grid, col, train_days, 1), target, dl),
                "persistence_2d": metrics(ytr_all, shifted(grid, col, train_days, 2), target, dl),
            }
        results[target] = r
    return models, results


def m10(grid: F.Grid, train_days: np.ndarray, test_days: np.ndarray, m4: dict,
        demo: dict[str, int]) -> tuple[dict, dict, dict]:
    """M10 candidate selection (training CV only), test metrics, demo-day errors."""
    models, specs, out = {}, {}, {}
    for target in F.TARGETS:
        col = F.SERIES_COL[target]
        Xtr, Str = F.build_features(grid, target, train_days)
        Xte, Ste = F.build_features(grid, target, test_days)
        for X, S in ((Xtr, Str), (Xte, Ste)):
            bad = F.leakage_violations(X, S)
            if bad:
                raise AssertionError(f"leakage in {target}: {bad[:3]}")
        ytr = F.target_values(grid, target, train_days)
        yte = F.target_values(grid, target, test_days)
        dl_tr = DAYLIGHT_FRACTION * float(ytr.max()) if target == "solar" else None
        dl_te = DAYLIGHT_FRACTION * float(yte.max()) if target == "solar" else None
        cands = {"m4": M.SPECS[target], **M.m10_candidates(target)}
        cv, oof, test, fitted = {}, {}, {}, {}
        for name, spec in cands.items():
            cv[name], oof[name] = blocked_monthly_cv_mae(grid, spec, train_days, F.AS_OF_HOUR,
                                                         dl_tr)
            fitted[name] = M.fit(spec, Xtr, ytr)
            test[name] = metrics(yte, M.predict(fitted[name], spec, Xte), target, dl_te)
        chosen = min(cands, key=lambda n: cv[n]["mae_mw"])
        models[target], specs[target] = fitted[chosen], cands[chosen]
        r = {"cv_selection": {n: cv[n] for n in cands}, "chosen": chosen,
             "test": {**{n: test[n] for n in cands},
                      "yesterday": metrics(yte, shifted(grid, col, test_days, 1), target, dl_te)}}
        ref = {"wind": "ercot_stwpf", "solar": "ercot_stppf"}.get(target)
        if ref is not None:
            v = Xte[ref].to_numpy()
            ok = np.isfinite(v)
            r["test"][f"{ref}_direct_reference"] = {
                **metrics(yte[ok], v[ok], target, dl_te), "hours": int(ok.sum()),
                "note": "ERCOT forecast of uncurtailed potential, used directly as a forecast of "
                        "EIA-930 output; reference only"}
        # Demo days are inside the training period: in-sample (fit on all training days) vs
        # out-of-fold (blocked CV) MAE on those days, chosen spec and M4.
        rows = {k: np.flatnonzero(np.repeat(train_days, F.HOURS) == d) for k, d in demo.items()}
        r["demo_days_mae_mw"] = {}
        for n in dict.fromkeys(("m4", chosen)):
            ins = M.predict(fitted[n], cands[n], Xtr)
            r["demo_days_mae_mw"][n] = {
                k: {"in_sample": round(float(np.mean(np.abs(ins[i] - ytr[i]))), 1),
                    "out_of_fold": round(float(np.mean(np.abs(oof[n][i] - ytr[i]))), 1)}
                for k, i in rows.items()}
        out[target] = r
    adopted = out["wind"]["test"][out["wind"]["chosen"]]["mae_mw"] < m4["wind"]["model"]["mae_mw"]
    return models, specs, {"results": out, "adopted": bool(adopted)}


def headline(r: dict, target: str) -> str:
    key = "mape_pct" if target != "solar" else "mape_daylight_pct"
    cells = []
    for name in ("model", "yesterday", "persistence_2d"):
        m = r[name]
        cells.append(f"{m['mae_mw']:>8.0f} {m['nmae']:>6.3f} {m[key]:>7.2f}")
    return " | ".join(cells)


def main() -> None:
    eia = load_eia930()
    grid = F.make_grid(eia)
    F.add_forecast_inputs(grid, load_forecast_inputs())
    split = load_days()["forecast_split"]
    first = F.MIN_HISTORY_DAYS
    train_end = grid.day_index(split["train_end_exclusive"])
    train_days = np.arange(first, train_end)
    test_days = np.arange(grid.day_index(split["test_start"]),
                          grid.day_index(split["test_end"]) + 1)

    models, results = run(F.AS_OF_HOUR, grid, train_days, test_days, with_cv=True)
    _, rejected = run(F.REJECTED_AS_OF_HOUR, grid, train_days, test_days, with_cv=True)

    d = results["demand"]
    passed = d["model"]["mape_pct"] < d["yesterday"]["mape_pct"]
    rd = rejected["demand"]
    payload = {
        "milestone": "M4",
        "as_of_rule": F.AS_OF_RULE,
        "weather_data": "M4: none (wind/solar rely on lags and calendar only); M10: see m10.inputs",
        "model": "sklearn HistGradientBoostingRegressor, one per target (deviation from "
                 "LightGBM: its macOS wheel needs a system libomp that is not installed; "
                 "see model.py)",
        "model_selection": "settings chosen by blocked monthly CV inside the training period "
                           "(under the rejected 10:00 D-1 issue time) and kept unchanged for the "
                           "adopted issue time; the test period was never used for tuning",
        "train_days": {"first": grid.dates[train_days[0]], "last": grid.dates[train_days[-1]],
                       "n_days": int(len(train_days))},
        "test_days": {"first": grid.dates[test_days[0]], "last": grid.dates[test_days[-1]],
                      "n_days": int(len(test_days))},
        "baselines": {
            "yesterday": "previous CST day's actual same-hour value (pre-registered "
                         "yesterday-as-forecast); fully available at as_of = 00:00 CST on D",
            "persistence_2d": "same hour two CST days earlier (context)",
            "ercot_day_ahead_reference": "EIA-930 'Demand Forecast (MW)' reported by ERCOT; "
                                         "issue time undocumented, reference only",
        },
        "metric_notes": {
            "nmae": "MAE / mean actual over the evaluated hours",
            "mape_daylight_pct": f"solar MAPE over hours with actual > {DAYLIGHT_FRACTION:.0%} of "
                                 "the period's peak solar",
            "train_period_cv": "blocked monthly CV inside the training period (14-day buffer); "
                               "reported, not used to tune the adopted setting",
        },
        "pass_criterion": "test-period demand MAPE: model < yesterday (adopted issue time)",
        "demand_pass": bool(passed),
        "results": results,
        "rejected_issue_time_d-1_10:00": {
            "as_of_rule": "as_of = 10:00 CST on day D-1 (day-ahead style)",
            "note": "rejected: yesterday baseline used data after issue time (inconsistent "
                    "comparison)",
            "demand_pass_under_this_setting": bool(rd["model"]["mape_pct"]
                                                   < rd["yesterday"]["mape_pct"]),
            "results": rejected,
        },
    }
    demo = {x["date"]: grid.day_index(x["date"]) for x in load_days()["demo_days"]}
    m10_models, m10_specs, m10_payload = m10(grid, train_days, test_days, results, demo)
    adopted = m10_payload["adopted"]
    payload["m10"] = {
        "rule": "per target, lowest training-period blocked-CV MAE among {m4, m10_a_inputs, "
                "m10_b_ercot_base}; M4 hyperparameters unchanged; test period chooses nothing",
        "adoption_rule": "M10 becomes the default model set iff its test wind MAE < M4's",
        "inputs": "ERCOT NP4-732/737 latest posting before as_of; Open-Meteo gfs_global "
                  "*_previous_day2 (lead >= 48 h); see clearinghouse/data/forecast_inputs.py",
        **m10_payload,
    }
    payload["default_model"] = "M10" if adopted else "M4"
    default_specs = m10_specs if adopted else M.SPECS
    default_models = m10_models if adopted else models
    payload["default_specs"] = {t: s.base + " | " + ("+inputs" if any(
        f in F.EXOG_FEATURES for f in s.features) else "M4 features")
        for t, s in default_specs.items()}
    M.save(default_models, {"train_days": payload["train_days"],
                            "test_days": payload["test_days"],
                            "model_set": payload["default_model"]}, specs=default_specs)
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    METRICS_FILE.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"as_of: {F.AS_OF_RULE}")
    print(f"train {payload['train_days']}  test {payload['test_days']}")
    print(f"{'target':<7}| {'model: MAE  nMAE  MAPE%':>23} | {'yesterday':>23} | "
          f"{'persistence_2d':>23}")
    for t in F.TARGETS:
        print(f"{t:<7}| {headline(results[t], t)}")
    print("(solar MAPE = daylight hours only)")
    ref = d["ercot_day_ahead_reference"]["mape_pct"]
    print(f"demand MAPE by hour group: {d['by_hour_group_mape_pct']}")
    print(f"ERCOT day-ahead forecast (reference only) MAPE {ref:.2f}%")
    for t in F.TARGETS:
        cv = results[t]["train_period_cv"]
        print(f"train-period CV {t:<6}| {headline(cv, t)}")
    print("rejected setting (as_of = 10:00 CST on D-1; inconsistent comparison):")
    for t in F.TARGETS:
        print(f"{t:<7}| {headline(rejected[t], t)}")
    print(f"M4 demand criterion (model MAPE < yesterday MAPE): {'PASS' if passed else 'FAIL'} "
          f"({d['model']['mape_pct']:.3f}% vs {d['yesterday']['mape_pct']:.3f}%)")
    print("\nM10 (CV MAE on training period -> chosen; test MAE MW / nMAE / MAPE%):")
    for t in F.TARGETS:
        r = m10_payload["results"][t]
        cvs = ", ".join(f"{n} {v['mae_mw']:.0f}" for n, v in r["cv_selection"].items())
        print(f"{t:<7} CV: {cvs} -> {r['chosen']}")
        for n, m in r["test"].items():
            key = "mape_pct" if t != "solar" else "mape_daylight_pct"
            print(f"         test {n:<32} {m['mae_mw']:>7.0f} {m['nmae']:>6.3f} {m[key]:>7.2f}")
        print(f"         demo days MAE (in-sample / out-of-fold): {r['demo_days_mae_mw']}")
    print(f"M10 adoption (wind test MAE < M4): {'ADOPTED' if adopted else 'NOT ADOPTED'}; "
          f"default model set = {payload['default_model']}")
    print(f"wrote {METRICS_FILE} and artifacts in {M.ARTIFACT_DIR}")


if __name__ == "__main__":
    main()
