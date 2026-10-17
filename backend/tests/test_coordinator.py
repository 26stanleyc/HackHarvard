"""M5 coordinator checks (strategies 1-4, fallback, trace, herding, schema, determinism, timing,
evaluate). Uses calibration TRAINING days and demo days only, never herding-gate days. Planning
on yesterday-as-forecast inputs, scoring on actuals, calibrated params."""

from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pytest

from clearinghouse.clearing import (
    TRACE_MAX_STEPS,
    BidValidationError,
    aggregate_trace,
    clear,
    clear_with_details,
    evaluate,
    schedule_placements,
)
from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data import load_days
from clearinghouse.inputs import actual_inputs, yesterday_forecast_inputs
from clearinghouse.params import load_params
from clearinghouse.schemas import STRATEGY_NAMES, Bid, ClearResponse, EvaluateResponse
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import (
    default_schedules,
    greedy_place,
    herding_metric,
    schedule_error,
)

if not (DATA_PROCESSED / "eia930_erco_hourly.parquet").is_file():
    pytest.skip("processed data absent", allow_module_level=True)

DAYS = load_days()
GATE = set(DAYS["herding_gate_days"])
TRAIN = DAYS["calibration_train_days"]
TRAIN_DAY = "2024-02-04"  # calibration training day
DEMO_DAY = "2024-01-16"  # demo day (cold winter morning)
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
PARAMS, _ = load_params()


def make_sims(day: str) -> tuple[DaySimulator, DaySimulator]:
    assert day not in GATE
    return (DaySimulator(yesterday_forecast_inputs(day), PARAMS),
            DaySimulator(actual_inputs(day), PARAMS))


def bid(i, e, a, d, m, name=None) -> Bid:
    return Bid(id=f"b{i}", name=name or f"bid {i}", load_type="t", energy_mwh=e,
               earliest_hour=a, deadline_hour=d, max_mw=m)


EIGHT = [bid(0, 3000, 0, 8, 600), bid(1, 4000, 0, 24, 300), bid(2, 900, 6, 18, 200),
         bid(3, 1500, 0, 24, 120), bid(4, 2000, 17, 24, 500), bid(5, 3500, 0, 24, 250),
         bid(6, 800, 9, 17, 150), bid(7, 2500, 1, 7, 700)]


def dummy_forecast() -> dict:
    return json.loads((FIXTURES / "clear_response.json").read_text())["forecast"]


@pytest.fixture(scope="module")
def train_sims():
    return make_sims(TRAIN_DAY)


@pytest.fixture(scope="module")
def demo_sims():
    return make_sims(DEMO_DAY)


@pytest.fixture(scope="module")
def demo_clear(demo_sims):
    plan, score = demo_sims
    return clear_with_details(DEMO_DAY, EIGHT[:5], 5.0, 50.0, 100000.0, plan, score)


def random_bids(rng: np.random.Generator, n: int) -> list[Bid]:
    out = []
    for i in range(n):
        a = int(rng.integers(0, 20))
        d = int(rng.integers(a + 1, 25))
        m = float(rng.uniform(50, 800))
        e = float(rng.uniform(0.2, 0.9)) * m * (d - a)
        out.append(bid(i, e, a, d, m))
    return out


def trace_by_bid_hour(trace) -> dict:
    acc = defaultdict(float)
    for t in trace:
        acc[(t["bid_id"], t["hour"])] += t["mwh"]
    return acc


def assert_trace_matches(trace, schedules) -> None:
    acc = trace_by_bid_hour(trace)
    for bid_id, s in schedules.items():
        for h in range(24):
            assert acc.get((bid_id, h), 0.0) == pytest.approx(float(s[h]), abs=1e-6)
    assert set(k[0] for k in acc) <= set(schedules)


# --- schema / shape --------------------------------------------------------------------------


def test_clear_validates_against_schema(demo_clear):
    r, _ = demo_clear
    ClearResponse.model_validate({**r, "forecast": dummy_forecast()})
    json.dumps(r)  # plain Python types only
    assert "forecast" not in r


def test_clear_fixture_request_validates(train_sims):
    req = json.loads((FIXTURES / "clear_request.json").read_text())
    plan, score = train_sims
    r = clear(TRAIN_DAY, req["bids"], req["background_pct"], req["carbon_price"],
              req["pool_usd"], plan, score)
    ClearResponse.model_validate({**r, "forecast": dummy_forecast()})


