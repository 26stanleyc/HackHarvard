"""M6 API checks (design B6): contract, errors, evaluate, consistency, latency, determinism."""

from __future__ import annotations

import json
import time
import warnings

import numpy as np
import pytest

from clearinghouse import api as api_mod
from clearinghouse.api import (
    ADOPTION_CHART_FILE,
    CALIBRATION_FILE,
    MAX_BIDS,
    SHAPLEY_MAX_BIDS,
    app,
    default_day,
    get_engine,
)
from clearinghouse.clearing import clear, evaluate
from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data import load_days
from clearinghouse.forecast import get_provider
from clearinghouse.inputs import actual_inputs, forecast_inputs
from clearinghouse.params import load_params
from clearinghouse.presets import PRESETS, preset_bids
from clearinghouse.schemas import (
    STRATEGY_NAMES,
    AdoptionChartResponse,
    Bid,
    ClearResponse,
    EvaluateResponse,
    MetaResponse,
    PresetsResponse,
    bid_feasibility_error,
)
from clearinghouse.simulator import DaySimulator

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

DEMO_DATES = [d["date"] for d in load_days()["demo_days"]]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # runs the lifespan (warm-up)
        yield c


def bids_json(n: int | None = None) -> list[dict]:
    """The preset bids (b1..b5) plus, for n > 5, copies with new ids and halved sizes."""
    base = [b.model_dump() for b in preset_bids()]
    out = list(base)
    k = 0
    while n is not None and len(out) < n:
        b = dict(base[k % len(base)])
        b.update(id=f"x{k + 1}", name=f"{b['name']} II", energy_mwh=b["energy_mwh"] / 2,
                 max_mw=b["max_mw"] / 2)
        out.append(b)
        k += 1
    return out[:n] if n is not None else out


def clear_req(day: str | None = None, bids: list[dict] | None = None, **kw) -> dict:
    return {"day": day or default_day(), "bids": bids if bids is not None else bids_json(),
            "background_pct": kw.get("background_pct", 10.0),
            "carbon_price": kw.get("carbon_price", 50.0), "pool_usd": kw.get("pool_usd", 1e5)}


def flat(b: dict) -> list[float]:
    lo, hi = b["earliest_hour"], b["deadline_hour"]
    return [b["energy_mwh"] / (hi - lo) if lo <= h < hi else 0.0 for h in range(24)]


def eval_req(schedule: dict, day: str | None = None, bids: list[dict] | None = None) -> dict:
    return {"day": day or default_day(), "bids": bids if bids is not None else bids_json(),
            "schedule": schedule, "background_pct": 10.0}


def assert_per_bid_422(r, bid_id, fragment: str) -> list[dict]:
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, list) and detail
    for e in detail:
        assert set(e) == {"bid_id", "message"}
    assert any(e["bid_id"] == bid_id and fragment in e["message"] for e in detail), detail
    return detail


# --- GET endpoints -----------------------------------------------------------------------------


