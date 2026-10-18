"""Bid edit suggestions: rule-based from the grounding payload, plus validation of LLM ones.

Rules (in priority order, at most ``MAX_SUGGESTIONS``):

1. ``fix`` window: deadline at or before the start -> move the deadline to start + the minimum
   run hours (or the start back if that passes 24:00).
2. ``fix`` capacity: energy above max MW x window -> extend the deadline to fit at max power, or
   if that passes 24:00, raise max MW to energy / window.
3. ``improve`` no clean hours: a feasible window holding none of the day's cleanest hours (the
   4 cleanest, or every hour tied at the lowest rate), and whose best hour has a higher marginal
   rate than they do -> extend the window to the nearest one (marked "if your operations
   allow"). Never fires when timing cannot lower emissions.

Every suggestion is ``{bid_id, field, from, to, reason, kind}``. Numbers in reasons come from the
payload only.
"""

from __future__ import annotations

import math
from typing import Any

from clearinghouse.assistant.glossary import BID_FIELDS
from clearinghouse.schemas import FEASIBILITY_TOL_MWH, HOURS

MAX_SUGGESTIONS = 3


def _hh(h: int) -> str:
    return f"{h:02d}:00"


def _ceil1(x: float) -> float:
    return math.ceil(x * 10 - 1e-9) / 10


def _min_hours(b: dict) -> int | None:
    if not b.get("max_mw"):
        return None
    return max(1, math.ceil(b["energy_mwh"] / b["max_mw"] - 1e-9))


def rule_suggestions(payload: dict) -> list[dict]:
    out: list[dict] = []
    fixes: list[dict] = []
    improves: list[dict] = []
    cleanest = payload["cleanest_hours"]
    for b in payload["bids"]:
        if b.get("window_hours") is None:  # malformed (missing numbers): nothing grounded to say
            continue
        lo, hi = b["earliest_hour"], b["deadline_hour"]
        need = _min_hours(b)
        if lo >= hi:
            if need is None:
                continue
            if lo + need <= HOURS:
                fixes.append(dict(bid_id=b["id"], field="deadline_hour", **{"from": hi},
                                  to=lo + need, kind="fix", reason=(
                    f"The deadline ({_hh(hi)}) is not after the start ({_hh(lo)}); windows "
                    f"cannot cross midnight. At {b['max_mw']:g} MW, {b['energy_mwh']:g} MWh "
                    f"needs {need} h, so the earliest valid deadline is {_hh(lo + need)}.")))
            else:
                fixes.append(dict(bid_id=b["id"], field="earliest_hour", **{"from": lo},
                                  to=max(0, hi - need), kind="fix", reason=(
                    f"The start ({_hh(lo)}) is not before the deadline ({_hh(hi)}). At "
                    f"{b['max_mw']:g} MW the bid needs {need} h, so start at "
                    f"{_hh(max(0, hi - need))} at the latest.")))
            continue
        window = hi - lo
        if b["energy_mwh"] > b["max_mw"] * window + FEASIBILITY_TOL_MWH:
            if need is not None and lo + need <= HOURS:
                fixes.append(dict(bid_id=b["id"], field="deadline_hour", **{"from": hi},
                                  to=lo + need, kind="fix", reason=(
                    f"{b['energy_mwh']:g} MWh does not fit in {window} h at {b['max_mw']:g} MW "
                    f"({b['max_mw'] * window:g} MWh). A deadline of {_hh(lo + need)} gives the "
                    f"{need} h it needs at max power.")))
            else:
                mw = _ceil1(b["energy_mwh"] / window)
                fixes.append(dict(bid_id=b["id"], field="max_mw", **{"from": b["max_mw"]},
                                  to=mw, kind="fix", reason=(
                    f"{b['energy_mwh']:g} MWh in a {window} h window needs at least {mw:g} MW "
                    f"(now {b['max_mw']:g} MW), if your equipment can draw that.")))
            continue
        rates = payload["marginal_co2_t_per_mwh"]
        best_in_window = min(rates[lo:hi])
        if (b.get("cleanest_hours_in_window") == [] and cleanest
                and best_in_window > min(rates[c] for c in cleanest) + 1e-6):
            after = [c for c in cleanest if c >= hi]
            before = [c for c in cleanest if c < lo]
            cand = []
            if after:
                cand.append((after[0] + 1 - hi, "deadline_hour", hi, after[0] + 1, after[0]))
            if before:
                cand.append((lo - before[-1], "earliest_hour", lo, before[-1], before[-1]))
            if cand:
                _, field, frm, to, c = min(cand)
                hours = ", ".join(_hh(h) for h in cleanest)
                what = "deadline" if field == "deadline_hour" else "start"
                improves.append(dict(bid_id=b["id"], field=field, **{"from": frm}, to=to,
                                     kind="improve", reason=(
                    f"Your window misses all {len(cleanest)} of today's cleanest hours "
                    f"({hours}). With a {_hh(to)} {what}, this bid can also run "
                    f"{_hh(c)}-{_hh(c + 1)}, one of those clean hours. Apply only if your "
                    "operations allow it.")))
    out = fixes + improves
    return out[:MAX_SUGGESTIONS]


def _apply(b: dict, field: str, to: Any) -> dict:
    nb = dict(b)
    nb[field] = to
    return nb


def validate(raw: Any, payload: dict) -> list[dict]:
    """Keep only LLM suggestions that name a real bid and editable field, start from the bid's
    current value, and leave the bid well-formed and feasible."""
    items = raw.get("suggestions") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    bids = {b["id"]: b for b in payload["bids"]}
    out: list[dict] = []
    for s in items:
        if not isinstance(s, dict):
            continue
        b, field = bids.get(s.get("bid_id")), s.get("field")
        if b is None or field not in BID_FIELDS:
            continue
        frm, to = s.get("from"), s.get("to")
        if not isinstance(to, int | float) or isinstance(to, bool) or not math.isfinite(to):
            continue
        cur = b.get(field)
        if cur is None or not isinstance(frm, int | float) or abs(float(frm) - float(cur)) > 1e-6:
            continue
        if field in ("earliest_hour", "deadline_hour"):
            if to != int(to):
                continue
            to = int(to)
        nb = _apply(b, field, to)
        if not (0 <= nb["earliest_hour"] < nb["deadline_hour"] <= HOURS):
            continue
        if nb["energy_mwh"] < 0 or nb["max_mw"] < 0:
            continue
        if nb["energy_mwh"] > nb["max_mw"] * (nb["deadline_hour"] - nb["earliest_hour"]) \
                + FEASIBILITY_TOL_MWH:
            continue
        out.append({"bid_id": b["id"], "field": field, "from": cur, "to": to,
                    "reason": str(s.get("reason", ""))[:400],
                    "kind": "fix" if not b["feasible"] else "improve"})
    return out[:MAX_SUGGESTIONS]


def merge(primary: list[dict], extra: list[dict]) -> list[dict]:
    """``primary`` first, then ``extra`` entries for (bid, field) pairs not already covered."""
    seen = {(s["bid_id"], s["field"]) for s in primary}
    return (primary + [s for s in extra if (s["bid_id"], s["field"]) not in seen])[:MAX_SUGGESTIONS]