def test_clear_rejects_infeasible_and_duplicate_bids(train_sims):
    plan, score = train_sims
    bad = [bid(0, 5000, 0, 4, 100), bid(1, 10, 5, 5, 10), bid(2, 10, 0, 5, 10),
           bid(2, 10, 0, 5, 10)]
    with pytest.raises(BidValidationError) as ei:
        clear(TRAIN_DAY, bad, 5.0, 50, 1000, plan, score)
    ids = [e["bid_id"] for e in ei.value.errors]
    assert ids == ["b0", "b1", "b2"]
    assert "exceeds max_mw" in ei.value.errors[0]["message"]
    assert "duplicate" in ei.value.errors[2]["message"]


def test_clear_rejects_wrong_day(train_sims):
    plan, score = train_sims
    with pytest.raises(ValueError, match="built for"):
        clear(DEMO_DAY, EIGHT[:2], 5.0, 50, 1000, plan, score)


def test_empty_bids(train_sims):
    plan, score = train_sims
    r = clear(TRAIN_DAY, [], 5.0, 50, 1000, plan, score)
    ClearResponse.model_validate({**r, "forecast": dummy_forecast()})
    assert r["settlement"]["total_avoided_t"] == 0.0 and r["coordinator_trace"] == []
    totals = {r["strategies"][k]["total_co2_t"] for k in STRATEGY_NAMES}
    assert len(totals) == 1


# --- every schedule satisfies its bid --------------------------------------------------------


def test_every_schedule_satisfies_its_bid(demo_clear):
    r, det = demo_clear
    bids = EIGHT[:5]
    for name in STRATEGY_NAMES:
        for b in bids:
            assert schedule_error(b, r["strategies"][name]["schedule"][b.id]) is None, name
    assert len(det.game.plans) == 2 ** len(bids)
    by_id = {b.id: b for b in bids}
    for plan in det.game.plans.values():
        for bid_id, s in plan.schedules.items():
            assert schedule_error(by_id[bid_id], s) is None
    for b in det.background_bids:
        assert b.energy_mwh > 0


def test_background_is_fixed_in_every_strategy(demo_clear):
    _, det = demo_clear
    bg = det.background_24
    assert bg.sum() > 0
    for name in STRATEGY_NAMES:
        user = sum(np.asarray(v) for v in det.schedules[name].values())
        assert np.allclose(det.total_added[name], bg + user, atol=1e-9)
    # stated background: 5% of the FORECAST peak in max MW
    plan, _ = make_sims(DEMO_DAY)
    assert sum(b.max_mw for b in det.background_bids) == pytest.approx(
        0.05 * plan.inputs.peak_demand_mw, rel=1e-12)


# --- coordinated == full coalition, totals, herding ------------------------------------------


def test_coordinated_is_full_coalition_and_totals_consistent(demo_clear):
    r, det = demo_clear
    full = det.game.plans[det.game.full]
    for b in EIGHT[:5]:
        assert np.array_equal(np.asarray(r["strategies"]["coordinated"]["schedule"][b.id]),
                              full.schedules[b.id])
    assert r["fallback_fired"] == full.fallback_fired
    s = r["strategies"]
    assert (s["independent_marginal"]["total_co2_t"] - s["coordinated"]["total_co2_t"]
            == pytest.approx(r["settlement"]["total_avoided_t"], abs=1e-6))
    for name in STRATEGY_NAMES:
        assert sum(s[name]["hourly_co2_t"]) == pytest.approx(s[name]["total_co2_t"], rel=1e-12)
        assert len(s[name]["marginal_resource"]) == 24


def test_herding_block_matches_definition(demo_clear, demo_sims):
    r, det = demo_clear
    plan, score = demo_sims
    s = r["strategies"]
    for name in ("independent_average", "independent_marginal"):
        h = r["herding"][name]
        assert h["gap_t"] == pytest.approx(h["predicted_t"] - h["realized_t"], abs=1e-9)
        assert h["realized_t"] == pytest.approx(
            s["default"]["total_co2_t"] - s[name]["total_co2_t"], abs=1e-6)
    ref = herding_metric(EIGHT[:5], plan, score, "independent_marginal", det.background_24)
    assert r["herding"]["independent_marginal"]["predicted_t"] == pytest.approx(
        ref.predicted_t, abs=1e-6)


