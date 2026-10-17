"""M2 population / strategy checks. Uses calibration TRAINING days only (never gate days)."""

from __future__ import annotations

import numpy as np
import pytest

from clearinghouse.config import DATA_PROCESSED
from clearinghouse.inputs import actual_inputs, yesterday_forecast_inputs
from clearinghouse.params import DEFAULT_PARAMS
from clearinghouse.population import make_population
from clearinghouse.schemas import Bid, TraceStep, bid_feasibility_error
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import (
    default_schedule,
    default_schedules,
    greedy_place,
    herding_metric,
    independent_average,
    independent_marginal,
    schedule_error,
    sum_schedules,
    validate_schedules,
)

if not (DATA_PROCESSED / "eia930_erco_hourly.parquet").is_file():
    pytest.skip("processed data absent", allow_module_level=True)

DAY = "2024-02-04"  # calibration training day


@pytest.fixture(scope="module")
def sims() -> tuple[DaySimulator, DaySimulator]:
    return (DaySimulator(yesterday_forecast_inputs(DAY), DEFAULT_PARAMS),
            DaySimulator(actual_inputs(DAY), DEFAULT_PARAMS))


@pytest.fixture(scope="module")
def peak() -> float:
    return actual_inputs(DAY).peak_demand_mw


@pytest.mark.parametrize("share", [2.5, 10.0, 30.0])
@pytest.mark.parametrize("seed", [0, 1, 2, 3, 4])
def test_population_feasible_and_scaled(share, seed, peak):
    bids = make_population(share, peak, seed)
    assert len(bids) == 30 and len({b.id for b in bids}) == 30
    assert all(bid_feasibility_error(b) is None for b in bids)
    assert sum(b.max_mw for b in bids) == pytest.approx(share / 100 * peak, rel=1e-12)
    assert all(b.energy_mwh > 0 for b in bids)


def test_population_deterministic_and_shape_invariant(peak):
    a, b = make_population(10, peak, 7), make_population(10, peak, 7)
    assert a == b
    c = make_population(20, peak, 7)
    for x, y in zip(a, c, strict=True):
        assert (x.earliest_hour, x.deadline_hour, x.load_type) == (
            y.earliest_hour, y.deadline_hour, y.load_type)
        assert y.max_mw == pytest.approx(2 * x.max_mw)
    assert make_population(0, peak, 7) == []


def test_default_schedule_shape():
    b = Bid(id="x", name="x", load_type="t", energy_mwh=250, earliest_hour=3, deadline_hour=10,
            max_mw=100)
    s = default_schedule(b)
    assert s[3] == 100 and s[4] == 100 and s[5] == 50 and s.sum() == 250
    assert s[:3].sum() == 0 and s[6:].sum() == 0


def test_schedule_error_catches_violations():
    b = Bid(id="x", name="x", load_type="t", energy_mwh=100, earliest_hour=5, deadline_hour=8,
            max_mw=50)
    ok = np.zeros(24)
    ok[5], ok[6] = 50, 50
    assert schedule_error(b, ok) is None
    bad = ok.copy()
    bad[4] = 1
    assert "outside window" in schedule_error(b, bad)
    bad = np.zeros(24)
    bad[5] = 100
    assert "exceeds max_mw" in schedule_error(b, bad)
    bad = ok.copy()
    bad[7] = 1
    assert "delivers" in schedule_error(b, bad)


@pytest.mark.parametrize("share", [5.0, 20.0, 30.0])
def test_strategy_schedules_satisfy_bids(sims, peak, share):
    plan, _ = sims
    bids = make_population(share, peak, 3)
    for sched in (default_schedules(bids), independent_average(bids, plan),
                  independent_marginal(bids, plan), greedy_place(plan, None, bids)):
        assert validate_schedules(bids, sched) == {}


def test_greedy_deterministic_and_trace(sims, peak):
    plan, _ = sims
    bids = make_population(15, peak, 4)
    bg = np.full(24, 1000.0)
    s1, t1 = greedy_place(plan, bg, bids, record_trace=True)
    s2, t2 = greedy_place(plan, bg, bids, record_trace=True)
    assert t1 == t2
    assert all(np.array_equal(s1[k], s2[k]) for k in s1)
    for b in bids:
        assert sum(t["mwh"] for t in t1 if t["bid_id"] == b.id) == pytest.approx(b.energy_mwh)
    assert [t["step"] for t in t1] == list(range(len(t1)))
    TraceStep(**t1[0])
    assert independent_marginal(bids, plan).keys() == {b.id for b in bids}


def test_greedy_beats_default_on_its_own_forecast(sims, peak):
    """One bid alone, planned and scored on the same inputs: greedy is no worse than default."""
    plan, _ = sims
    for b in make_population(10, peak, 5):
        g = greedy_place(plan, None, [b])[b.id]
        assert plan.emissions(g) <= plan.emissions(default_schedule(b)) + 1e-6


def test_herding_metric_single_bid_has_no_gap(sims, peak):
    plan, score = sims
    bids = make_population(10, peak, 6)[:1]
    r = herding_metric(bids, plan, score, "independent_marginal")
    assert r.predicted_t == pytest.approx(r.realized_t, abs=1e-6)
    assert r.gap_t == pytest.approx(0.0, abs=1e-6)


def test_herding_metric_definition(sims, peak):
    plan, score = sims
    bids = make_population(15, peak, 2)
    r = herding_metric(bids, plan, score, "independent_average")
    d = default_schedules(bids)
    D = sum_schedules(d)
    e_def = score.emissions(D)
    pred = sum(e_def - score.emissions(D - d[b.id] + r.schedules[b.id]) for b in bids)
    real = e_def - score.emissions(sum_schedules(r.schedules))
    assert r.predicted_t == pytest.approx(pred, rel=1e-9)
    assert r.realized_t == pytest.approx(real, rel=1e-9)
    assert r.gap_t == pytest.approx(pred - real, rel=1e-9)
    assert set(r.as_metric()) == {"predicted_t", "realized_t", "gap_t"}
