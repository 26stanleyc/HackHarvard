"""Coordinator Guide (assistant) checks: grounding, rule suggestions, LLM-output validation,
gateway fallback, local answers grounded, and the /api/assistant/* routes. The Model Gateway is
never called: a fake gateway stands in for the streaming path."""

from __future__ import annotations

import json
import time
import warnings

import numpy as np
import pytest

from clearinghouse.api import app, default_day, get_engine
from clearinghouse.assistant import local, service, suggestions
from clearinghouse.assistant.checks import numbers_grounded, ungrounded_numbers
from clearinghouse.assistant.context import build_grounding, rank_hours
from clearinghouse.assistant.gateway import GatewayUnavailable
from clearinghouse.assistant.glossary import GLOSSARY
from clearinghouse.assistant.models import AssistantBid, ChatRequest, SuggestRequest
from clearinghouse.assistant.prompts import SUGGESTIONS_MARKER, system_prompt
from clearinghouse.presets import preset_bids

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

DAY = "2024-08-20"


def bids(**patch) -> list[AssistantBid]:
    out = [AssistantBid(**b.model_dump()) for b in preset_bids()[:3]]
    for bid_id, fields in patch.items():
        i = next(k for k, b in enumerate(out) if b.id == bid_id)
        out[i] = out[i].model_copy(update=fields)
    return out


@pytest.fixture(scope="module")
def engine():
    return get_engine()


@pytest.fixture(scope="module")
def payload(engine):
    return build_grounding(engine, DAY, bids())


# --- grounding ---------------------------------------------------------------------------------


def test_grounding_comes_from_provider_and_simulator(engine, payload):
    fc, _ = engine.forecast(DAY, fresh=False)
    plan, intensity, _ = engine.plan_sim(fc)
    assert payload["forecast"]["source"] == fc.source
    assert payload["forecast"]["demand_mw"] == [round(float(x)) for x in fc.demand_mw]
    mr = plan.marginal_rate(np.zeros(24))
    np.testing.assert_allclose(payload["marginal_co2_t_per_mwh"], mr, atol=5e-4)
    clean, dirty = rank_hours(mr, np.asarray(intensity))
    assert payload["cleanest_hours"] == clean and payload["dirtiest_hours"] == dirty
    assert len(clean) == len(dirty) == 4 and not set(clean) & set(dirty)
    assert max(mr[h] for h in clean) <= min(mr[h] for h in dirty)
    assert payload["results"] is None
    assert payload["bid_features_source"] in ("chalk", "local")
    for row in payload["bids"]:
        assert row["feasible"] and row["slack_hours"] is not None


def test_infeasible_and_malformed_bids_are_described_not_rejected(engine):
    p = build_grounding(engine, DAY, bids(b1={"deadline_hour": 2, "earliest_hour": 20},
                                          b2={"energy_mwh": None}))
    rows = {r["id"]: r for r in p["bids"]}
    assert not rows["b1"]["feasible"] and "cannot cross midnight" in rows["b1"]["problem"]
    assert not rows["b2"]["feasible"] and rows["b2"]["problem"] == "energy_mwh must be a number"
    assert rows["b3"]["feasible"]


# --- rule suggestions ------------------------------------------------------------------------


def test_window_fix_suggestion(engine):
    b = bids(b1={"earliest_hour": 20, "deadline_hour": 18})[0]
    p = build_grounding(engine, DAY, [b])
    (s,) = suggestions.rule_suggestions(p)
    need = int(np.ceil(b.energy_mwh / b.max_mw))
    assert s["kind"] == "fix" and s["bid_id"] == "b1"
    if 20 + need <= 24:
        assert (s["field"], s["from"], s["to"]) == ("deadline_hour", 18, 20 + need)
    else:
        assert s["field"] == "earliest_hour" and s["to"] == 18 - need


def test_capacity_fix_suggestion_makes_bid_feasible(engine):
    b = AssistantBid(id="x", name="X", load_type="ev_fleet", energy_mwh=100, earliest_hour=0,
                     deadline_hour=4, max_mw=10)
    p = build_grounding(engine, DAY, [b])
    (s,) = suggestions.rule_suggestions(p)
    assert (s["field"], s["from"], s["to"]) == ("deadline_hour", 4, 10)
    b2 = AssistantBid(id="y", name="Y", load_type="ev_fleet", energy_mwh=100, earliest_hour=20,
                      deadline_hour=24, max_mw=10)
    (s2,) = suggestions.rule_suggestions(build_grounding(engine, DAY, [b2]))
    assert (s2["field"], s2["from"], s2["to"]) == ("max_mw", 10, 25)