# --- fallback guarantee ----------------------------------------------------------------------


def test_strategy4_never_worse_than_strategy3_on_forecast():
    """Fallback guarantee on 20 random scenarios (training days); every coalition plan is also
    never worse than all-strategy-3 on the forecast. Reports the fallback frequency."""
    rng = np.random.default_rng(55)
    fired, coalition_fired, coalitions, done = 0, 0, 0, 0
    pool = [d for d in TRAIN if d not in GATE]
    while done < 20:
        day = pool[int(rng.integers(len(pool)))]
        try:
            plan, score = make_sims(day)
        except ValueError:  # previous day unusable as a forecast
            continue
        bids = random_bids(rng, int(rng.integers(2, 6)))
        r, det = clear_with_details(day, bids, float(rng.uniform(0, 15)), 50, 1e5, plan, score)
        assert det.plan_totals_t["coordinated"] <= det.plan_totals_t["independent_marginal"] + 1e-9
        for p in det.game.plans.values():
            assert p.e_plan_t <= det.game.e_plan_base + 1e-9
            coalition_fired += p.fallback_fired
            coalitions += 1
        fired += r["fallback_fired"]
        assert_trace_matches(r["coordinator_trace"], r["strategies"]["coordinated"]["schedule"])
        done += 1
    print(f"\nfallback fired (full coalition): {fired}/20 scenarios; "
          f"any coalition: {coalition_fired}/{coalitions}")


# --- trace -----------------------------------------------------------------------------------


def test_trace_invariant_and_size(demo_clear):
    r, det = demo_clear
    trace = r["coordinator_trace"]
    assert_trace_matches(trace, r["strategies"]["coordinated"]["schedule"])
    steps = [t["step"] for t in trace]
    assert steps == sorted(steps) and steps[0] == 0
    assert len(set(steps)) <= TRACE_MAX_STEPS
    assert len(det.raw_trace) > TRACE_MAX_STEPS  # aggregation exercised
    assert sum(t["incremental_co2_t"] for t in trace) == pytest.approx(
        sum(t["incremental_co2_t"] for t in det.raw_trace), rel=1e-9)


def test_trace_merges_consecutive_and_small_traces_keep_steps():
    raw = [{"step": k, "bid_id": "a", "hour": 3, "mwh": 1.0, "incremental_co2_t": 0.5}
           for k in range(3)]
    raw += [{"step": 3, "bid_id": "b", "hour": 3, "mwh": 2.0, "incremental_co2_t": 1.0}]
    out = aggregate_trace(raw)
    assert out == [{"step": 0, "bid_id": "a", "hour": 3, "mwh": 3.0, "incremental_co2_t": 1.5},
                   {"step": 1, "bid_id": "b", "hour": 3, "mwh": 2.0, "incremental_co2_t": 1.0}]


def test_fallback_trace_invariant(train_sims):
    """Fallback trace source (strategy-3 schedules, hour order) keeps the invariant."""
    plan, _ = train_sims
    bids = EIGHT
    bg = np.full(24, 500.0)
    s3 = {b.id: greedy_place(plan, bg, [b])[b.id] for b in bids}
    raw = schedule_placements(plan, bg, bids, s3)
    assert [t["hour"] for t in raw] == sorted(t["hour"] for t in raw)
    trace = aggregate_trace(raw)
    assert_trace_matches(trace, s3)
    total = plan.emissions(bg + sum(s3.values())) - plan.emissions(bg)
    assert sum(t["incremental_co2_t"] for t in trace) == pytest.approx(total, rel=1e-9)


# --- determinism and timing ------------------------------------------------------------------


def test_deterministic():
    p1, s1 = make_sims(DEMO_DAY)
    p2, s2 = make_sims(DEMO_DAY)
    a = clear(DEMO_DAY, EIGHT[:4], 7.5, 40.0, 5000.0, p1, s1)
    b = clear(DEMO_DAY, EIGHT[:4], 7.5, 40.0, 5000.0, p2, s2)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_eight_bid_clear_under_5s():
    t0 = time.perf_counter()
    plan, score = make_sims(TRAIN_DAY)
    r = clear(TRAIN_DAY, EIGHT, 10.0, 50.0, 100000.0, plan, score)
    dt = time.perf_counter() - t0
    print(f"\n8-bid clear incl. simulator build: {dt:.2f} s")
    assert r["settlement"]["method"] == "exact"
    assert r["settlement"]["coalitions_evaluated"] == 256
    assert dt < 5.0


