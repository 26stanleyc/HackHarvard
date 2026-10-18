"""Dataset ``clearinghouse-assistant-v1`` for the assistant evaluation (44 rows).

Built locally from real app state: each row's ``grounding_json`` is the payload
``/api/assistant/chat`` would build for that day and those bids (forecast and bid features from
the provider, i.e. Chalk online queries when ``CHALK_*`` are set; the simulator's marginal
rates), and ``expected_json`` holds the facts the answer must contain. Kept out of
``scripts/eval_assistant.py`` because importing that script deploys its Chalk functions.

Rows: 24 definitions (8 glossary fields x 3 personas), 9 cleanest-hours questions (3 days x 3
personas; ground truth = the simulator's 4 lowest marginal-rate hours), 8 feasibility edge
cases (reversed window, energy over capacity, over capacity next to midnight, overlapping but
valid windows; x 2 days), 3 savings questions that must be refused.
"""

from __future__ import annotations

import json

DATASET = "clearinghouse-assistant-v1"


def build_rows() -> list[dict]:
    from clearinghouse.api import get_engine
    from clearinghouse.assistant.context import build_grounding
    from clearinghouse.assistant.glossary import GLOSSARY
    from clearinghouse.assistant.models import AssistantBid
    from clearinghouse.presets import preset_bids

    engine = get_engine()
    personas = ("ev_fleet", "data_center", "facilities")
    days = ("2024-08-20", "2024-03-29", "2024-01-16")
    base = [AssistantBid(**b.model_dump()) for b in preset_bids()[:3]]
    rows: list[dict] = []

    def add(question, bids, day, persona, expected, expected_field="", focus="",
            expected_suggestion=None):
        payload = build_grounding(engine, day, bids)
        rows.append({
            "question": question, "bids_json": json.dumps([b.model_dump() for b in bids]),
            "day": day, "persona": persona, "focus_field": focus,
            "grounding_json": json.dumps(payload, separators=(",", ":")),
            "expected_field": expected_field, "expected_numbers_json": "[]",
            "expected_suggestion_json": json.dumps(expected_suggestion) if expected_suggestion
            else "",
            "expected_json": json.dumps(expected(payload) if callable(expected) else expected),
        })

    # 1. Definitions: every glossary field x persona (24 rows).
    for i, (field, g) in enumerate(GLOSSARY.items()):
        for j, persona in enumerate(personas):
            q = (f"What does {g['label'].lower()} mean?", f"Explain {field} to me",
                 f"How should I set {g['label'].lower()}?")[j]
            add(q, base, days[(i + j) % 3], persona,
                {"kind": "definition", "label": g["label"]}, expected_field=field)

    # 2. Cleanest hours: ground truth from the simulator, per day x persona (9 rows).
    for day in days:
        for persona in personas:
            add("Which hours are cleanest today?", base, day, persona,
                lambda p: {"kind": "cleanest", "hours": p["cleanest_hours"],
                           "flat": p["marginal_rates_all_equal"]})

    # 3. Feasibility edge cases x 2 days (8 rows).
    def patched(**fields):
        return [base[0].model_copy(update=fields), *base[1:]]
    big = AssistantBid(id="b9", name="Night depot", load_type="ev_fleet", energy_mwh=100,
                       earliest_hour=0, deadline_hour=4, max_mw=10)
    late = AssistantBid(id="b9", name="Night depot", load_type="ev_fleet", energy_mwh=100,
                        earliest_hour=20, deadline_hour=24, max_mw=10)
    names = [b.name for b in base]
    cases = [
        ("Is my window feasible?", patched(earliest_hour=20, deadline_hour=18),
         {"kind": "feasibility", "infeasible": [names[0]], "feasible": names[1:]},
         {"bid_id": "b1", "fields": ["deadline_hour", "earliest_hour"],
          "direction": {"deadline_hour": "up", "earliest_hour": "down"}}),
        ("Can the coordinator fit all my energy?", [*base, big],
         {"kind": "feasibility", "infeasible": ["Night depot"], "feasible": names},
         {"bid_id": "b9", "fields": ["deadline_hour", "max_mw"],
          "direction": {"deadline_hour": "up", "max_mw": "up"}}),
        ("Is anything wrong with my bids?", [*base, late],
         {"kind": "feasibility", "infeasible": ["Night depot"], "feasible": names},
         {"bid_id": "b9", "fields": ["max_mw"], "direction": {"max_mw": "up"}}),
        ("Are my bids valid? Two of them overlap.", base,
         {"kind": "feasibility", "infeasible": [], "feasible": names}, {"none": True}),
    ]
    for day in days[:2]:
        for k, (q, bids, exp, sug) in enumerate(cases):
            add(q, bids, day, personas[k % 3], exp, expected_suggestion=sug)

    # 4. Savings questions must be refused until a clearing result exists (3 rows).
    for persona, q in zip(personas, ("How many tonnes will my fleet avoid?",
                                     "What payout will I get?",
                                     "How much CO2 will I save?"), strict=True):
        add(q, base, days[0], persona, {"kind": "savings"})
    return rows


def main(out_path: str) -> None:
    """Write ``{"rows": [...], "gateway": status}`` to ``out_path``.

    Run as a subprocess by ``scripts/eval_assistant.py`` so the evaluation process never loads
    the repo ``.env`` (its ``CHALK_CLIENT_*`` are the app's query-only token; dataset upload and
    evaluation runs use the developer's ``chalk login``).
    """
    from pathlib import Path

    from clearinghouse.assistant.service import gateway_status

    Path(out_path).write_text(json.dumps({"rows": build_rows(), "gateway": gateway_status()}))


if __name__ == "__main__":
    import sys

    main(sys.argv[1])
