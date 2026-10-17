"""M4 checks: forecast model + provider switch (design M4; thresholds pre-registered there).

(a) no leakage (source timestamps + perturbation), (b) demand model beats yesterday-as-forecast
on the test period, (c) empty CHALK_* -> local provider with source "local", (d) one-day
forecast under 500 ms and cached, (e) Chalk REST request construction with a mock transport
(no network). Needs ``scripts/train_forecast.py`` to have been run (artifacts + metrics json).
M10 additions: candidate selection by training CV only, the adoption rule, the default artifacts,
and the provider ignoring forecast inputs for slots after the target day. Leakage checks of the
forecast inputs themselves are in ``test_forecast_inputs.py``.
"""

from __future__ import annotations

import json
import time

import httpx
import numpy as np
import pandas as pd
import pytest

from clearinghouse import config
from clearinghouse.config import RESULTS_DIR
from clearinghouse.data import load_eia930
from clearinghouse.forecast import features as F
from clearinghouse.forecast import get_provider
from clearinghouse.forecast import model as M
from clearinghouse.forecast.chalk import ChalkError, ChalkForecastProvider
from clearinghouse.forecast.fallback import FallbackProvider
from clearinghouse.forecast.local import LocalForecastProvider
from clearinghouse.forecast.provider import BID_FEATURE_NAMES, HOURLY_FIELDS

METRICS = RESULTS_DIR / "forecast_metrics.json"
CHECK_DATES = ("2023-12-16", "2024-03-10", "2024-03-29", "2024-07-01", "2024-10-01",
               "2024-11-04", "2024-12-31")

if not (M.ARTIFACT_DIR / M.SPEC_FILE).is_file() or not METRICS.is_file():
    pytest.skip("forecast artifacts absent: run uv run python scripts/train_forecast.py",
                allow_module_level=True)


@pytest.fixture(scope="module")
def grid() -> F.Grid:
    return F.make_grid(load_eia930())


@pytest.fixture(scope="module")
def local() -> LocalForecastProvider:
    return LocalForecastProvider()


# --- (a) no leakage --------------------------------------------------------------------------


@pytest.mark.parametrize("target", F.TARGETS)
def test_every_feature_source_is_before_as_of(grid, target):
    days = np.array([grid.day_index(d) for d in CHECK_DATES])
    X, S = F.build_features(grid, target, days)
    assert F.leakage_violations(X, S) == []
    for d in days:
        rows = (X["day"] == d).to_numpy()
        as_of = np.datetime64(grid.as_of_ts(int(d)).to_datetime64(), "ns")
        for name in F.DATA_FEATURES:
            src = S.loc[rows, name].to_numpy()
            ts = grid.slot_ts(src)
            known = ~np.isnat(ts)
            # slot start strictly before as_of AND the whole slot elapsed by as_of
            assert (ts[known] < as_of).all(), (target, name, grid.dates[d])
            assert (ts[known] + np.timedelta64(1, "h") <= as_of).all(), (target, name)
        # every data feature that has a value read at least one source slot
        vals = X.loc[rows, list(F.DATA_FEATURES)].to_numpy()
        srcs = S.loc[rows, list(F.DATA_FEATURES)].to_numpy()
        assert (np.isfinite(vals) <= np.isfinite(srcs)).all()
    # calendar features read no data
    assert S[list(F.CALENDAR_FEATURES)].isna().all().all()


def test_adopted_issue_time_is_midnight_of_target_day(grid):
    d = grid.day_index("2024-10-15")
    assert grid.as_of_ts(d) == pd.Timestamp("2024-10-15 00:00")
    X, S = F.build_features(grid, "demand", np.array([d]))
    # all of D-1 is available: the 1-day lag exists for every hour; nothing from D is read
    assert X["lag1d"].notna().all()
    assert int(S[list(F.DATA_FEATURES)].max().max()) == d * 24 - 1  # D-1 23:00 slot
    np.testing.assert_array_equal(X["same_hour_latest"], X["lag1d"])


