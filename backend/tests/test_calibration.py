"""M3 calibration checks: day separation, fit never touches holdout days, params round-trip,
calibration.json shape."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

import clearinghouse.calibration as cal
from clearinghouse.config import RESULTS_DIR
from clearinghouse.data import load_days, load_emission_factors
from clearinghouse.inputs import actual_inputs
from clearinghouse.params import (
    CALIBRATED_PARAMS_FILE,
    DEFAULT_PARAMS,
    FUELS,
    SimParams,
    load_params,
)

DAYS = load_days()
TRAIN = DAYS["calibration_train_days"]
HOLDOUT = DAYS["calibration_holdout_days"]
DEMO = [d["date"] for d in DAYS["demo_days"]]
GATE = DAYS["herding_gate_days"]

QUICK = cal.FitConfig("quick", "test config", init_avail={"coal": 0.9, "gas": 0.9},
                      init_coal_scale=1.0, avail_grid_steps=(0.1,), max_iter=2)


def _small_train() -> list[str]:
    jan = [d for d in TRAIN if d.startswith("2024-01")][:2]
    jul = [d for d in TRAIN if d.startswith("2024-07")][:2]
    return jan + jul


def test_day_sets_do_not_overlap():
    sets = {"train": set(TRAIN), "holdout": set(HOLDOUT), "demo": set(DEMO), "gate": set(GATE)}
    assert len(sets["train"]) == len(TRAIN) and len(sets["holdout"]) == len(HOLDOUT)
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not sets[a] & sets[b], f"{a} and {b} overlap"
    assert not sets["train"] & set(DAYS["never_trained_days"])
    assert len(TRAIN) == 248 and len(HOLDOUT) == 104


@pytest.mark.parametrize("bad", [HOLDOUT[0], DEMO[0], GATE[0]])
def test_fit_refuses_never_trained_days(bad):
    with pytest.raises(ValueError, match="never-trained"):
        cal.fit(_small_train() + [bad], QUICK)


def test_fit_only_touches_training_days(monkeypatch):
    loaded, simulated = [], []
    real_sim = cal.DaySimulator

    def spy_loader(date):
        loaded.append(date)
        return actual_inputs(date)

    class SpySim(real_sim):
        def __init__(self, inputs, *a, **k):
            simulated.append(inputs.date)
            super().__init__(inputs, *a, **k)

    monkeypatch.setattr(cal, "actual_inputs", spy_loader)
    monkeypatch.setattr(cal, "DaySimulator", SpySim)
    train = _small_train()
    res = cal.fit(train, QUICK)
    assert loaded == train
    assert simulated and set(simulated) <= set(train)
    assert not (set(loaded) | set(simulated)) & set(HOLDOUT + DEMO + GATE)
    # only the named parameters, inside their bounds
    p = res.params
    assert set(p.monthly_price_scale) == {1, 7}
    for v in p.monthly_price_scale.values():
        assert set(v) == {"coal"}
        assert cal.COAL_SCALE_BOUNDS[0] <= v["coal"] <= cal.COAL_SCALE_BOUNDS[1]
    for f in cal.FITTED_AVAIL_FUELS:
        assert cal.AVAIL_BOUNDS[0] <= p.availability[f] <= cal.AVAIL_BOUNDS[1]
    assert p.availability["oil"] == DEFAULT_PARAMS.availability["oil"]
    assert dict(p.price_scale) == {f: 1.0 for f in FUELS}
    assert res.train_objective <= cal.objective(
        cal.make_params({"coal": 0.9, "gas": 0.9}, {1: 1.0, 7: 1.0}), cal.load_day_data(train))


def test_fast_month_path_matches_day_simulator():
    days = cal.load_day_data(_small_train())
    f = cal._Fitter(days)
    rng = np.random.default_rng(0)
    for _ in range(5):
        av = {"coal": float(rng.uniform(0.3, 1)), "gas": float(rng.uniform(0.3, 1))}
        sc = {m: float(rng.uniform(0.1, 3.0)) for m in f.months}
        assert math.isclose(f.total(av, sc)[0], cal.objective(cal.make_params(av, sc), days),
                            rel_tol=1e-9)


def test_observed_co2_uses_fuel_emission_factors():
    inp = actual_inputs(TRAIN[0])
    obs = cal.observed_hourly(inp)
    ef = load_emission_factors()
    oil = np.nan_to_num(inp.actual_fossil["oil"])
    expect = inp.actual_fossil["coal"] * ef["coal"] + inp.actual_fossil["gas"] * ef["gas"] \
        + oil * ef["oil"]
    np.testing.assert_allclose(obs["co2_t"], expect)
    assert np.all((obs["coal_share"] >= 0) & (obs["coal_share"] <= 1))


def test_calibrated_params_round_trip(tmp_path):
    p = cal.make_params({"coal": 0.55, "gas": 0.65}, {m: 0.5 + 0.01 * m for m in range(1, 13)})
    f = tmp_path / "calibrated_params.json"
    f.write_text(json.dumps({"params": p.to_dict(), "fitted_on": "test"}))
    q, src = load_params(f)
    assert q == p and src.startswith("calibrated:")
    assert q.scale("coal", 3) == pytest.approx(0.53) and q.scale("gas", 3) == 1.0


def test_committed_calibrated_params_load():
    assert CALIBRATED_PARAMS_FILE.is_file(), "run scripts/calibrate.py"
    p, src = load_params()
    assert src.startswith("calibrated:")
    assert isinstance(p, SimParams) and p != DEFAULT_PARAMS
    assert set(p.monthly_price_scale) == set(range(1, 13))
    assert all(set(v) == {"coal"} for v in p.monthly_price_scale.values())
    assert p.availability["oil"] == DEFAULT_PARAMS.availability["oil"]


def test_calibration_json_keys_finite():
    path = RESULTS_DIR / "calibration.json"
    assert path.is_file(), "run scripts/calibrate.py"
    d = json.loads(path.read_text())
    for k in ("coal_gas_split_error", "mef_simulated", "mef_observed", "fossil_co2_error"):
        assert isinstance(d[k], float) and math.isfinite(d[k]), k
    for k in ("params", "attempts", "holdout_days_n", "train_days_n", "definitions"):
        assert k in d, k
    assert d["holdout_days_n"] == len(HOLDOUT) and d["train_days_n"] == len(TRAIN)
    assert 2 <= len(d["attempts"]) <= 6  # rejected attempt 1 + 1b (+ 2-5 only if 1b missed)
    first = d["attempts"][0]
    assert first["attempt"] == "1" and first["status"] == "rejected"
    assert "infeasible" in first["note"]
    assert d["attempts"][1]["attempt"] == "1b"
    adopted = [a for a in d["attempts"] if a["status"] == "adopted"]
    assert len(adopted) == 1 and adopted[0]["config"]["feasibility"]
    assert d["selected_attempt"] == adopted[0]["attempt"]
    for k in ("train", "holdout", "demo", "gate"):
        assert d["adopted_unserved"][k]["hours"] == 0, k
    for a in d["attempts"]:
        assert math.isfinite(a["train_objective"])
        assert math.isfinite(a["holdout"]["fossil_co2_error"])
    assert SimParams.from_dict(d["params"]) == load_params()[0]
    assert d["fossil_co2_error"] == pytest.approx(
        d["holdout"]["after_calibrated"]["c_fossil_co2"]["hourly_mape"])


def test_adopted_params_serve_every_training_hour():
    """Physical feasibility: the adopted stack serves each training hour's observed fossil
    generation and the simulator's fossil requirement (no unserved energy)."""
    p, _ = load_params()
    fb = cal.feasibility_bounds(cal.load_day_data(TRAIN))
    cap = cal.fleet_capacity_mw()
    for f in ("coal", "gas"):
        assert p.availability[f] * cap[f] >= fb[f"max_obs_{f}_mw"] - 1e-6
    days = cal.load_day_data(TRAIN)
    for d, sim in zip(days, cal.simulate_days(p, days), strict=True):
        assert sim.stack.total_mw >= d.obs["fossil_mw"].max() - 1e-6, d.date
        assert sim.unserved_mwh(None) == 0.0, d.date


def test_feasibility_constrained_fit_respects_constraints():
    train = _small_train()
    cfg = cal.FitConfig("quick_feasible", "test", init_avail={"coal": 0.3, "gas": 0.3},
                        init_coal_scale=1.0, avail_grid_steps=(0.1,), max_iter=2,
                        feasibility=True)
    res = cal.fit(train, cfg)
    fb = res.feasibility
    assert fb is not None
    assert cal.is_feasible(dict(res.params.availability), fb)
    days = cal.load_day_data(train)
    assert fb["max_obs_fossil_mw"] == max(d.obs["fossil_mw"].max() for d in days)
