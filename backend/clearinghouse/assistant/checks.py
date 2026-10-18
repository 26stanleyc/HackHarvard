"""Grounding checks shared by the tests and the evaluation (Phase 4 ``numbers_grounded``).

A number in an answer is grounded if it is within 1% of a number in the grounding payload (or
in the extra allowed values, e.g. the user's question). Clock times ("06:00") and bare integers
0-24 count as hours or small counts and are always allowed: they are positions in the day, not
measurements.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

NUM_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
REL_TOL = 0.01


def numbers_in_text(text: str) -> list[float]:
    text = CLOCK_RE.sub(" ", text)
    out = []
    for m in NUM_RE.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return out


def payload_numbers(x: Any) -> list[float]:
    out: list[float] = []
    if isinstance(x, bool) or x is None:
        return out
    if isinstance(x, int | float):
        return [float(x)]
    if isinstance(x, str):
        return numbers_in_text(x)
    if isinstance(x, dict):
        for v in x.values():
            out += payload_numbers(v)
    elif isinstance(x, list | tuple):
        for v in x:
            out += payload_numbers(v)
    return out


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(REL_TOL * max(abs(a), abs(b)), 1e-9) or round(a, 1) == round(b, 1)


def ungrounded_numbers(answer: str, payload: Any, extra: Iterable[float] = ()) -> list[float]:
    allowed = payload_numbers(payload) + [float(v) for v in extra]
    allowed_pct = [100 * v for v in allowed if 0 < abs(v) <= 1]  # shares quoted as percents
    bad = []
    for n in numbers_in_text(answer):
        if n == int(n) and 0 <= n <= 24:
            continue
        if any(_close(n, a) for a in allowed) or any(_close(n, a) for a in allowed_pct):
            continue
        bad.append(n)
    return bad


def numbers_grounded(answer: str, payload: Any, extra: Iterable[float] = ()) -> float:
    """1.0 if every number is grounded, else the grounded share."""
    nums = numbers_in_text(answer)
    nums = [n for n in nums if not (n == int(n) and 0 <= n <= 24)]
    if not nums:
        return 1.0
    return 1.0 - len(ungrounded_numbers(answer, payload, extra)) / len(nums)
