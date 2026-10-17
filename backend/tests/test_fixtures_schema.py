import json

import pytest
from pydantic import ValidationError

from clearinghouse.config import FIXTURES_DIR
from clearinghouse.schemas import (
    STRATEGY_NAMES,
    AdoptionChartResponse,
    Bid,
    ClearRequest,
    ClearResponse,
    EvaluateRequest,
    EvaluateResponse,
    MetaResponse,
    PresetsResponse,
    bid_feasibility_error,
)

MODELS = {
    "meta.json": MetaResponse,
    "presets.json": PresetsResponse,
    "adoption_chart.json": AdoptionChartResponse,
    "clear_request.json": ClearRequest,
    "clear_response.json": ClearResponse,
    "evaluate_request.json": EvaluateRequest,
    "evaluate_response.json": EvaluateResponse,
}
TOL = 1e-3


def load(name):
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.mark.parametrize("name", sorted(MODELS))
def test_fixture_parses(name):
    MODELS[name].model_validate(load(name))


@pytest.fixture(scope="module")
def clear():
    return ClearRequest.model_validate(load("clear_request.json")), ClearResponse.model_validate(
        load("clear_response.json")
    )


def test_clear_is_example_scenario_all_strategies(clear):
    """Example-scenario rule (M6): one company per preset, ids b1..bn, on meta's defaults."""
    req, resp = clear
    presets = PresetsResponse.model_validate(load("presets.json")).root
    meta = MetaResponse.model_validate(load("meta.json"))
    assert [b.id for b in req.bids] == [f"b{k}" for k in range(1, len(presets) + 1)]
    for b, p in zip(req.bids, presets, strict=True):
        assert b.model_dump(exclude={"id"}) == p.model_dump(exclude={"size_reference"})
    assert req.day == meta.default_day
    assert (req.background_pct, req.carbon_price, req.pool_usd) == (
        meta.background_pct_default, meta.carbon_price_default, meta.pool_default_usd)
    assert set(resp.strategies) == set(STRATEGY_NAMES)


def test_schedules_respect_bids(clear):
    req, resp = clear
    for name, strat in resp.strategies.items():
        assert set(strat.schedule) == {b.id for b in req.bids}, name
        for b in req.bids:
            s = strat.schedule[b.id]
            assert abs(sum(s) - b.energy_mwh) <= 1e-3 * max(b.energy_mwh, 1), (name, b.id)
            for h, mw in enumerate(s):
                assert mw >= 0
                assert mw <= b.max_mw + 1e-9, (name, b.id, h)
                if not (b.earliest_hour <= h < b.deadline_hour):
                    assert mw == 0, (name, b.id, h)


def test_default_runs_asap(clear):
    req, resp = clear
    for b in req.bids:
        s = resp.strategies["default"].schedule[b.id]
        first = next(h for h, mw in enumerate(s) if mw > 0)
        assert first == b.earliest_hour


def test_hourly_co2_sums_to_total(clear):
    _, resp = clear
    for name, strat in resp.strategies.items():
        assert abs(sum(strat.hourly_co2_t) - strat.total_co2_t) < TOL, name


def test_herding_gap(clear):
    _, resp = clear
    for m in (resp.herding.independent_average, resp.herding.independent_marginal):
        assert abs(m.predicted_t - m.realized_t - m.gap_t) < TOL


def test_trace(clear):
    req, resp = clear
    bids = {b.id: b for b in req.bids}
    trace = resp.coordinator_trace
    assert len(trace) >= 1
    steps = [t.step for t in trace]
    # engine trace: <= 200 steps; several entries may share a step after aggregation
    assert steps == sorted(steps) and len(set(steps)) <= 200
    placed = {}
    for t in trace:
        b = bids[t.bid_id]
        assert b.earliest_hour <= t.hour < b.deadline_hour
        assert t.mwh <= b.max_mw + 1e-9
        placed[(t.bid_id, t.hour)] = placed.get((t.bid_id, t.hour), 0.0) + t.mwh
    coord = resp.strategies["coordinated"].schedule
    for bid_id, sched in coord.items():  # trace MWh per (bid, hour) == coordinated schedule
        for h, mw in enumerate(sched):
            assert abs(placed.get((bid_id, h), 0.0) - mw) < 1e-6, (bid_id, h)