def test_rule_suggestion_reasons_are_grounded(engine):
    p = build_grounding(engine, DAY, bids(b1={"earliest_hour": 20, "deadline_hour": 18},
                                          b2={"max_mw": 1.0}))
    for s in suggestions.rule_suggestions(p):
        assert ungrounded_numbers(s["reason"], p, extra=[s["to"]]) == [], s


def test_validate_drops_bad_llm_suggestions(payload):
    b = payload["bids"][0]
    raw = {"suggestions": [
        {"bid_id": b["id"], "field": "max_mw", "from": b["max_mw"], "to": b["max_mw"] * 2,
         "reason": "ok"},
        {"bid_id": "nope", "field": "max_mw", "from": 1, "to": 2},              # unknown bid
        {"bid_id": b["id"], "field": "name", "from": "a", "to": "b"},           # not editable
        {"bid_id": b["id"], "field": "max_mw", "from": 12345, "to": 1},          # wrong "from"
        {"bid_id": b["id"], "field": "deadline_hour", "from": b["deadline_hour"],
         "to": b["earliest_hour"]},                                               # breaks window
        {"bid_id": b["id"], "field": "max_mw", "from": b["max_mw"], "to": 0},    # infeasible
    ]}
    out = suggestions.validate(raw, payload)
    assert len(out) == 1 and out[0]["to"] == b["max_mw"] * 2
    assert suggestions.validate("garbage", payload) == []


# --- local fallback ------------------------------------------------------------------------


@pytest.mark.parametrize("q", [
    "What is max MW?", "Is my window feasible?", "Which hours are cleanest today?",
    "What does the forecast say about wind?", "How many tonnes will I save?", "hello",
    *[f"What does {f} mean?" for f in GLOSSARY],
])
@pytest.mark.parametrize("persona", ["ev_fleet", "data_center", "facilities"])
def test_local_answers_cite_only_grounded_numbers(payload, q, persona):
    a = local.answer(q, payload, persona)
    assert a and len(a.split()) <= 160
    assert numbers_grounded(a, payload) == 1.0, ungrounded_numbers(a, payload)


def test_local_never_claims_savings(payload):
    a = local.answer("How many tonnes will my fleet avoid?", payload, "ev_fleet").lower()
    assert "run the clearinghouse" in a and "will avoid" not in a


def test_local_field_answer_mentions_current_values(payload):
    a = local.answer("What is max MW?", payload, "ev_fleet")
    for b in payload["bids"]:
        assert f"{b['max_mw']:g} MW" in a


# --- streaming path with a fake gateway --------------------------------------------------------


class FakeGateway:
    configured = True
    model = "anthropic/fake"

    def __init__(self, chunks=None, fail_at=None):
        self.chunks, self.fail_at, self.messages = chunks or [], fail_at, None

    def chat_stream(self, messages, **_):
        self.messages = messages
        for i, c in enumerate(self.chunks):
            if i == self.fail_at:
                raise GatewayUnavailable("APIConnectionError")
            yield c
        if self.fail_at is not None and self.fail_at >= len(self.chunks):
            raise GatewayUnavailable("APIConnectionError")


def chat_req(**kw) -> ChatRequest:
    return ChatRequest(day=DAY, bids=bids(), messages=[{"role": "user", "content": "max MW?"}],
                       **kw)


def test_gateway_stream_strips_marker_and_validates(engine, payload):
    b = payload["bids"][0]
    block = json.dumps({"suggestions": [
        {"bid_id": b["id"], "field": "max_mw", "from": b["max_mw"], "to": b["max_mw"] + 1,
         "reason": "more room"},
        {"bid_id": "ghost", "field": "max_mw", "from": 1, "to": 2, "reason": "x"}]})
    text = f"Max MW is the most your equipment draws. {SUGGESTIONS_MARKER} {block}"
    chunks = [text[i:i + 7] for i in range(0, len(text), 7)]
    gw = FakeGateway(chunks)
    out = service.chat(engine, chat_req(persona="data_center"), gw)
    assert out["meta"]["llm"] == "chalk" and out["meta"]["model"] == "anthropic/fake"
    assert out["answer"] == "Max MW is the most your equipment draws."
    assert SUGGESTIONS_MARKER not in out["answer"]
    assert [s["bid_id"] for s in out["suggestions"]] == [b["id"]]
    assert out["suggestions_source"] == "chalk"
    sys = gw.messages[0]["content"]
    assert "Data-center ops lead" in sys
    assert json.dumps(payload["cleanest_hours"], separators=(",", ":")) in sys


