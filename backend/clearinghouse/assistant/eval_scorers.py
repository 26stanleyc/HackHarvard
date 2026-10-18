"""Scorer logic for the assistant evaluation (``scripts/eval_assistant.py``).

Plain functions so they are unit-tested locally and wrapped as ``@chalkcompute.function``
scorers on Chalk. Each takes the task's ``output`` (a JSON string, see ``task_output``) plus
dataset columns, and returns ``(score in [0, 1] or None if not applicable, metadata)``.

* ``suggestions_json_valid``: the suggestions block the assistant produced parses as
  ``{"suggestions": [...]}``.
* ``numbers_grounded``: every number in the answer is in the grounding payload (1% tolerance) or
  the question. The hallucination check.
* ``suggestion_matches``: for rows with an expected edit, a returned suggestion has the expected
  bid, field and direction; for rows expecting none, no "fix" is returned.
* ``answer_correct``: per-row facts: definition rows name the field; cleanest-hours rows list the
  simulator's 4 cleanest hours (or say timing does not matter on a flat day); feasibility rows
  call out exactly the infeasible bids; savings rows refuse to quote savings.
"""

from __future__ import annotations

import json
import re
from typing import Any

from clearinghouse.assistant.checks import numbers_in_text, ungrounded_numbers

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
    extra = numbers_in_text(question) + expected
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


def _hh(h: int) -> str:
    return f"{h:02d}:00"


def answer_correct(output: str, expected_json: str) -> Score:
    exp = json.loads(expected_json)
    a = _out(output).get("answer", "")
    al = a.lower()
    kind = exp["kind"]
    if kind == "definition":
        ok = exp["label"].lower() in al
        return (1.0 if ok else 0.0), {"label": exp["label"]}
    if kind == "cleanest":
        if exp["flat"]:
            ok = "does not change emissions" in al or "doesn't change emissions" in al
            return (1.0 if ok else 0.0), {"flat": True}
        named = [h for h in exp["hours"] if _hh(h) in a]
        return len(named) / len(exp["hours"]), {"missing": [h for h in exp["hours"]
                                                            if h not in named]}
    if kind == "feasibility":
        said_bad = [n for n in exp["infeasible"] if re.search(
            re.escape(n.lower()) + r"[^.]*not feasible", al)]
        false_alarm = [n for n in exp["feasible"] if re.search(
            re.escape(n.lower()) + r" is not feasible", al)]
        if not exp["infeasible"]:
            ok = "feasible" in al and not false_alarm
            return (1.0 if ok else 0.0), {"false_alarm": false_alarm}
        score = len(said_bad) / len(exp["infeasible"]) - 0.5 * bool(false_alarm)
        return max(0.0, score), {"missed": [n for n in exp["infeasible"] if n not in said_bad],
                                 "false_alarm": false_alarm}
    if kind == "savings":
        claims = re.search(r"\bwill (avoid|save|earn)\b", al)
        ok = not claims and ("run the clearinghouse" in al or "simulator" in al)
        return (1.0 if ok else 0.0), {}
    return None, {"unknown kind": kind}


SCORERS = ("suggestions_json_valid", "numbers_grounded", "suggestion_matches", "answer_correct")


def score_row(output: str, row: dict[str, Any]) -> dict[str, Score]:
    """All scorers on one row (local cross-check of the Chalk run)."""
    return {
        "suggestions_json_valid": suggestions_json_valid(output),
        "numbers_grounded": numbers_grounded(output, row["grounding_json"], row["question"],
                                             row["expected_numbers_json"]),
        "suggestion_matches": suggestion_matches(output, row["expected_suggestion_json"]),
        "answer_correct": answer_correct(output, row["expected_json"]),
    }
