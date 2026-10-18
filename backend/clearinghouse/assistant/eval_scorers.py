"""Scorer logic for the assistant evaluation (``scripts/eval_assistant.py``).

Plain functions so they are unit-tested locally and wrapped as ``@chalkcompute.function``
scorers on Chalk. Each takes the task's ``output`` (a JSON string, see ``task_output``) plus
dataset columns, and returns ``(score in [0, 1] or None if not applicable, metadata)``.

* ``suggestions_json_valid``: the suggestions block the assistant produced parses as
  ``{"suggestions": [...]}``.
* ``numbers_grounded``: every number in the answer is in the grounding payload (1% tolerance),
  the question, or the "to" value of an edit the assistant proposed (validated as feasible).
  The hallucination check.
* ``suggestion_matches``: for rows with an expected edit, a returned suggestion has the expected
  bid, field and direction; for rows expecting none, no "fix" is returned.
* ``answer_correct``: per-row facts, checked on meaning rather than one wording (v2, after the
  first LLM run showed v1 matched only the rule-based answerer's phrasing): definition rows name
  the field (label, key or alias); cleanest-hours rows mention all of the simulator's cleanest
  hours in the sentence about cleanest hours or the next (any hour format, ranges expanded), or
  say timing does not matter on a flat day; feasibility rows call out each infeasible bid (by
  name or id) with a negative
  feasibility phrase (in that sentence or the one before) and do not call a feasible bid
  infeasible; savings rows give no tonne or
  dollar figure and point to the clearinghouse / simulator. A row with no infeasible bid passes
  if the answer says the bids are feasible / valid / fit and calls none infeasible (v2.1, after
  "Yes, all three bids are valid" failed v2 in rescore 10426eba).
"""

from __future__ import annotations

import json
import re
from typing import Any

from clearinghouse.assistant.checks import numbers_in_text, ungrounded_numbers
from clearinghouse.assistant.glossary import ALIASES, GLOSSARY

Score = tuple[float | None, dict]


def task_output(out: dict) -> str:
    return json.dumps({"answer": out["answer"], "suggestions": out["suggestions"],
                       "suggestions_raw": out.get("suggestions_raw"),
                       "llm": out["meta"].get("llm"), "model": out["meta"].get("model"),
                       "fallback_reason": out["meta"].get("fallback_reason")})


