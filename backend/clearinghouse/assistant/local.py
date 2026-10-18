"""Local (rule-based) fallback for the assistant, used when the Chalk Model Gateway is not
available. It answers from the same grounding payload as the LLM path, using keyword intents,
and never invents numbers. The UI labels it "local (rule-based)".
"""

from __future__ import annotations

from clearinghouse.assistant.glossary import ALIASES, GLOSSARY
from clearinghouse.assistant.personas import persona


def _hh(h: int) -> str:
    return f"{h:02d}:00"


def _hours(hs: list[int]) -> str:
    return ", ".join(_hh(h) for h in hs) if hs else "none"


def _field_for(text: str, focus: str | None) -> str | None:
    if focus in GLOSSARY:
        return focus
    t = text.lower()
    for field, words in ALIASES.items():
        if any(w in t for w in words):
            return field
    return None


def _bid_line(b: dict, field: str) -> str | None:
    v = b.get(field)
    if v is None:
        return None
    unit = {"energy_mwh": " MWh", "max_mw": " MW"}.get(field, "")
    if field in ("earliest_hour", "deadline_hour"):
        return f"{b['name'] or b['id']}: {_hh(v)}"
    return f"{b['name'] or b['id']}: {v:g}{unit}" if not isinstance(v, str) else f"{b['name']}: {v}"


def _feasibility(payload: dict) -> str:
    bids = payload["bids"]
    if not bids:
        return "You have no companies yet. Add one and I will check its window."
    bad = [b for b in bids if not b["feasible"]]
    if not bad:
        parts = []
        for b in bids[:4]:
            if b.get("min_run_hours") is not None:
                parts.append(f"{b['name'] or b['id']} needs {b['min_run_hours']:g} h of its "
                             f"{b['window_hours']} h window")
        return "All your bids are feasible. " + ("; ".join(parts) + "." if parts else "")
    return " ".join(f"{b['name'] or b['id']} is not feasible: {b['problem']}." for b in bad[:3])


def _clean_hours(payload: dict) -> str:
    m = payload["marginal_co2_t_per_mwh"]
    if payload.get("marginal_rates_all_equal"):
        a = payload["avg_intensity_t_per_mwh"]
        lo = ", ".join(f"{_hh(h)} ({a[h]:g} t/MWh)" for h in payload["cleanest_hours"])
        return (f"On the {payload['forecast']['source']} forecast for {payload['day']}, every "
                f"hour has the same marginal CO2 rate ({m[0]:g} t/MWh), so moving load does not "
                "change emissions today. By average grid intensity instead, the cleanest hours "
                f"are {lo}.")
    clean = ", ".join(f"{_hh(h)} ({m[h]:g} t/MWh)" for h in payload["cleanest_hours"])
    dirty = ", ".join(f"{_hh(h)} ({m[h]:g} t/MWh)" for h in payload["dirtiest_hours"])
    zero = sum(1 for x in m if x == 0)
    text = (f"On the {payload['forecast']['source']} forecast for {payload['day']}, the cleanest "
            f"hours by marginal CO2 rate are {clean}. The dirtiest are {dirty}.")
    if zero:
        text += (f" {zero} hours have a rate of 0: extra load there is absorbed by curtailed "
                 "wind and solar.")
    for b in payload["bids"][:3]:
        if "cleanest_hours_in_window" in b:
            text += (f" {b['name'] or b['id']}'s window holds "
                     f"{len(b['cleanest_hours_in_window'])} of them.")
    return text


def _forecast(payload: dict) -> str:
    f = payload["forecast"]
    d, w, s = f["demand_mw"], f["wind_mw"], f["solar_mw"]
    hd = max(range(24), key=lambda h: d[h])
    hw = max(range(24), key=lambda h: w[h])
    hs = max(range(24), key=lambda h: s[h])
    return (f"The {f['source']} forecast for {payload['day']} peaks at {d[hd]:,} MW demand at "
            f"{_hh(hd)}. Wind peaks at {w[hw]:,} MW at {_hh(hw)} and solar at {s[hs]:,} MW at "
            f"{_hh(hs)}.")


def answer(question: str, payload: dict, persona_id: str | None,
           focus_field: str | None = None) -> str:
    q = question.lower()
    field = _field_for(question, focus_field)
    if any(w in q for w in ("save", "avoid", "tonne", "tons", "payout", "earn", "paid")):
        return ("I can only quote savings and payouts from a simulator run, and none is in my "
                "data yet. Run the clearinghouse and the results screen shows what coordination "
                "avoided and each company's share.")
    if field and not any(w in q for w in ("feasible", "valid", "clean")):
        g = GLOSSARY[field]
        lines = [x for x in (_bid_line(b, field) for b in payload["bids"]) if x]
        text = f"{g['label']} ({g['unit']}): {g['definition']}"
        if lines:
            text += " Your current values: " + "; ".join(lines[:4]) + "."
        text += f" {persona(persona_id)['hint'].get(field, '')}".rstrip()
        return text
    if any(w in q for w in ("feasible", "valid", "fit", "work", "problem", "wrong", "error")):
        return _feasibility(payload)
    if any(w in q for w in ("clean", "dirty", "best hour", "when", "which hour", "green")):
        return _clean_hours(payload)
    if any(w in q for w in ("forecast", "wind", "solar", "demand", "weather")):
        return _forecast(payload)
    n = len(payload["bids"])
    head = (f"You have {n} compan{'y' if n == 1 else 'ies'} on {payload['day']}. "
            if n else f"No companies yet for {payload['day']}. ")
    return (head + _feasibility(payload) + " Ask me what any field means, whether a window is "
            "feasible, or which hours are cleanest today.") if n else (
        head + "Ask me what any field means or which hours are cleanest today.")