# --- evaluate (Beat the coordinator) ---------------------------------------------------------


def test_evaluate_rejects_bad_schedules(demo_sims):
    plan, score = demo_sims
    bids = EIGHT[:3]
    d = {k: v.tolist() for k, v in default_schedules(bids).items()}
    bad = dict(d)
    s0 = list(d["b0"])
    s0[0], s0[9] = s0[0] - 100, 100.0  # b0 window is [0, 8): energy moved outside
    bad["b0"] = s0
    s1 = [0.0] * 24
    s1[0] = 4000.0  # b1: above max MW (300)
    bad["b1"] = s1
    s2 = list(d["b2"])
    s2[6] -= 50  # b2: energy short by 50 MWh
    bad["b2"] = s2
    bad["zz"] = [0.0] * 24
    r = evaluate(DEMO_DAY, bids, bad, 5.0, plan, score)
    EvaluateResponse.model_validate(r)
    assert r["feasible"] is False
    msgs = {e["bid_id"]: e["message"] for e in r["errors"]}
    assert set(msgs) == {"b0", "b1", "b2", "zz"}
    assert "outside its window" in msgs["b0"]
    assert "exceeds max_mw" in msgs["b1"]
    assert "delivers" in msgs["b2"] and "outside" not in msgs["b2"]
    assert "unknown bid id" in msgs["zz"]
    # infeasible -> scores of the all-default schedule
    ok = evaluate(DEMO_DAY, bids, d, 5.0, plan, score)
    assert r["total_co2_t"] == ok["total_co2_t"] and r["hourly_co2_t"] == ok["hourly_co2_t"]


def test_evaluate_scores_valid_hand_schedule(demo_clear, demo_sims):
    plan, score = demo_sims
    r, _ = demo_clear
    bids = EIGHT[:5]
    for name in STRATEGY_NAMES:  # each strategy's schedule scores exactly like the strategy
        ev = evaluate(DEMO_DAY, bids, r["strategies"][name]["schedule"], 5.0, plan, score)
        EvaluateResponse.model_validate(ev)
        assert ev["feasible"] and ev["errors"] == []
        assert ev["total_co2_t"] == pytest.approx(r["strategies"][name]["total_co2_t"], abs=1e-6)
        assert ev["marginal_resource"] == r["strategies"][name]["marginal_resource"]
    # a hand schedule: b2 (900 MWh, window 6-18, 200 MW) spread evenly over 12-16 + 6
    hand = [0.0] * 24
    for h in (12, 13, 14, 15):
        hand[h] = 200.0
    hand[16] = 100.0
    ev = evaluate(DEMO_DAY, bids, {"b2": hand}, 5.0, plan, score)
    assert ev["feasible"]
    # omitted bids are filled with default: same as passing defaults explicitly
    d = {k: v.tolist() for k, v in default_schedules(bids).items()}
    d["b2"] = hand
    ev2 = evaluate(DEMO_DAY, bids, d, 5.0, plan, score)
    assert ev2["total_co2_t"] == ev["total_co2_t"]
    assert ev["total_co2_t"] != r["strategies"]["default"]["total_co2_t"]


def test_evaluate_tolerances(demo_sims):
    plan, score = demo_sims
    b = bid(0, 1000, 2, 6, 300)
    s = [0.0] * 24
    s[2], s[3], s[4], s[5] = 300 + 1e-7, 300.0, 300.0, 100.0 + 0.5  # +0.05% energy, ok
    s[0] = 1e-7
    r = evaluate(DEMO_DAY, [b], {"b0": s}, 0.0, plan, score)
    assert r["feasible"], r["errors"]
    s[5] = 100 + 2.0  # +0.2% energy
    r = evaluate(DEMO_DAY, [b], {"b0": s}, 0.0, plan, score)
    assert not r["feasible"] and "delivers" in r["errors"][0]["message"]
    r = evaluate(DEMO_DAY, [b], {"b0": [1.0] * 23}, 0.0, plan, score)
    assert not r["feasible"] and "24 hourly values" in r["errors"][0]["message"]
