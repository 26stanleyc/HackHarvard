"""M2 simulator checks (docs/design.md, M2). Uses calibration TRAINING days only (no gate days)."""

from __future__ import annotations

import time

import numpy as np
import pytest

from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data import load_days
from clearinghouse.inputs import (
    actual_inputs,
    previous_day,
    residual_check,
    yesterday_forecast_inputs,
)
from clearinghouse.params import DEFAULT_PARAMS, SimParams, load_params
from clearinghouse.schemas import MarginalResource
from clearinghouse.simulator import DaySimulator

if not (DATA_PROCESSED / "eia930_erco_hourly.parquet").is_file():
    pytest.skip("processed data absent", allow_module_level=True)

DAY_LOW_CURT = "2024-01-20"  # training day, curtailment <= 92 MW all day
DAY_HIGH_CURT = "2024-02-04"  # training day, curtailment up to 14.6 GW


@pytest.fixture(scope="module")
def sim() -> DaySimulator:
    return DaySimulator(actual_inputs(DAY_LOW_CURT), DEFAULT_PARAMS)


@pytest.fixture(scope="module")
def sim_curt() -> DaySimulator:
    return DaySimulator(actual_inputs(DAY_HIGH_CURT), DEFAULT_PARAMS)


def test_test_days_are_training_days():
    train = set(load_days()["calibration_train_days"])
    assert {DAY_LOW_CURT, DAY_HIGH_CURT} <= train


def test_zero_load_reproduces_baseline_exactly(sim, sim_curt):
    for s in (sim, sim_curt):
        z = np.zeros(24)
        assert s.emissions(z) == s.baseline["total_co2_t"]
        assert s.emissions() == s.baseline["total_co2_t"]
        assert np.array_equal(s.hourly_emissions(z), s.baseline["hourly_co2_t"])
        assert s.emissions_batch(np.zeros((3, 24))).tolist() == [s.baseline["total_co2_t"]] * 3
        assert s.baseline["unserved_mwh"] == 0.0


def test_adding_load_never_lowers_generation_or_emissions(sim, sim_curt):
    rng = np.random.default_rng(0)
    for s in (sim, sim_curt):
        base_gen = s.fossil_generation()
        base_e = s.hourly_emissions()
        for _ in range(200):
            a = rng.uniform(0, 8000, 24) * (rng.random(24) < 0.6)
            extra = rng.uniform(0, 3000, 24)
            g1, g2 = s.fossil_generation(a), s.fossil_generation(a + extra)
            e1, e2 = s.hourly_emissions(a), s.hourly_emissions(a + extra)
            assert (g1 >= base_gen).all() and (g2 >= g1).all()
            assert (e1 >= base_e).all() and (e2 >= e1).all()


def test_small_load_matches_marginal_plant_rate(sim):
    a0 = sim.inputs.curtailed_mw.copy()  # curtailment fully used -> next MWh is fossil
    s = sim.stack
    checked = 0
    for h in range(24):
        g = sim.fossil_generation(a0)[h]
        mr = sim.marginal_resource(a0)[h]
        assert mr["kind"] == "fossil"
        j = int(np.searchsorted(s.K, g, side="right") - 1)
        if g + 1.0 > s.K[j + 1]:
            continue  # the 1 MWh step would cross into the next plant
        a1 = a0.copy()
        a1[h] += 1.0
        delta = sim.emissions(a1) - sim.emissions(a0)
        assert delta == pytest.approx(mr["co2_t_per_mwh"], rel=1e-6)
        assert sim.marginal_rate(a0)[h] == pytest.approx(mr["co2_t_per_mwh"], rel=1e-12)
        checked += 1
    assert checked >= 20