def test_builder_masks_unavailable_lags_for_earlier_issue_time(grid):
    """Rigor of the masking itself, shown with the rejected 10:00 D-1 issue time."""
    d = grid.day_index("2024-10-15")
    X, S = F.build_features(grid, "demand", np.array([d]), as_of_hour=F.REJECTED_AS_OF_HOUR)
    assert F.leakage_violations(X, S, F.REJECTED_AS_OF_HOUR) == []
    h = X["hour_slot"].to_numpy()
    assert X.loc[h < 10, "lag1d"].notna().all()
    assert X.loc[h >= 10, "lag1d"].isna().all()
    np.testing.assert_array_equal(X["same_hour_latest"].to_numpy(),
                                  np.where(h < 10, X["lag1d"], X["lag2d"]))


@pytest.mark.parametrize("as_of_hour", [F.AS_OF_HOUR, F.REJECTED_AS_OF_HOUR])
def test_whole_training_and_test_period_leakage_free(grid, as_of_hour):
    days = np.arange(F.MIN_HISTORY_DAYS, grid.day_index("2025-01-01"))
    for target in F.TARGETS:
        X, S = F.build_features(grid, target, days, as_of_hour)
        assert F.leakage_violations(X, S, as_of_hour) == []


def _perturbed_grid_from(grid: F.Grid, cut: int, mode: str, seed: int) -> F.Grid:
    rng = np.random.default_rng(seed)
    series = {}
    for col, v in grid.series.items():
        w = v.copy()
        w[cut:] = np.nan if mode == "nan" else rng.uniform(-1e6, 1e6, len(w) - cut)
        series[col] = w
    return F.Grid(first_day=grid.first_day, dates=grid.dates, series=series)


@pytest.mark.parametrize("mode", ["random", "nan"])
@pytest.mark.parametrize("as_of_hour", [F.AS_OF_HOUR, F.REJECTED_AS_OF_HOUR])
def test_features_unchanged_when_data_at_or_after_as_of_is_perturbed(grid, mode, as_of_hour):
    for date in CHECK_DATES:
        d = grid.day_index(date)
        pert = _perturbed_grid_from(grid, int(F.as_of_index(d, as_of_hour)), mode, d)
        for target in F.TARGETS:
            X0, _ = F.build_features(grid, target, np.array([d]), as_of_hour)
            X1, _ = F.build_features(pert, target, np.array([d]), as_of_hour)
            pd.testing.assert_frame_equal(X0, X1)


def test_provider_result_unchanged_under_perturbation(local):
    """End to end: every served field (ML + persistence + curtailment) ignores data >= as_of."""
    from clearinghouse.data import load_curtailment

    date = "2024-11-04"
    as_of = pd.Timestamp("2024-11-04 00:00")
    pert_e, pert_c = load_eia930().copy(), load_curtailment().copy()
    for df in (pert_e, pert_c):
        late = df["ts_cst"] >= as_of
        num = [c for c in df.columns if c.endswith("_mw")]
        df.loc[late, num] = df.loc[late, num] * 3.0 + 1234.0
    a = local.get_forecast("ERCO", date)
    b = LocalForecastProvider(eia=pert_e, curtailment=pert_c).get_forecast("ERCO", date)
    assert a.as_of == b.as_of == "2024-11-04T00:00:00-06:00"
    for name in HOURLY_FIELDS:
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name))
    # and the perturbation does reach data the provider must not read
    pc = LocalForecastProvider(eia=pert_e, curtailment=pert_c).get_forecast("ERCO", "2024-11-05")
    assert not np.array_equal(pc.nuclear_mw, local.get_forecast("ERCO", "2024-11-05").nuclear_mw)


# --- (b) demand beats yesterday-as-forecast on the test period -------------------------------


def test_metrics_file_covers_pre_registered_test_period():
    m = json.loads(METRICS.read_text())
    assert m["test_days"] == {"first": "2024-10-01", "last": "2024-12-31", "n_days": 92}
    assert m["train_days"]["last"] == "2024-09-30"
    rejected = m["rejected_issue_time_d-1_10:00"]
    assert rejected["note"].startswith("rejected: yesterday baseline used data after issue time")
    for t in F.TARGETS:
        assert {"model", "yesterday", "persistence_2d"} <= set(m["results"][t])
        assert {"model", "yesterday", "persistence_2d"} <= set(rejected["results"][t])


