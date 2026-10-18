"""Bid extraction from images (Phase 3): parsing/validation, the Chalk Function -> Model Gateway
fallback, and the API route. No network: the function and the gateway call are faked."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

from clearinghouse.api import app
from clearinghouse.assistant import extract_service
from clearinghouse.assistant.extract import ExtractError, check_image, parse_bid
from clearinghouse.assistant.gateway import GatewayUnavailable

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "extract"
PNG = (FIXTURES / "bill_01.png").read_bytes()
GOOD = {"earliest_hour": 0, "deadline_hour": 6, "energy_mwh": 7.332, "max_mw": 1.6,
        "confidence": 0.8, "notes": "94% of 234,000 kWh / 30 days"}


def test_parse_bid_accepts_json_in_prose_and_marks_feasibility():
    out = parse_bid("Here you go:\n```json\n" + json.dumps(GOOD) + "\n```")
    assert out["energy_mwh"] == 7.332 and out["feasible"] is True
    out = parse_bid(json.dumps({**GOOD, "energy_mwh": 100.0}))
    assert out["feasible"] is False  # 100 MWh cannot fit in 6 h at 1.6 MW
    assert parse_bid(json.dumps({**GOOD, "confidence": 3}))["confidence"] == 1.0
    assert parse_bid(json.dumps({**GOOD, "earliest_hour": 2.0}))["earliest_hour"] == 2


@pytest.mark.parametrize("bad", [
    "no json here", "[1, 2]", json.dumps({**GOOD, "deadline_hour": 25}),
    json.dumps({**GOOD, "earliest_hour": -1}), json.dumps({**GOOD, "max_mw": -2}),
    json.dumps({k: v for k, v in GOOD.items() if k != "energy_mwh"}),
])
def test_parse_bid_rejects_invalid(bad):
    with pytest.raises(ExtractError):
        parse_bid(bad)


def test_check_image():
    check_image(PNG, "image/png")
    with pytest.raises(ExtractError, match="unsupported file type"):
        check_image(PNG, "application/pdf")
    with pytest.raises(ExtractError, match="limit is 5 MB"):
        check_image(b"x" * (5 * 1024 * 1024 + 1), "image/png")
    with pytest.raises(ExtractError, match="empty"):
        check_image(b"", "image/png")


class FakeFunction:
    def __init__(self, reply=None, raises=None):
        self.reply, self.raises, self.calls = reply, raises, 0

    def remote(self, image_b64, mime):
        self.calls += 1
        if self.raises:
            raise self.raises
        return json.dumps(self.reply)


@pytest.fixture
def no_cache():
    extract_service._fn_cache.clear()
    yield
    extract_service._fn_cache.clear()


def test_extract_uses_chalk_function(monkeypatch, no_cache):
    fn = FakeFunction(reply={**GOOD, "feasible": True})
    monkeypatch.setattr(extract_service, "_function", lambda: fn)
    out = extract_service.extract(PNG, "image/png")
    assert out["path"] == "chalk_function" and out["fallback_reason"] is None
    assert out["bid"]["energy_mwh"] == 7.332 and fn.calls == 1


def test_extract_falls_back_to_gateway(monkeypatch, no_cache):
    monkeypatch.setattr(extract_service, "_function",
                        lambda: FakeFunction(raises=ConnectionError("no route")))
    monkeypatch.setattr(extract_service, "extract_bid", lambda d, m: {**GOOD, "feasible": True})
    out = extract_service.extract(PNG, "image/png")
    assert out["path"] == "gateway" and "ConnectionError" in out["fallback_reason"]


def test_bad_image_reported_by_function_is_not_retried(monkeypatch, no_cache):
    monkeypatch.setattr(extract_service, "_function",
                        lambda: FakeFunction(reply={"error": "no JSON object in the reply"}))
    called = []
    monkeypatch.setattr(extract_service, "extract_bid", lambda d, m: called.append(1))
    with pytest.raises(ExtractError):
        extract_service.extract(PNG, "image/png")
    assert called == []


def test_both_paths_down_is_unavailable(monkeypatch, no_cache):
    monkeypatch.setattr(extract_service, "_function",
                        lambda: FakeFunction(raises=RuntimeError("CHALK_* not set")))

    def down(d, m):
        raise GatewayUnavailable("CHALK_ROUTER_API_KEY or CHALK_ENVIRONMENT not set")
    monkeypatch.setattr(extract_service, "extract_bid", down)
    with pytest.raises(extract_service.ExtractUnavailable, match="Model Gateway"):
        extract_service.extract(PNG, "image/png")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_extract_route(client, monkeypatch):
    monkeypatch.setattr(extract_service, "extract", lambda d, m: {
        "bid": {**GOOD, "feasible": True}, "path": "chalk_function", "fallback_reason": None,
        "latency_ms": 12.0})
    r = client.post("/api/assistant/extract", files={"file": ("bill.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["path"] == "chalk_function"


def test_extract_route_errors(client, monkeypatch):
    r = client.post("/api/assistant/extract",
                    files={"file": ("x.json", b"{}", "application/json")})
    assert r.status_code == 422 and "unsupported file type" in r.json()["detail"][0]["message"]

    def unavailable(d, m):
        raise extract_service.ExtractUnavailable("Chalk Model Gateway: HTTP 401")
    monkeypatch.setattr(extract_service, "extract", unavailable)
    r = client.post("/api/assistant/extract", files={"file": ("b.png", PNG, "image/png")})
    assert r.status_code == 503 and "unavailable" in r.json()["detail"].lower()


def test_fixtures_have_feasible_answer_keys():
    truth = json.loads((FIXTURES / "ground_truth.json").read_text())
    assert len(truth) == 10
    for name, t in truth.items():
        assert (FIXTURES / name).is_file(), name
        assert t["energy_mwh"] <= t["max_mw"] * (t["deadline_hour"] - t["earliest_hour"]) + 1e-9