def _out(output: str) -> dict:
    try:
        o = json.loads(output)
        return o if isinstance(o, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def suggestions_json_valid(output: str) -> Score:
    raw = _out(output).get("suggestions_raw")
    if raw is None:
        return 0.0, {"reason": "no suggestions block"}
    try:
        block = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return 0.0, {"reason": "suggestions block is not JSON"}
    ok = isinstance(block, dict) and isinstance(block.get("suggestions"), list)
    return (1.0 if ok else 0.0), {} if ok else {"reason": "missing suggestions list"}


def numbers_grounded(output: str, grounding_json: str, question: str,
                     expected_numbers_json: str = "[]") -> Score:
    answer = _out(output).get("answer", "")
    payload = json.loads(grounding_json)
    expected = [float(x) for x in json.loads(expected_numbers_json or "[]")]
    proposed = [float(s["to"]) for s in _out(output).get("suggestions") or []
                if isinstance(s.get("to"), int | float)]
    extra = numbers_in_text(question) + expected + proposed
    bad = ungrounded_numbers(answer, payload, extra)
    nums = [n for n in numbers_in_text(answer) if not (n == int(n) and 0 <= n <= 24)]
    if not nums:
        return 1.0, {"numbers": 0}
    return 1.0 - len(bad) / len(nums), {"numbers": len(nums), "ungrounded": bad[:10]}


def suggestion_matches(output: str, expected_suggestion_json: str) -> Score:
    if not expected_suggestion_json:
        return None, {}
    exp = json.loads(expected_suggestion_json)
    sugs = _out(output).get("suggestions") or []
    if exp.get("none"):
        fixes = [s for s in sugs if s.get("kind") == "fix"]
        return (0.0 if fixes else 1.0), {"fixes": len(fixes)}
    for s in sugs:
        if s.get("bid_id") != exp["bid_id"] or s.get("field") not in exp["fields"]:
            continue
        up = s.get("to", 0) > s.get("from", 0)
        want = exp["direction"][s["field"]]
        if (want == "up") == up:
            return 1.0, {"matched": s}
    return 0.0, {"got": sugs[:3], "expected": exp}


NEGATIVE_FEASIBILITY = re.compile(
    r"\b(not|isn't|is not|aren't)\b[^.]{0,30}\bfeasible\b|\binfeasible\b|\bnot valid\b"
    r"|\binvalid\b|\b(can't|cannot|can not|doesn't|does not|won't|will not)\b[^.]{0,15}\bfit\b"
    r"|\bruns? backward|\bnot feasible\b|\bnot for\b")
SAVINGS_FIGURE = re.compile(
    r"\d[\d,.]*\s*(t|tonnes?|tons?|tco2)\b|\$\s?\d|\d[\d,.]*\s*(dollars|usd)\b", re.I)
HOUR_TOKEN = re.compile(r"\b(\d{1,2})(?::00)?\b")
HOUR_RANGE = re.compile(r"\b(\d{1,2})(?::00)?\s*(?:-|–|to|through|until)\s*(\d{1,2})(?::00)?\b")


def _hours_named(text: str) -> set[int]:
    """Hours in text, with ranges expanded ("0-9" -> 0..9, "15:00 to 24:00" -> 15..24)."""
    out = {int(h) for h in HOUR_TOKEN.findall(text) if int(h) <= 24}
    for a, b in HOUR_RANGE.findall(text):
        a, b = int(a), int(b)
        if a <= b <= 24:
            out.update(range(a, b + 1))
    return out


def _sentences(text: str) -> list[str]:
    return [x for x in re.split(r"(?<=[.!?])\s+|\n+", text) if x.strip()]


def _bid_refs(bids_json: str) -> dict[str, list[str]]:
    """bid name (lowercase) -> [name, id] as they may appear in an answer."""
    try:
        bids = json.loads(bids_json or "[]")
    except json.JSONDecodeError:
        bids = []
    return {b["name"].lower(): [b["name"].lower(), b["id"].lower()] for b in bids if b.get("name")}


def _mentions(sentence: str, refs: list[str]) -> bool:
    return any(re.search(rf"(?<![\w-]){re.escape(r)}(?![\w-])", sentence) for r in refs)


def answer_correct(output: str, expected_json: str, bids_json: str = "[]") -> Score:
    exp = json.loads(expected_json)
    a = _out(output).get("answer", "")
    al = a.lower()
    kind = exp["kind"]
    if kind == "definition":
        field = next((k for k, g in GLOSSARY.items() if g["label"] == exp["label"]), None)
        words = [exp["label"].lower()] + ([field, *ALIASES.get(field, ())] if field else [])
        ok = any(w.lower() in al for w in words)
        return (1.0 if ok else 0.0), {"label": exp["label"]}
    if kind == "cleanest":
        if exp["flat"]:
            ok = bool(re.search(r"(does not|doesn't|do not|don't|won't|will not) change "
                                r"(your )?emissions|timing (does not|doesn't) (matter|change)", al))
            return (1.0 if ok else 0.0), {"flat": True}
        ss = _sentences(al)
        clean = [" ".join(ss[i:i + 2]) for i, x in enumerate(ss) if "cleanest" in x]
        named = {h for x in clean for h in _hours_named(x)}
        missing = [h for h in exp["hours"] if h not in named]
        return 1.0 - len(missing) / len(exp["hours"]), {"missing": missing}
    if kind == "feasibility":
        refs = _bid_refs(bids_json)
        all_sents = _sentences(al)
        # A negative sentence covers the next one too ("One bid can't fit. The Night depot ...").
        sents = [" ".join(all_sents[i:i + 2]) for i, x in enumerate(all_sents)
                 if NEGATIVE_FEASIBILITY.search(x)]
        bad_refs = [refs.get(n.lower(), [n.lower()]) for n in exp["infeasible"]]
        said_bad = [n for n, r in zip(exp["infeasible"], bad_refs, strict=True)
                    if any(_mentions(x, r) for x in sents)]
        false_alarm = [n for n in exp["feasible"]
                       if any(_mentions(x, refs.get(n.lower(), [n.lower()]))
                              and not any(_mentions(x, r) for r in bad_refs) for x in sents)]
        if not exp["infeasible"]:
            ok = bool(re.search(r"\b(feasible|valid|fits?)\b", al)) and not false_alarm \
                and not sents
            return (1.0 if ok else 0.0), {"false_alarm": false_alarm}
        score = len(said_bad) / len(exp["infeasible"]) - 0.5 * bool(false_alarm)
        return max(0.0, score), {"missed": [n for n in exp["infeasible"] if n not in said_bad],
                                 "false_alarm": false_alarm}
    if kind == "savings":
        figure = SAVINGS_FIGURE.search(a)
        ok = not figure and ("clearinghouse" in al or "simulator" in al)
        return (1.0 if ok else 0.0), {"figure": figure.group(0) if figure else None}
    return None, {"unknown kind": kind}


SCORERS = ("suggestions_json_valid", "numbers_grounded", "suggestion_matches", "answer_correct")


def score_row(output: str, row: dict[str, Any]) -> dict[str, Score]:
    """All scorers on one row (local cross-check of the Chalk run)."""
    return {
        "suggestions_json_valid": suggestions_json_valid(output),
        "numbers_grounded": numbers_grounded(output, row["grounding_json"], row["question"],
                                             row["expected_numbers_json"]),
        "suggestion_matches": suggestion_matches(output, row["expected_suggestion_json"]),
        "answer_correct": answer_correct(output, row["expected_json"], row.get("bids_json", "[]")),
    }