def test_demand_model_mape_beats_yesterday_baseline():
    """Pre-registered M4 criterion (design M4), judged on the adopted issue time (00:00 CST D)."""
    m = json.loads(METRICS.read_text())
    assert m["as_of_rule"] == F.AS_OF_RULE
    d = m["results"]["demand"]
    assert d["model"]["mape_pct"] < d["yesterday"]["mape_pct"], (
        f"demand MAPE model {d['model']['mape_pct']:.3f}% vs yesterday "
        f"{d['yesterday']['mape_pct']:.3f}% (strict 2-day persistence "
        f"{d['persistence_2d']['mape_pct']:.3f}%)")


# --- M10: selection, adoption rule, default artifacts ----------------------------------------


def test_m10_chosen_by_training_cv_and_adoption_rule_applied():
    m = json.loads(METRICS.read_text())
    m10 = m["m10"]
    for t in F.TARGETS:
        r = m10["results"][t]
        cv = r["cv_selection"]
        assert r["chosen"] == min(cv, key=lambda n: cv[n]["mae_mw"])
        assert r["test"]["m4"] == m["results"][t]["model"]  # M4 reproduced exactly
    wind = m10["results"]["wind"]
    adopted = wind["test"][wind["chosen"]]["mae_mw"] < m["results"]["wind"]["model"]["mae_mw"]
    assert m10["adopted"] is adopted
    assert m["default_model"] == ("M10" if adopted else "M4")


def test_default_artifacts_match_default_model_and_demand_beats_yesterday():
    m = json.loads(METRICS.read_text())
    spec = json.loads((M.ARTIFACT_DIR / M.SPEC_FILE).read_text())
    assert spec["model_set"] == m["default_model"]
    specs = M.specs_from(spec)
    if m["default_model"] == "M4":
        assert specs == M.SPECS
        return
    for t in F.TARGETS:
        chosen = m["m10"]["results"][t]["chosen"]
        assert specs[t] == {"m4": M.SPECS[t], **M.m10_candidates(t)}[chosen]
    d = m["m10"]["results"]["demand"]
    assert d["test"][d["chosen"]]["mape_pct"] < d["test"]["yesterday"]["mape_pct"]


def test_provider_ignores_forecast_inputs_for_later_slots(local):
    from clearinghouse.data.forecast_inputs import load_forecast_inputs

    date = "2024-11-04"
    fin = load_forecast_inputs().copy()
    late = fin["ts_cst"] >= pd.Timestamp("2024-11-05 00:00")
    cols = list(F.EXOG_SERIES)
    fin.loc[late, cols] = fin.loc[late, cols] * 3.0 + 1234.0
    a = local.get_forecast("ERCO", date)
    b = LocalForecastProvider(forecast_inputs=fin).get_forecast("ERCO", date)
    for name in HOURLY_FIELDS:
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name))


# --- (c) provider switch ---------------------------------------------------------------------


@pytest.fixture
def no_chalk_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "nonexistent.env")
    for name in config.CHALK_KEYS:
        monkeypatch.setenv(name, "")


def test_empty_chalk_vars_select_local_provider(no_chalk_env):
    assert config.chalk_configured() is False
    p = get_provider()
    assert isinstance(p, LocalForecastProvider)
    assert p.source == "local"
    r = p.get_forecast("ERCOT", "2024-10-14")
    assert r.source == "local"
    assert all(getattr(r, f).shape == (24,) for f in HOURLY_FIELDS)
    assert not hasattr(r, "avg_intensity")


def test_all_chalk_vars_select_chalk_provider(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "nonexistent.env")
    for name in config.CHALK_KEYS:
        monkeypatch.setenv(name, f"FAKE_{name}_q7")
    p = get_provider()  # constructing it makes no request
    assert isinstance(p, FallbackProvider) and p.source == "chalk"
    assert isinstance(p.primary, ChalkForecastProvider)
    assert isinstance(p.fallback, LocalForecastProvider)
    assert get_provider() is p  # cached: the OAuth token and connections are reused
    assert "FAKE_" not in repr(p)


# --- (d) latency + cache ---------------------------------------------------------------------


def test_one_day_forecast_under_500ms_then_cached():
    p = LocalForecastProvider()  # model + data load happen here, outside the timed call
    t0 = time.perf_counter()
    r1 = p.get_forecast("ERCO", "2024-12-02")
    first_ms = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    r2 = p.get_forecast("ERCO", "2024-12-02")
    second_ms = (time.perf_counter() - t0) * 1000
    assert first_ms < 500, first_ms
    assert r1.latency_ms < 500
    assert second_ms < 5 and second_ms < first_ms / 10, (first_ms, second_ms)
    assert r2.demand_mw is r1.demand_mw  # served from the per-date cache
    with pytest.raises(ValueError):
        r2.demand_mw[0] = 0.0  # cached arrays are read-only


