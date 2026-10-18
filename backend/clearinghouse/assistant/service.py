"""Assistant service: the code path behind /api/assistant/* and scripts/eval_assistant.py.

* ``chat_events``: grounding -> Chalk Model Gateway stream (tokens, then a validated
  suggestions block) or, if the gateway is unavailable, the local rule-based answer. The first
  event (``meta``) says which path served the answer; it is sent only after the first gateway
  token arrives, so a gateway failure before any text falls back cleanly.
* ``suggest``: rule-based suggestions from the grounding payload (fast enough for the
  600 ms-debounced bid edits and unable to invent numbers; the bid features in the payload come
  from a Chalk online query when Chalk is configured).
* ``status``: which path is live for the panel's credibility strip.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

from clearinghouse.assistant import local, suggestions
from clearinghouse.assistant.context import build_grounding
from clearinghouse.assistant.gateway import Gateway, GatewayUnavailable
from clearinghouse.assistant.models import ChatRequest, SuggestRequest
from clearinghouse.assistant.prompts import SUGGESTIONS_MARKER, system_prompt

HISTORY_TURNS = 10
STATUS_TTL_S = 60.0
_status_lock = threading.Lock()
_status_cache: dict[str, Any] = {}

Event = tuple[str, dict]


def _sources(payload: dict) -> dict:
    return {"forecast": payload["forecast"]["source"],
            "bid_features": payload["bid_features_source"]}


def _parse_suggestions(tail: str) -> Any:
    i, j = tail.find("{"), tail.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(tail[i:j + 1])
    except json.JSONDecodeError:
        return None


def _local_events(req: ChatRequest, payload: dict, reason: str | None) -> Iterator[Event]:
    yield "meta", {"llm": "local", "model": "rule-based", "fallback_reason": reason,
                   **_sources(payload)}
    text = local.answer(req.messages[-1].content, payload, req.persona, req.focus_field)
    for piece in re.findall(r"\S+\s*", text):
        yield "token", {"t": piece}
    yield "suggestions", {"suggestions": suggestions.rule_suggestions(payload),
                          "source": "rules"}
    yield "done", {}


def chat_events(engine: Any, req: ChatRequest, gateway: Gateway | None = None) -> Iterator[Event]:
    payload = build_grounding(engine, req.day, req.bids, req.scenario)
    gw = gateway or Gateway()
    if not gw.configured:
        yield from _local_events(req, payload, "CHALK_ROUTER_API_KEY not set")
        return
    focus = (f"\n(The user clicked the help icon of the field {req.focus_field}.)"
             if req.focus_field else "")
    messages = [{"role": "system", "content": system_prompt(payload, req.persona) + focus}]
    messages += [m.model_dump() for m in req.messages[-HISTORY_TURNS:]]
    stream = gw.chat_stream(messages)
    try:
        first = next(stream, "")
    except GatewayUnavailable as exc:
        yield from _local_events(req, payload, f"Chalk Model Gateway unavailable: {exc}")
        return
    yield "meta", {"llm": "chalk", "model": gw.model, "fallback_reason": None,
                   **_sources(payload)}
    full, sent, cut = first, 0, -1
    keep = len(SUGGESTIONS_MARKER)
    try:
        for delta in stream:
            full += delta
            cut = full.find(SUGGESTIONS_MARKER)
            safe = cut if cut >= 0 else max(sent, len(full) - keep)
            if safe > sent:
                yield "token", {"t": full[sent:safe]}
                sent = safe
    except GatewayUnavailable as exc:
        yield "error", {"message": f"The Chalk Model Gateway stopped mid-answer ({exc})."}
    cut = full.find(SUGGESTIONS_MARKER)
    end = cut if cut >= 0 else len(full)
    if end > sent:
        yield "token", {"t": full[sent:end]}
    raw = _parse_suggestions(full[cut + keep:]) if cut >= 0 else None
    llm_sugs = suggestions.validate(raw, payload)
    fixes = [s for s in suggestions.rule_suggestions(payload) if s["kind"] == "fix"]
    yield "suggestions", {"suggestions": suggestions.merge(llm_sugs, fixes),
                          "source": "chalk" if llm_sugs else "rules",
                          "dropped": max(0, len((raw or {}).get("suggestions", []) if
                                                isinstance(raw, dict) else []) - len(llm_sugs))}
    yield "done", {}


def chat(engine: Any, req: ChatRequest, gateway: Gateway | None = None) -> dict:
    """Non-streaming form of ``chat_events`` (evaluations, tests)."""
    out: dict[str, Any] = {"answer": "", "suggestions": [], "meta": {}, "errors": []}
    for event, data in chat_events(engine, req, gateway):
        if event == "meta":
            out["meta"] = data
        elif event == "token":
            out["answer"] += data["t"]
        elif event == "suggestions":
            out["suggestions"] = data["suggestions"]
            out["suggestions_source"] = data["source"]
        elif event == "error":
            out["errors"].append(data["message"])
    out["answer"] = out["answer"].strip()
    return out


def suggest(engine: Any, req: SuggestRequest) -> dict:
    t0 = time.perf_counter()
    payload = build_grounding(engine, req.day, req.bids, req.scenario)
    return {"suggestions": suggestions.rule_suggestions(payload), "source": "rules",
            **_sources(payload), "latency_ms": (time.perf_counter() - t0) * 1000.0}


def gateway_status(gateway: Gateway | None = None) -> dict:
    """``{gateway: chalk|unavailable, reason, model}``; a live ``/models`` check, cached 60 s."""
    gw = gateway or Gateway()
    if not gw.configured:
        return {"gateway": "unavailable", "reason": "CHALK_ROUTER_API_KEY not set",
                "model": None}
    key = f"{gw.host}|{gw.env_id}|{gw.model}"
    with _status_lock:
        hit = _status_cache.get(key)
        if hit and time.monotonic() - hit[0] < STATUS_TTL_S:
            return hit[1]
    try:
        models = [m.id for m in gw.client.models.list()]
        if gw.model in models:
            res = {"gateway": "chalk", "reason": None, "model": gw.model}
        else:
            res = {"gateway": "unavailable", "model": gw.model,
                   "reason": f"model {gw.model} not served (available: {len(models)})"}
    except Exception as exc:  # any failure means not usable; the reason is shown, never a secret
        res = {"gateway": "unavailable", "model": gw.model,
               "reason": f"Chalk Model Gateway check failed: {type(exc).__name__}"}
    with _status_lock:
        _status_cache[key] = (time.monotonic(), res)
    return res