def test_gateway_failure_before_first_token_falls_back(engine):
    out = service.chat(engine, chat_req(), FakeGateway(["x"], fail_at=0))
    assert out["meta"]["llm"] == "local"
    assert "Gateway unavailable" in out["meta"]["fallback_reason"]
    assert out["answer"]


def test_gateway_failure_mid_stream_reports_error(engine):
    out = service.chat(engine, chat_req(), FakeGateway(["Hello ", "there "], fail_at=2))
    assert out["meta"]["llm"] == "chalk" and out["errors"]
    assert out["answer"].startswith("Hello")


def test_system_prompt_carries_rules_and_grounding(payload):
    s = system_prompt(payload, "facilities")
    assert "Only cite numbers that appear in GROUNDING" in s
    assert SUGGESTIONS_MARKER in s and "Facilities manager" in s
    assert json.dumps(payload["forecast"]["demand_mw"], separators=(",", ":")) in s


# --- API -----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for frame in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_status_without_router_key(client, monkeypatch):
    monkeypatch.setenv("CHALK_ROUTER_API_KEY", "")
    r = client.get("/api/assistant/status").json()
    assert r["gateway"] == "unavailable" and "not set" in r["gateway_reason"]
    assert r["forecast"] in ("chalk", "local") and r["extract"] in ("unavailable", "local",
                                                                   "chalk_function")


def test_chat_route_streams_sse(client, monkeypatch):
    monkeypatch.setenv("CHALK_ROUTER_API_KEY", "")
    body = {"day": default_day(), "bids": [b.model_dump() for b in bids()],
            "messages": [{"role": "user", "content": "What is max MW?"}], "persona": "ev_fleet"}
    r = client.post("/api/assistant/chat", json=body)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = _events(r.text)
    assert ev[0][0] == "meta" and ev[0][1]["llm"] == "local"
    assert [e for e, _ in ev][-2:] == ["suggestions", "done"]
    assert "Max power" in "".join(d["t"] for e, d in ev if e == "token")


def test_suggest_route_fixes_reversed_window_quickly(client):
    b = [x.model_dump() for x in bids(b1={"earliest_hour": 20, "deadline_hour": 18})]
    body = {"day": DAY, "bids": b, "persona": "ev_fleet"}
    client.post("/api/assistant/suggest", json=body)  # warm the day
    t0 = time.perf_counter()
    r = client.post("/api/assistant/suggest", json=body)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    s = r.json()["suggestions"][0]
    assert s["bid_id"] == "b1" and s["kind"] == "fix"
    assert elapsed < 1.0, elapsed


def test_assistant_routes_reject_unknown_day(client):
    r = client.post("/api/assistant/suggest", json={"day": "1999-01-01", "bids": []})
    assert r.status_code == 422
    SuggestRequest(day=DAY)  # empty bids are allowed


def test_flat_marginal_day_gives_no_timing_advice(engine):
    """2024-03-29 (windy night): if every hour's marginal rate is equal, no "improve" moves and
    the answer says timing does not matter instead of naming arbitrary clean/dirty hours."""
    p = build_grounding(engine, "2024-03-29", bids())
    if not p["marginal_rates_all_equal"]:
        pytest.skip("forecast for this day has distinct marginal rates")
    assert [s for s in suggestions.rule_suggestions(p) if s["kind"] == "improve"] == []
    a = local.answer("Which hours are cleanest today?", p, "ev_fleet")
    assert "does not change emissions" in a and "dirtiest" not in a
    assert numbers_grounded(a, p) == 1.0


def test_improve_only_when_it_lowers_the_rate(payload):
    rates = payload["marginal_co2_t_per_mwh"]
    for s in suggestions.rule_suggestions(payload):
        if s["kind"] != "improve":
            continue
        b = next(x for x in payload["bids"] if x["id"] == s["bid_id"])
        lo, hi = b["earliest_hour"], b["deadline_hour"]
        lo2, hi2 = (s["to"], hi) if s["field"] == "earliest_hour" else (lo, s["to"])
        assert min(rates[lo2:hi2]) < min(rates[lo:hi])