def test_planning_inputs_rules(local, grid):
    date = "2024-10-14"
    r = local.get_forecast("ERCO", date)
    d = grid.day_index(date)
    prev = slice((d - 1) * 24, d * 24)
    np.testing.assert_array_equal(r.nuclear_mw, grid.series["nuclear_mw"][prev])
    np.testing.assert_array_equal(r.net_imports_mw, -grid.series["interchange_mw"][prev])
    ren = r.wind_mw + r.solar_mw
    assert (r.curtailed_mw >= 0).all() and (r.curtailed_mw <= ren + 1e-9).all()
    assert (r.wind_mw >= 0).all() and (r.solar_mw >= 0).all()
    assert r.as_of == "2024-10-14T00:00:00-06:00"
    np.testing.assert_allclose(r.fixed_supply_mw - r.fixed_nonimport_mw, r.net_imports_mw)


def test_local_bid_features(local):
    bid = {"id": "b1", "energy_mwh": 400.0, "earliest_hour": 8, "deadline_hour": 18,
           "max_mw": 100.0}
    f = local.get_bid_features(bid, "2024-10-14")
    assert tuple(f) == BID_FEATURE_NAMES
    assert f["window_hours"] == 10 and f["min_run_hours"] == 4.0
    assert f["flexibility_ratio"] == 2.5 and f["feasible"] is True
    assert 8 <= f["window_lowest_net_load_hour"] < 18
    assert local.get_bid_features(bid)["energy_share_of_day_demand"] is None


def test_unknown_region_and_too_early_date_rejected(local):
    with pytest.raises(ValueError):
        local.get_forecast("CAISO", "2024-10-14")
    with pytest.raises(ValueError):
        local.get_forecast("ERCO", "2023-12-05")


# --- (e) Chalk request construction (mock transport, never the network) ----------------------