def test_settlement(clear):
    req, resp = clear
    st = resp.settlement
    ids = {b.id for b in req.bids}
    assert st.baseline == "independent_marginal"
    assert {p.bid_id for p in st.per_bid} == ids
    assert abs(sum(p.shapley_t for p in st.per_bid) - st.total_avoided_t) < TOL
    realized = (
        resp.strategies["independent_marginal"].total_co2_t
        - resp.strategies["coordinated"].total_co2_t
    )
    assert abs(realized - st.total_avoided_t) < TOL
    assert sum(p.payout_usd for p in st.per_bid) <= st.pool_usd + 1e-6
    for p in st.per_bid:
        if p.shapley_t <= 0:
            assert p.payout_usd == 0
    if st.method == "exact":
        assert st.coalitions_evaluated == 2 ** len(ids)
    assert set(st.leave_one_out) == ids
    for removed, loo in st.leave_one_out.items():
        assert {p.bid_id for p in loo.per_bid} == ids - {removed}
        assert abs(sum(p.shapley_t for p in loo.per_bid) - loo.total_avoided_t) < TOL
        assert sum(p.payout_usd for p in loo.per_bid) <= st.pool_usd + 1e-6


def test_evaluate_consistent():
    req = EvaluateRequest.model_validate(load("evaluate_request.json"))
    resp = EvaluateResponse.model_validate(load("evaluate_response.json"))
    assert resp.feasible and resp.errors == []
    assert abs(sum(resp.hourly_co2_t) - resp.total_co2_t) < TOL
    for b in req.bids:
        s = req.schedule[b.id]
        assert abs(sum(s) - b.energy_mwh) < 1e-6
        assert max(s) <= b.max_mw


def test_presets_feasible():
    for p in PresetsResponse.model_validate(load("presets.json")).root:
        bid = Bid(id="x", **p.model_dump(exclude={"size_reference"}))
        assert bid_feasibility_error(bid) is None, p.name


# --- schema behavior --------------------------------------------------------------------------


def _bid(**kw):
    base = dict(
        id="b", name="n", load_type="ev_fleet", energy_mwh=100, earliest_hour=0,
        deadline_hour=4, max_mw=25,
    )
    return Bid(**{**base, **kw})


def test_bid_feasibility_rule():
    assert bid_feasibility_error(_bid()) is None  # 25 MW x 4 h = 100 MWh exactly
    assert "exceeds" in bid_feasibility_error(_bid(energy_mwh=100.5))
    assert "before" in bid_feasibility_error(_bid(earliest_hour=4, deadline_hour=4))
    assert bid_feasibility_error(_bid(earliest_hour=23, deadline_hour=24, energy_mwh=25)) is None


def test_clear_request_does_not_reject_infeasible_bids():
    data = load("clear_request.json")
    data["bids"][0]["energy_mwh"] = 1e9
    req = ClearRequest.model_validate(data)
    assert bid_feasibility_error(req.bids[0]) is not None


@pytest.mark.parametrize(
    "kw", [dict(earliest_hour=24), dict(earliest_hour=-1), dict(deadline_hour=0),
           dict(deadline_hour=25), dict(energy_mwh=-1)],
)
def test_malformed_bid_rejected(kw):
    with pytest.raises(ValidationError):
        _bid(**kw)


def test_hourly_arrays_must_be_24():
    data = load("clear_response.json")
    data["strategies"]["default"]["hourly_co2_t"] = data["strategies"]["default"][
        "hourly_co2_t"
    ][:23]
    with pytest.raises(ValidationError):
        ClearResponse.model_validate(data)
    data = load("evaluate_request.json")
    first = next(iter(data["schedule"]))
    data["schedule"][first].append(0.0)
    with pytest.raises(ValidationError):
        EvaluateRequest.model_validate(data)


def test_extra_keys_and_missing_strategy_rejected():
    data = load("clear_response.json")
    data["_fixture"] = True
    with pytest.raises(ValidationError):
        ClearResponse.model_validate(data)
    data = load("clear_response.json")
    del data["strategies"]["coordinated"]
    with pytest.raises(ValidationError):
        ClearResponse.model_validate(data)
