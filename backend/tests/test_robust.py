"""M11 checks (design doc): robust planning against forecast error."""

from __future__ import annotations

import json
import time
import warnings
from datetime import date, timedelta

import numpy as np
import pytest

from clearinghouse import robust
from clearinghouse.clearing import clear, clear_with_details
from clearinghouse.forecast import get_provider
from clearinghouse.inputs import actual_inputs, forecast_inputs
from clearinghouse.params import load_params
from clearinghouse.presets import preset_bids
from clearinghouse.robust import (
    MIN_ERROR_DAYS,
    ScenarioSimulator,
    build_planning_sim,
    error_days,
    planning_mode,
)
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import validate_schedules

DAY = "2024-11-12"  # forecast test period (2024-10-01..12-31)
PARAMS, _ = load_params()


def _fc(d: str):
    return forecast_inputs(get_provider().get_forecast("ERCO", d))


def _robust_sim(day: str = DAY, actual_for=actual_inputs, forecast_for=_fc):
    return build_planning_sim(day, _fc(day), PARAMS, "robust", forecast_for, actual_for)


def test_error_days_skip_d_and_d_minus_1():
    days = error_days(DAY)
    d0 = date.fromisoformat(DAY)
    assert days == [(d0 - timedelta(days=k)).isoformat() for k in range(2, 12)]
    assert DAY not in days and (d0 - timedelta(days=1)).isoformat() not in days


def test_single_scenario_equals_plain_simulator():
    fc = _fc(DAY)
    plain = DaySimulator(fc, PARAMS)
    one = ScenarioSimulator([DaySimulator(fc, PARAMS)], fc, [])
    rng = np.random.default_rng(0)
    added = rng.uniform(0, 3000, 24)
    batch = rng.uniform(0, 3000, (5, 24))
    hours = np.array([0, 5, 13, 23])
    np.testing.assert_array_equal(one.avg_intensity(added), plain.avg_intensity(added))
    np.testing.assert_array_equal(one.hourly_emissions(added), plain.hourly_emissions(added))
    assert one.emissions(added) == plain.emissions(added)
    np.testing.assert_array_equal(one.hourly_emissions_batch(batch),
                                  plain.hourly_emissions_batch(batch))
    np.testing.assert_array_equal(one.emissions_batch(batch), plain.emissions_batch(batch))
    np.testing.assert_array_equal(one.hourly_co2_at(hours, added[hours]),
                                  plain.hourly_co2_at(hours, added[hours]))


def test_robust_never_reads_d_or_d_minus_1():
    asked: list[str] = []
    forbidden = {DAY, (date.fromisoformat(DAY) - timedelta(days=1)).isoformat()}
    rng = np.random.default_rng(1)

    def poisoned_actual(d):
        asked.append(d)
        a = actual_inputs(d)
        if d in forbidden:  # random values: must never reach the plan
            return a.__class__(**{**a.__dict__, "demand_mw": rng.uniform(0, 1e5, 24)})
        return a

    def recorded_fc(d):
        asked.append(d)
        return _fc(d)

    sim = _robust_sim(actual_for=poisoned_actual, forecast_for=recorded_fc)
    assert not (set(asked) & forbidden)
    ref = _robust_sim()
    batch = np.random.default_rng(2).uniform(0, 2000, (3, 24))
    np.testing.assert_array_equal(sim.emissions_batch(batch), ref.emissions_batch(batch))


def test_robust_builds_scenarios_and_falls_back_when_too_few():
    sim = _robust_sim()
    assert isinstance(sim, ScenarioSimulator)
    assert sim.n_scenarios == 1 + len(sim.error_days_used) >= 1 + MIN_ERROR_DAYS

    def missing(d):
        raise ValueError("no data")

    plain = _robust_sim(actual_for=missing)
    assert isinstance(plain, DaySimulator)


def test_robust_mode_schedules_valid_and_deterministic():
    score = DaySimulator(actual_inputs(DAY), PARAMS)
    plan = _robust_sim()
    bids = preset_bids()
    resp, det = clear_with_details(DAY, bids, 10.0, 50.0, 100000.0, plan, score)
    for name, scheds in det.schedules.items():
        assert validate_schedules(bids, scheds) == {}, name
    again = clear(DAY, bids, 10.0, 50.0, 100000.0, _robust_sim(), score)
    assert json.dumps(resp, sort_keys=True) == json.dumps(again, sort_keys=True)


def test_planning_mode_env(monkeypatch):
    monkeypatch.setenv("PLANNING_MODE", "robust")
    assert planning_mode() == "robust"
    monkeypatch.setenv("PLANNING_MODE", "bogus")
    with pytest.raises(ValueError):
        planning_mode()
    monkeypatch.delenv("PLANNING_MODE")
    assert planning_mode() == robust.DEFAULT_PLANNING_MODE


def test_api_robust_mode_contract_and_latency(monkeypatch):
    monkeypatch.setenv("PLANNING_MODE", "robust")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from fastapi.testclient import TestClient
    from clearinghouse.api import Engine, app, get_engine
    from clearinghouse.schemas import ClearResponse
    from tests.test_api import bids_json

    get_engine.cache_clear()
    try:
        with TestClient(app) as client:
            body = {"day": "2024-08-20", "bids": bids_json(8), "background_pct": 10,
                    "carbon_price": 50, "pool_usd": 100000}
            client.post("/api/clear", json=body)  # warm the day's scenarios
            t0 = time.perf_counter()
            r = client.post("/api/clear", json=body)
            elapsed = time.perf_counter() - t0
            assert r.status_code == 200, r.text
            ClearResponse.model_validate(r.json())
            assert elapsed < 5.0
            meta = client.get("/api/meta").json()
            assert any(s.startswith("Planning: robust") for s in meta["data_sources"])
            assert isinstance(get_engine(), Engine)
    finally:
        get_engine.cache_clear()


def test_vectorized_scenarios_equal_mean_of_scenario_simulators():
    sim = _robust_sim()
    rng = np.random.default_rng(3)
    added = rng.uniform(0, 4000, 24)
    batch = rng.uniform(0, 4000, (4, 24))
    hours = np.array([1, 7, 18])

    def mean(name, *args):
        return np.mean([getattr(s, name)(*args) for s in sim.sims], axis=0)

    np.testing.assert_allclose(sim.avg_intensity(added), mean("avg_intensity", added), rtol=1e-12)
    np.testing.assert_allclose(sim.hourly_emissions_batch(batch),
                               mean("hourly_emissions_batch", batch), rtol=1e-12)
    np.testing.assert_allclose(sim.emissions_batch(batch), mean("emissions_batch", batch),
                               rtol=1e-12)
    np.testing.assert_allclose(sim.hourly_co2_at(hours, added[hours]),
                               mean("hourly_co2_at", hours, added[hours]), rtol=1e-12)
    assert sim.emissions(added) == pytest.approx(float(mean("emissions", added)), rel=1e-12)
