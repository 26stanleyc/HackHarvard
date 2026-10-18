"""Bid extraction from an image (a bill, load chart, charging schedule, emissions report).

One vision call through the Chalk Model Gateway, then strict validation. Used by the deployed
Chalk function ``extract_bid`` (``backend/chalk_compute/extract_bid.py``) and, if that is
unavailable, directly by the API (``/api/assistant/extract``).

The gateway's OpenAI-compatible Chat route cannot carry images to Claude ("cannot represent
messages[0].content[1] losslessly", HTTP 400, 2026-10-18), so this uses the gateway's native
Messages route (``{host}/v1/router/v1/messages``, Bearer router key, ``X-Chalk-Env-Id``). The one
POST uses the standard library (``urllib``): no new dependency, and nothing extra to install in
the Chalk Function's image (an ``httpx`` pin there was not picked up by the image build).
"""

from __future__ import annotations

import base64
import json
import math
import re
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from clearinghouse.assistant.gateway import Gateway, GatewayUnavailable

MIME_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
MAX_IMAGE_BYTES = 5 * 1024 * 1024
TIMEOUT_S = 60.0
MAX_TOKENS = 4000

PROMPT = """You read one document image (a utility bill, an hourly load chart, a charging
schedule, a job scheduler screen, or an emissions report) and extract ONE flexible-load bid
for a single day for sage.ai.

Return only a JSON object, no other text:
{"earliest_hour": int 0-23, "deadline_hour": int 1-24, "energy_mwh": number,
 "max_mw": number, "confidence": number 0-1, "notes": "one or two sentences"}

Rules:
- energy_mwh is the FLEXIBLE energy needed PER DAY in MWh. Convert kWh to MWh (divide by
  1,000). If the document gives a billing-period total, divide by the number of days (or
  workdays) the flexible load runs in that period. If it gives power x hours, multiply.
- max_mw is the most power the flexible load can draw, in MW (kW / 1,000). Use charger or
  equipment capacity or the stated limit; a 15-minute peak demand on a bill also works.
- earliest_hour is when the flexible load may start; deadline_hour is the hour by which it
  must be done (exclusive: done by 06:00 -> 6; midnight -> 24). Hours are local clock hours.
- If the window crosses midnight, keep the part after midnight if the energy fits there,
  otherwise the part before, and say so in notes. Windows cannot cross midnight.
- Ignore loads the document marks as not flexible.
- confidence: how sure you are that the four numbers are right (lower it for guesses).
- Never invent a number the document does not support; if something is missing, give your best
  estimate, lower confidence, and say what you assumed in notes."""


class ExtractError(ValueError):
    """The model's reply could not be turned into a valid bid."""


class ExtractedBid(BaseModel):
    earliest_hour: int = Field(ge=0, le=23)
    deadline_hour: int = Field(ge=1, le=24)
    energy_mwh: float = Field(ge=0)
    max_mw: float = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    notes: str = ""


def _json_object(text: str) -> dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ExtractError("no JSON object in the model's reply")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ExtractError(f"model reply is not valid JSON: {exc.msg}") from None
    if not isinstance(obj, dict):
        raise ExtractError("model reply is not a JSON object")
    return obj


def parse_bid(text: str) -> dict[str, Any]:
    """Model text -> validated bid dict, clamped to the bid schema's limits."""
    obj = _json_object(text)
    for k in ("earliest_hour", "deadline_hour"):
        if isinstance(obj.get(k), float) and obj[k].is_integer():
            obj[k] = int(obj[k])
    for k in ("energy_mwh", "max_mw", "confidence"):
        v = obj.get(k)
        if isinstance(v, int | float) and not math.isfinite(v):
            raise ExtractError(f"{k} is not finite")
    if isinstance(obj.get("confidence"), int | float):
        obj["confidence"] = min(1.0, max(0.0, float(obj["confidence"])))
    obj["notes"] = str(obj.get("notes", ""))[:500]
    try:
        bid = ExtractedBid.model_validate(obj)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ExtractError(f"{'.'.join(map(str, first['loc']))}: {first['msg']}") from None
    out = bid.model_dump()
    out["feasible"] = (out["earliest_hour"] < out["deadline_hour"] and out["energy_mwh"]
                       <= out["max_mw"] * (out["deadline_hour"] - out["earliest_hour"]) + 1e-6)
    return out


def check_image(data: bytes, mime: str) -> None:
    if mime not in MIME_TYPES:
        raise ExtractError(f"unsupported file type {mime!r}; use PNG, JPEG, WebP or GIF "
                           "(screenshot a PDF page first)")
    if not data:
        raise ExtractError("empty file")
    if len(data) > MAX_IMAGE_BYTES:
        raise ExtractError(f"image is {len(data) / 1e6:.1f} MB; the limit is 5 MB")


def extract_bid(data: bytes, mime: str, gateway: Gateway | None = None) -> dict[str, Any]:
    """Image -> validated bid (raises ``GatewayUnavailable`` or ``ExtractError``)."""
    check_image(data, mime)
    gw = gateway or Gateway()
    if not gw.configured:
        raise GatewayUnavailable("CHALK_ROUTER_API_KEY or CHALK_ENVIRONMENT not set")
    body = {
        "model": gw.vision_model.split("/", 1)[-1],
        "max_tokens": MAX_TOKENS,
        "output_config": {"effort": "low"},
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.b64encode(data).decode()}},
            {"type": "text", "text": PROMPT},
        ]}],
    }
    req = urllib.request.Request(
        f"{gw.host}/v1/router/v1/messages", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {gw.api_key}", "X-Chalk-Env-Id": gw.env_id,
                 "anthropic-version": "2023-06-01", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            reply = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise GatewayUnavailable(f"HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise GatewayUnavailable(type(exc).__name__) from None
    if reply.get("stop_reason") == "refusal":
        raise ExtractError("the model declined to read this image")
    text = "".join(b.get("text", "") for b in reply.get("content", []) if b.get("type") == "text")
    out = parse_bid(text)
    out["model"] = reply.get("model") or body["model"]
    return out