def test_load_absorbed_by_curtailment_adds_zero(sim_curt):
    c = sim_curt.inputs.curtailed_mw
    assert c.max() > 1000
    a = 0.5 * c
    assert sim_curt.emissions(a) == sim_curt.baseline["total_co2_t"]
    assert sim_curt.absorbed_curtailment_mwh(a) == pytest.approx(a.sum())
    mr = sim_curt.marginal_resource(a)
    for h in range(24):
        assert mr[h]["kind"] == ("curtailed_renewables" if c[h] > 0 else "fossil")
    # beyond curtailment, emissions rise
    assert sim_curt.emissions(c + 100.0) > sim_curt.baseline["total_co2_t"]


def test_one_day_under_50ms(sim):
    a = np.random.default_rng(1).uniform(0, 5000, 24)
    sim.emissions(a)  # warm-up (stack is built in __init__, excluded)
    n = 2000
    t0 = time.perf_counter()
    for _ in range(n):
        sim.emissions(a)
    per_day_ms = (time.perf_counter() - t0) / n * 1000
    print(f"\nsimulated day: {per_day_ms:.4f} ms per emissions() call")
    assert per_day_ms < 50


def test_batch_matches_single(sim_curt):
    rng = np.random.default_rng(2)
    A = rng.uniform(0, 10000, (50, 24))
    batch = sim_curt.emissions_batch(A)
    single = np.array([sim_curt.emissions(a) for a in A])
    assert np.allclose(batch, single, rtol=0, atol=1e-9)
    inc = sim_curt.incremental_cost(A[0], np.arange(24), 10.0)
    for h in (0, 7, 23):
        a1 = A[0].copy()
        a1[h] += 10.0
        assert inc[h] == pytest.approx(sim_curt.emissions(a1) - sim_curt.emissions(A[0]), abs=1e-9)


def test_unserved_energy_is_recorded(sim):
    huge = np.full(24, 1e5)
    assert sim.unserved_mwh(huge) > 0
    s = sim.stack
    req = sim.fossil_requirement(huge)
    expected = s.Q[-1] * 24 + ((req - s.total_mw) * s.last_rate).sum()
    assert sim.emissions(huge) == pytest.approx(expected)
    assert (sim.fossil_generation(huge) == s.total_mw).all()


def test_marginal_resource_schema_and_fuel_split(sim, sim_curt):
    for s in (sim, sim_curt):
        mrs = s.marginal_resource(np.full(24, 500.0))
        assert len(mrs) == 24
        for m in mrs:
            MarginalResource(**m)
        split = s.fuel_split()
        total = split["coal"] + split["gas"] + split["oil"]
        assert np.allclose(total, s.fossil_generation())
        ai = s.avg_intensity()
        assert ai.shape == (24,) and (ai > 0).all() and (ai < 1.5).all()


def test_inputs_balance_and_yesterday(sim):
    inp = sim.inputs
    resid = residual_check(inp)
    assert np.abs(resid).max() < 0.005 * inp.demand_mw.min()
    y = yesterday_forecast_inputs("2024-03-01")
    assert y.source_date == "2024-02-29" == previous_day("2024-03-01")
    assert y.price_month == "2024-03" and y.month == 3
    assert np.array_equal(y.demand_mw, actual_inputs("2024-02-29").demand_mw)


def test_params(tmp_path):
    p, src = load_params(tmp_path / "absent.json")
    assert p == DEFAULT_PARAMS and src == "uncalibrated_default"
    custom = SimParams(availability={"coal": 0.8, "gas": 0.95, "oil": 0.5},
                       price_scale={"coal": 1.1, "gas": 0.9, "oil": 1.0},
                       monthly_price_scale={3: {"gas": 1.2}}, label="x")
    f = tmp_path / "cal.json"
    import json

    f.write_text(json.dumps({"params": custom.to_dict()}))
    q, src = load_params(f)
    assert q == custom and hash(q) == hash(custom) and src.startswith("calibrated:")
    assert q.scale("gas", 3) == pytest.approx(0.9 * 1.2) and q.scale("gas", 4) == pytest.approx(0.9)
    with pytest.raises(ValueError):
        SimParams(availability={"coal": 1.2, "gas": 0.9, "oil": 0.5})
    with pytest.raises(TypeError):
        custom.availability["coal"] = 0.1  # type: ignore[index]
