"""Bid extraction for ``POST /api/assistant/extract``: which path runs, and its fallbacks.

1. ``chalk_function``: the deployed Chalk Function ``extract-bid`` (``chalk_compute/``), called
   with the app's Chalk token (needs ``query.online`` + ``deploy.read``). The vision call then
   runs in Chalk's sandbox.
2. ``gateway``: if the function cannot be reached, the same extraction code runs in this
   process and calls the Chalk Model Gateway directly.
3. Neither: ``ExtractUnavailable`` (the API answers 503 with the reasons).

A bad image (wrong type, unreadable, no bid in it) is ``ExtractError`` on either path (422), not
a reason to fall back. The response always names the path that served it.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any

from clearinghouse import config
from clearinghouse.assistant.extract import ExtractError, check_image, extract_bid
from clearinghouse.assistant.gateway import Gateway, GatewayUnavailable

FUNCTION_NAME = "extract-bid"
STATUS_TTL_S = 60.0
_lock = threading.Lock()
_fn_cache: dict[str, Any] = {}


class ExtractUnavailable(RuntimeError):
    """No extraction path is available (reasons in the message)."""


def _function():
    """The deployed Chalk Function handle (cached), or raise with the reason."""
    if not config.chalk_configured():
        raise RuntimeError("CHALK_* not set")
    with _lock:
        hit = _fn_cache.get("fn")
        if hit is not None:
            return hit
    import chalkcompute  # heavy import; only when Chalk is configured

    fn = chalkcompute.RemoteFunction.from_name(FUNCTION_NAME)
    with _lock:
        _fn_cache["fn"] = fn
    return fn


def function_status() -> tuple[bool, str | None]:
    """(reachable, reason) for the status strip, cached for a minute."""
    with _lock:
        hit = _fn_cache.get("status")
        if hit and time.monotonic() - hit[0] < STATUS_TTL_S:
            return hit[1]
    try:
        _function()
        res = (True, None)
    except Exception as exc:  # any failure: report the path as unavailable, never a secret
        res = (False, f"Chalk Function {FUNCTION_NAME}: {type(exc).__name__}")
    with _lock:
        _fn_cache["status"] = (time.monotonic(), res)
    return res


def status() -> dict:
    ok, reason = function_status()
    if ok:
        return {"extract": "chalk_function", "extract_reason": None}
    if Gateway().configured:
        return {"extract": "gateway", "extract_reason": reason}
    return {"extract": "unavailable",
            "extract_reason": f"{reason}; CHALK_ROUTER_API_KEY not set"}


def extract(data: bytes, mime: str) -> dict:
    """Image -> ``{bid, path, latency_ms, fallback_reason}``."""
    check_image(data, mime)
    t0 = time.perf_counter()
    reasons: list[str] = []
    try:
        fn = _function()
        raw = fn.remote(base64.b64encode(data).decode(), mime)
        out = json.loads(raw)
        if "error" in out:
            if out["error"].startswith("Chalk Model Gateway unavailable"):
                raise ExtractUnavailable(out["error"])
            raise ExtractError(out["error"])
        return {"bid": out, "path": "chalk_function", "fallback_reason": None,
                "latency_ms": (time.perf_counter() - t0) * 1000.0}
    except ExtractError:
        raise
    except Exception as exc:  # function unreachable or failed: fall back to the gateway
        reasons.append(f"Chalk Function {FUNCTION_NAME}: {type(exc).__name__}: {exc}"[:300])
        with _lock:
            _fn_cache.pop("fn", None)
    try:
        bid = extract_bid(data, mime)
    except GatewayUnavailable as exc:
        reasons.append(f"Chalk Model Gateway: {exc}")
        raise ExtractUnavailable("; ".join(reasons)) from None
    return {"bid": bid, "path": "gateway", "fallback_reason": reasons[0] if reasons else None,
            "latency_ms": (time.perf_counter() - t0) * 1000.0}