def test_meta(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    meta = MetaResponse.model_validate(r.json())
    assert meta.region == "ERCOT"
    assert [d.date for d in meta.days] == DEMO_DATES
    assert meta.default_day == DEMO_DATES[0]
    assert meta.max_bids == MAX_BIDS == 30
    assert meta.shapley_max_bids == SHAPLEY_MAX_BIDS == 8
    assert (meta.carbon_price_default, meta.pool_default_usd, meta.background_pct_default) == (
        50.0, 100000.0, 10.0)
    cal = json.loads(CALIBRATION_FILE.read_text())
    for k, v in meta.calibration.model_dump().items():
        assert v == pytest.approx(cal[k])
    text = " ".join(meta.data_sources)
    for needle in ("EIA-930", "eGRID2023", "NP3-965-ER", "NP4-732/737", "CAMPD", "Illustrative"):
        assert needle in text
    if get_provider().source == "local":
        assert "Chalk credentials pending" in text
        if get_provider().spec.get("model_set") == "M12":
            assert "NP3-565-CD" in text and "NP4-190-CD" in text
        if get_provider().spec.get("model_set") in ("M10", "M12"):  # Open-Meteo CC BY 4.0
            assert "Weather data by Open-Meteo.com (https://open-meteo.com/" in text
            assert "ERCOT's own wind and solar forecasts" in text


def test_presets(client):
    r = client.get("/api/presets")
    assert r.status_code == 200
    presets = PresetsResponse.model_validate(r.json()).root
    assert 5 <= len(presets) <= 6
    assert presets == list(PRESETS)
    for p in presets:
        bid = Bid(id="x", **p.model_dump(exclude={"size_reference"}))
        assert bid_feasibility_error(bid) is None, p.name
        assert p.size_reference


def test_adoption_chart(client):
    r = client.get("/api/adoption-chart")
    assert r.status_code == 200
    chart = AdoptionChartResponse.model_validate(r.json())
    assert chart.x_pct_of_peak == [2.5 * i for i in range(13)]
    assert r.json() == json.loads(ADOPTION_CHART_FILE.read_text())
    for name in STRATEGY_NAMES:
        s = chart.strategies[name]
        for lo, m, hi in zip(s.band_low, s.mean_co2_t, s.band_high, strict=True):
            assert lo - 1e-6 <= m <= hi + 1e-6
    base = {chart.strategies[k].mean_co2_t[0] for k in STRATEGY_NAMES}
    assert len(base) == 1  # x = 0: every strategy is the no-flex baseline
    meta = json.loads((DATA_PROCESSED / "adoption_chart_meta.json").read_text())
    assert meta["day"] == default_day()
    assert meta["seeds"] == [1000, 1001, 1002, 1003, 1004] and meta["n_bids"] == 30


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["forecast_source"] in ("local", "chalk")
    assert body["params_source"] == "calibrated"


def test_cors_preflight(client):
    r = client.options("/api/clear", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    r = client.get("/api/meta", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# --- POST /api/clear ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def example(client):
    req = clear_req()
    r = client.post("/api/clear", json=req)
    assert r.status_code == 200, r.text
    return req, r.json()


def test_clear_contract(example):
    req, body = example
    resp = ClearResponse.model_validate(body)
    ids = {b["id"] for b in req["bids"]}
    for name in STRATEGY_NAMES:
        assert set(resp.strategies[name].schedule) == ids
    assert resp.settlement.method == "exact"
    assert resp.settlement.coalitions_evaluated == 2 ** len(ids)
    assert resp.forecast.source == get_provider().source
    assert resp.forecast.latency_ms >= 0


def test_clear_forecast_block_and_planning_inputs(example):
    """forecast.hourly = the provider's forecast + simulator avg_intensity on forecast inputs, and
    the response equals clearing.clear planned on forecast_inputs, scored on actual_inputs."""
    req, body = example
    day = req["day"]
    fc = get_provider().get_forecast("ERCO", day)
    params, _ = load_params()
    plan_sim = DaySimulator(forecast_inputs(fc), params)
    score_sim = DaySimulator(actual_inputs(day), params)
    hourly = body["forecast"]["hourly"]
    np.testing.assert_allclose(hourly["demand"], fc.demand_mw, rtol=0, atol=1e-9)
    np.testing.assert_allclose(hourly["wind"], fc.wind_mw, rtol=0, atol=1e-9)
    np.testing.assert_allclose(hourly["solar"], fc.solar_mw, rtol=0, atol=1e-9)
    np.testing.assert_allclose(hourly["avg_intensity"], plan_sim.avg_intensity(np.zeros(24)),
                               rtol=0, atol=1e-12)
    assert plan_sim.inputs.kind == "forecast" and score_sim.inputs.kind == "actual"
    direct = clear(day, req["bids"], req["background_pct"], req["carbon_price"], req["pool_usd"],
                   plan_sim, score_sim)
    assert json.dumps(direct, sort_keys=True) == json.dumps(
        {k: v for k, v in body.items() if k not in ("forecast", "bid_features")}, sort_keys=True)


def test_forecast_inputs_not_actuals():
    day = default_day()
    fc = get_provider().get_forecast("ERCO", day)
    fin, act = forecast_inputs(fc), actual_inputs(day)
    assert fin.date == act.date == day and fin.source_date < day
    assert not np.allclose(fin.demand_mw, act.demand_mw)
    np.testing.assert_allclose(fin.fixed_supply_mw, fc.fixed_supply_mw)


def test_clear_deterministic(client):
    req = clear_req(day=DEMO_DATES[2])
    a = client.post("/api/clear", json=req).json()
    b = client.post("/api/clear", json=req).json()
    for x in (a, b):
        x["forecast"].pop("latency_ms")
        x["bid_features"].pop("latency_ms")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_clear_infeasible_bid_422(client):
    bids = bids_json()
    bids[1]["energy_mwh"] = 1e6
    r = client.post("/api/clear", json=clear_req(bids=bids))
    detail = assert_per_bid_422(r, "b2", "exceeds max_mw x window")
    assert all(e["bid_id"] == "b2" for e in detail)
    bids = bids_json()
    bids[0]["earliest_hour"], bids[0]["deadline_hour"] = 10, 10
    assert_per_bid_422(client.post("/api/clear", json=clear_req(bids=bids)), "b1",
                       "must be before deadline_hour")


def test_clear_duplicate_ids_422(client):
    bids = bids_json()
    bids[2]["id"] = "b1"
    assert_per_bid_422(client.post("/api/clear", json=clear_req(bids=bids)), "b1", "duplicate")
    assert_per_bid_422(client.post("/api/evaluate", json=eval_req({}, bids=bids)), "b1",
                       "duplicate")


def test_clear_too_many_bids_422(client):
    r = client.post("/api/clear", json=clear_req(bids=bids_json(MAX_BIDS + 1)))
    detail = assert_per_bid_422(r, None, f"at most {MAX_BIDS} bids")
    assert len(detail) == 1


def test_clear_above_shapley_max_settles_by_marginal(client):
    """More than SHAPLEY_MAX_BIDS bids: settled with marginal-contribution shares."""
    req = clear_req(bids=bids_json(SHAPLEY_MAX_BIDS + 1))
    r = client.post("/api/clear", json=req)
    assert r.status_code == 200, r.text
    resp = ClearResponse.model_validate(r.json())
    st = resp.settlement
    n = SHAPLEY_MAX_BIDS + 1
    assert st.method == "marginal"
    assert len(st.per_bid) == len(st.leave_one_out) == n
    assert sum(b.shapley_t for b in st.per_bid) == pytest.approx(st.total_avoided_t, abs=1e-6)
    assert all(b.payout_usd >= 0 for b in st.per_bid)
    tot = {k: v.total_co2_t for k, v in resp.strategies.items()}
    assert st.total_avoided_t == pytest.approx(
        tot["independent_marginal"] - tot["coordinated"], abs=1e-6)
    ids = {b["id"] for b in req["bids"]}
    for name in STRATEGY_NAMES:
        assert set(resp.strategies[name].schedule) == ids
    assert set(resp.bid_features.per_bid) == ids


def test_unknown_day_422(client):
    r = client.post("/api/clear", json=clear_req(day="2024-05-01"))
    assert_per_bid_422(r, None, "not available")
    r = client.post("/api/evaluate", json=eval_req({}, day="2024-02-03"))  # a gate day
    assert_per_bid_422(r, None, "not available")


def test_scenario_bounds_422(client):
    assert_per_bid_422(client.post("/api/clear", json=clear_req(background_pct=80)), None,
                       "background_pct")


def test_malformed_keeps_default_shape(client):
    bids = bids_json()
    bids[0]["earliest_hour"] = 30
    r = client.post("/api/clear", json=clear_req(bids=bids))
    assert r.status_code == 422
    (err,) = r.json()["detail"]
    assert err["loc"] == ["body", "bids", 0, "earliest_hour"] and "msg" in err


def test_non_finite_inputs_422(client):
    body = json.dumps(clear_req()).replace('"energy_mwh": 2000.0', '"energy_mwh": NaN')
    r = client.post("/api/clear", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 422  # default shape, NaN echoed as a string (not a 500)
    body = json.dumps(clear_req()).replace('"max_mw": 500.0', '"max_mw": Infinity')
    r = client.post("/api/clear", content=body, headers={"content-type": "application/json"})
    assert_per_bid_422(r, "b1", "finite")


# --- POST /api/evaluate ------------------------------------------------------------------------


def test_evaluate_hand_schedule(client):
    bids = bids_json()
    req = eval_req({b["id"]: flat(b) for b in bids})
    r = client.post("/api/evaluate", json=req)
    assert r.status_code == 200
    resp = EvaluateResponse.model_validate(r.json())
    assert resp.feasible and resp.errors == []
    assert sum(resp.hourly_co2_t) == pytest.approx(resp.total_co2_t, abs=1e-6)
    engine = get_engine()
    fc, _ = engine.forecast(req["day"])
    plan, _, _ = engine.plan_sim(fc)
    direct = evaluate(req["day"], bids, req["schedule"], 10.0, plan,
                      engine.score_sim(req["day"]))
    assert r.json() == direct


def test_evaluate_matches_clear(client, example):
    """Submitting the coordinator's own schedule scores exactly its total_co2_t."""
    req, body = example
    for name in STRATEGY_NAMES:
        sched = body["strategies"][name]["schedule"]
        r = client.post("/api/evaluate", json=eval_req(sched, day=req["day"], bids=req["bids"]))
        assert r.status_code == 200 and r.json()["feasible"], r.text
        assert r.json()["total_co2_t"] == pytest.approx(body["strategies"][name]["total_co2_t"],
                                                        rel=1e-12, abs=1e-6)


def test_evaluate_rejects_outside_window_and_over_max(client):
    bids = bids_json()
    sched = {b["id"]: flat(b) for b in bids}
    office = sched["b3"]  # window [6, 18)
    office[3], office[6] = office[6], 0.0  # move one hour outside the window
    ev = sched["b1"]  # 500 MW max, window [0, 7)
    ev[:] = [0.0] * 24
    ev[0], ev[1], ev[2], ev[3] = 600.0, 600.0, 400.0, 400.0  # 2000 MWh, 600 > 500 MW
    r = client.post("/api/evaluate", json=eval_req(sched))
    assert r.status_code == 200
    resp = EvaluateResponse.model_validate(r.json())
    assert not resp.feasible
    errs = {e.bid_id: e.message for e in resp.errors}
    assert set(errs) == {"b1", "b3"}
    assert "outside its window [6, 18)" in errs["b3"] and "[3]" in errs["b3"]
    assert "exceeds max_mw 500 MW" in errs["b1"]


def test_evaluate_rejects_wrong_energy_and_unknown_id(client):
    bids = bids_json()
    sched = {b["id"]: flat(b) for b in bids}
    sched["b2"] = [x * 0.5 for x in sched["b2"]]
    sched["zz"] = [0.0] * 24
    resp = EvaluateResponse.model_validate(
        client.post("/api/evaluate", json=eval_req(sched)).json())
    assert not resp.feasible
    errs = {e.bid_id: e.message for e in resp.errors}
    assert "delivers 1800 MWh but the bid needs 3600 MWh" in errs["b2"]
    assert "unknown bid id" in errs["zz"]


# --- latency (after warm-up) -------------------------------------------------------------------


def test_latency(client):
    eight = bids_json(8)
    times = {}
    for day in DEMO_DATES:
        client.post("/api/evaluate", json=eval_req({}, day=day, bids=eight))  # warm day caches
        t0 = time.perf_counter()
        r = client.post("/api/clear", json=clear_req(day=day, bids=eight))
        times[f"clear8 {day}"] = time.perf_counter() - t0
        assert r.status_code == 200
        sched = r.json()["strategies"]["coordinated"]["schedule"]
        t0 = time.perf_counter()
        r = client.post("/api/evaluate", json=eval_req(sched, day=day, bids=eight))
        times[f"evaluate8 {day}"] = time.perf_counter() - t0
        assert r.status_code == 200 and r.json()["feasible"]
    print("\nAPI latency (s): " + ", ".join(f"{k} {v:.3f}" for k, v in times.items()))
    for k, v in times.items():
        assert v < (5.0 if k.startswith("clear") else 1.0), (k, v)


def test_engine_caches(client):
    engine = get_engine()
    fc1, _ = engine.forecast(default_day())
    a = engine.plan_sim(fc1)[0]
    fc2, _ = engine.forecast(default_day())
    assert engine.plan_sim(fc2)[0] is a
    assert engine.score_sim(default_day()) is engine.score_sim(default_day())
    bg = engine.background(default_day(), engine.plan_sim(fc1)[2], 10.0, a)
    assert not bg[0].flags.writeable


def test_cors_env_origins(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGINS", "https://demo.example.com/, http://x.test:3000")
    origins = api_mod.cors_origins()
    assert origins[:2] == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert "https://demo.example.com" in origins and "http://x.test:3000" in origins


# --- bid features (served by the forecast provider) ------------------------------------------


def test_clear_returns_served_bid_features(example):
    req, body = example
    block = body["bid_features"]
    assert block["source"] == get_provider().source and block["latency_ms"] >= 0
    assert set(block["per_bid"]) == {b["id"] for b in req["bids"]}
    for b in req["bids"]:
        f = block["per_bid"][b["id"]]
        assert f["feasible"] is True
        assert f["window_hours"] == b["deadline_hour"] - b["earliest_hour"]
        assert b["earliest_hour"] <= f["window_lowest_net_load_hour"] < b["deadline_hour"]


def test_bid_features_endpoint_allows_infeasible(client):
    bids = bids_json(2)
    bids[1] = {**bids[1], "energy_mwh": bids[1]["max_mw"] * 100}
    r = client.post("/api/bid-features", json={"day": default_day(), "bids": bids})
    assert r.status_code == 200, r.text
    per = r.json()["per_bid"]
    assert per[bids[0]["id"]]["feasible"] is True
    assert per[bids[1]["id"]]["feasible"] is False
    r = client.post("/api/bid-features", json={"day": "1999-01-01", "bids": bids})
    assert r.status_code == 422
