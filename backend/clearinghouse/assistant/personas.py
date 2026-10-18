"""Three switchable personas. They change vocabulary and examples only, never the grounding data
or the math."""

from __future__ import annotations

from typing import Literal

PersonaId = Literal["ev_fleet", "data_center", "facilities"]
DEFAULT_PERSONA: PersonaId = "ev_fleet"

PERSONAS: dict[str, dict] = {
    "ev_fleet": {
        "label": "EV fleet manager",
        "addendum": (
            "The user runs overnight depot charging for an electric vehicle fleet. Talk in terms "
            "of vehicles, chargers and the morning dispatch. The deadline is when vehicles leave "
            "the depot; max MW is the depot's charger capacity."
        ),
        "example": "100 vans at about 0.1 MWh each need roughly 10 MWh; 50 chargers at 150 kW "
                   "give 7.5 MW max; deadline 06:00 for the morning dispatch.",
        # Number-free hints for the local fallback (its answers cite grounding numbers only).
        "hint": {
            "max_mw": "For a depot, that is the combined power of the chargers you can run "
                      "at once.",
            "energy_mwh": "For a fleet, add up what each vehicle needs to be ready for dispatch.",
            "deadline_hour": "For a fleet, use the time the first vehicles leave the depot.",
            "earliest_hour": "For a fleet, use the time most vehicles are back and plugged in.",
        },
    },
    "data_center": {
        "label": "Data-center ops lead",
        "addendum": (
            "The user schedules deferrable batch and training jobs in a data center. Talk in "
            "terms of jobs, racks and SLAs. Max MW is the power of the racks the jobs can use; "
            "the deadline is the job's SLA."
        ),
        "example": "A training run on racks drawing 20 MW for 10 hours needs 200 MWh; the SLA "
                   "sets the deadline.",
        "hint": {
            "max_mw": "For batch jobs, that is the power of the racks the jobs may use.",
            "energy_mwh": "For batch jobs, that is rack power times the job's run time.",
            "deadline_hour": "For batch jobs, use the time the job's SLA requires results by.",
            "earliest_hour": "For batch jobs, use the time the inputs are ready.",
        },
    },
    "facilities": {
        "label": "Facilities manager",
        "addendum": (
            "The user manages pre-cooling and thermal storage for office buildings. Talk in terms "
            "of chillers, ice storage and occupancy. The window is set by when the buildings are "
            "occupied; max MW is chiller capacity."
        ),
        "example": "Pre-cooling 20 buildings before 09:00 occupancy with 6 MW of chillers for "
                   "4 hours uses 24 MWh.",
        "hint": {
            "max_mw": "For pre-cooling, that is your chillers' combined capacity.",
            "energy_mwh": "For pre-cooling, that is chiller power times the hours of cooling "
                          "you need banked.",
            "deadline_hour": "For pre-cooling, use the time the buildings become occupied.",
            "earliest_hour": "For pre-cooling, use the earliest hour your thermal mass holds "
                             "the cold until occupancy.",
        },
    },
}


def persona(pid: str | None) -> dict:
    return PERSONAS.get(pid or DEFAULT_PERSONA, PERSONAS[DEFAULT_PERSONA])