def _chalk_mock(log: list[httpx.Request], query_status: int = 200, errors=None):
    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={
                "access_token": "tok-123", "token_type": "Bearer", "expires_in": 3600,
                "api_server": "https://api.chalk.ai", "primary_environment": "env-primary",
                "engines": {"env-abc": "https://engine-abc.chalk.test"},
                "environment_id_to_name": {"env-abc": "prod", "env-primary": "dev"}})
        if request.url.path == "/v1/query/online":
            body = json.loads(request.content)
            data = []
            for fqn in body["outputs"]:
                name = fqn.split(".", 1)[1]
                if fqn == "grid_day.as_of":
                    value = "2024-10-13T10:00:00-06:00"
                elif fqn.startswith("grid_day."):
                    value = [float(i) for i in range(24)]
                else:
                    value = 1.0 if name != "feasible" else True
                data.append({"field": fqn, "value": value, "error": None})
            return httpx.Response(query_status, json={
                "data": data, "errors": errors, "meta": {"execution_duration_s": 0.012}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_chalk_forecast_request_construction():
    log: list[httpx.Request] = []
    p = ChalkForecastProvider("cid-FAKE", "secret-FAKE", "prod", transport=_chalk_mock(log))
    r = p.get_forecast("ERCOT", "2024-10-14")

    tok, q = log
    assert tok.method == "POST" and str(tok.url) == "https://api.chalk.ai/v1/oauth/token"
    assert json.loads(tok.content) == {"client_id": "cid-FAKE", "client_secret": "secret-FAKE",
                                       "grant_type": "client_credentials"}
    # environment name "prod" mapped to its id; query goes to that environment's engine
    assert str(q.url) == "https://engine-abc.chalk.test/v1/query/online"
    assert q.headers["Authorization"] == "Bearer tok-123"
    assert q.headers["X-Chalk-Env-Id"] == "env-abc"
    assert q.headers["X-Chalk-Deployment-Type"] == "engine"
    body = json.loads(q.content)
    assert body["inputs"] == {"grid_day.id": "ERCO:2024-10-14"}
    assert set(body["outputs"]) == {f"grid_day.{n}" for n in (*HOURLY_FIELDS, "as_of")}
    assert body["include_meta"] is True

    assert r.source == "chalk" and r.date == "2024-10-14" and r.latency_ms >= 0
    np.testing.assert_array_equal(r.demand_mw, np.arange(24, dtype=float))
    assert r.methods["chalk_execution_ms"] == "12.0"

    # token is reused for the next query; bid features use flex_bid.* inputs/outputs
    f = p.get_bid_features({"id": "b1", "energy_mwh": 400.0, "earliest_hour": 8,
                            "deadline_hour": 18, "max_mw": 100.0}, "2024-10-14")
    assert [x.url.path for x in log].count("/v1/oauth/token") == 1
    bq = json.loads(log[-1].content)
    assert bq["inputs"] == {"flex_bid.id": "b1", "flex_bid.energy_mwh": 400.0,
                            "flex_bid.earliest_hour": 8, "flex_bid.deadline_hour": 18,
                            "flex_bid.max_mw": 100.0, "flex_bid.grid_day_id": "ERCO:2024-10-14"}
    assert bq["outputs"] == [f"flex_bid.{n}" for n in BID_FEATURE_NAMES]
    assert tuple(f) == BID_FEATURE_NAMES


def test_chalk_errors_raise_without_leaking_secrets():
    log: list[httpx.Request] = []
    p = ChalkForecastProvider("cid-FAKE", "secret-FAKE", "prod",
                              transport=_chalk_mock(log, errors=[{"message": "resolver failed"}]))
    with pytest.raises(ChalkError) as e:
        p.get_forecast("ERCO", "2024-10-14")
    assert "resolver failed" in str(e.value)
    p2 = ChalkForecastProvider("cid-FAKE", "secret-FAKE", "prod",
                               transport=_chalk_mock([], query_status=500))
    with pytest.raises(ChalkError) as e2:
        p2.get_forecast("ERCO", "2024-10-14")
    for text in (str(e.value), str(e2.value), repr(p)):
        assert "secret-FAKE" not in text and "cid-FAKE" not in text


# --- (f) Chalk failure falls back to the local model ---------------------------------------


def test_chalk_failure_falls_back_to_local(local):
    log: list[httpx.Request] = []
    chalk = ChalkForecastProvider("cid-FAKE", "secret-FAKE", "prod",
                                  transport=_chalk_mock(log, query_status=500))
    p = FallbackProvider(chalk, local, cooldown_s=60)
    r = p.get_forecast("ERCO", "2024-10-14")
    assert r.source == "local"
    assert r.methods["fallback_reason"].startswith("chalk ChalkError")
    assert "secret-FAKE" not in r.methods["fallback_reason"]
    np.testing.assert_array_equal(r.demand_mw, local.get_forecast("ERCO", "2024-10-14").demand_mw)
    # within the cooldown Chalk is not called again
    n = len(log)
    r2 = p.get_forecast("ERCO", "2024-10-14")
    assert len(log) == n and r2.source == "local" and "skipped" in r2.methods["fallback_reason"]
    source, feats = p.get_bid_features_many(
        [{"id": "b1", "energy_mwh": 400.0, "earliest_hour": 8, "deadline_hour": 18,
          "max_mw": 100.0}], "2024-10-14")
    assert source == "local" and len(log) == n and tuple(feats[0]) == BID_FEATURE_NAMES


def test_chalk_ok_is_served_by_chalk(local):
    log: list[httpx.Request] = []
    p = FallbackProvider(ChalkForecastProvider("cid-FAKE", "secret-FAKE", "prod",
                                               transport=_chalk_mock(log)), local)
    r = p.get_forecast("ERCO", "2024-10-14")
    assert r.source == "chalk" and "fallback_reason" not in r.methods
    bids = [{"id": f"b{i}", "energy_mwh": 100.0, "earliest_hour": 0, "deadline_hour": 10,
             "max_mw": 50.0} for i in range(5)]
    source, feats = p.get_bid_features_many(bids, "2024-10-14")
    assert source == "chalk" and len(feats) == 5
    queried = sorted(json.loads(x.content)["inputs"]["flex_bid.id"] for x in log
                     if x.url.path == "/v1/query/online" and b"flex_bid" in x.content)
    assert queried == [b["id"] for b in bids]
    assert [x.url.path for x in log].count("/v1/oauth/token") == 1
