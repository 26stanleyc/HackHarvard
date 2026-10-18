"""Plain-English definitions of the bid and scenario fields (prompt text and eval dataset).

The spec's "7 bid fields" map onto this repo as: window start = ``earliest_hour``, window end =
deadline = ``deadline_hour`` (exclusive), ``energy_mwh``, ``max_mw``, ``load_type``, and the
scenario-level ``carbon_price`` and ``pool_usd``. There is no per-bid strategy: the coordinator
schedules every bid. ``background_pct`` is included because users ask about it.
"""

from __future__ import annotations

GLOSSARY: dict[str, dict[str, str]] = {
    "earliest_hour": {
        "label": "Earliest (window start)",
        "unit": "hour of day, 0-23 (CST)",
        "definition": (
            "The first hour your load may run. The window starts at the beginning of this hour. "
            "An earlier start gives the coordinator more hours to choose from, so it can move "
            "your energy into cleaner hours."
        ),
    },
    "deadline_hour": {
        "label": "Deadline (window end)",
        "unit": "hour of day, 1-24 (CST), exclusive",
        "definition": (
            "The hour by which all your energy must be delivered. It is exclusive: a deadline of "
            "18 means the last hour you can run is 17:00-18:00. Windows cannot cross midnight, "
            "so the deadline must be after the earliest hour."
        ),
    },
    "energy_mwh": {
        "label": "Energy",
        "unit": "MWh",
        "definition": (
            "The total energy your load needs inside the window. The coordinator decides when "
            "it is delivered, never how much: the full amount is always scheduled."
        ),
    },
    "max_mw": {
        "label": "Max power",
        "unit": "MW",
        "definition": (
            "The most power your equipment can draw in any one hour. Energy divided by max "
            "power is the minimum number of hours you must run. If that equals the window "
            "length, the coordinator has no room to move your load."
        ),
    },
    "load_type": {
        "label": "Load type",
        "unit": "category",
        "definition": (
            "What kind of flexible load this is (EV fleet, data center, office pre-cooling, "
            "cold storage, water pumping). It is a label for the receipts; it does not change "
            "how the coordinator schedules the bid."
        ),
    },
    "carbon_price": {
        "label": "Carbon price",
        "unit": "$ per tonne CO2",
        "definition": (
            "What each tonne of CO2 avoided by coordination is worth. Payouts are your share of "
            "the avoided tonnes times this price, capped by the payout pool. It does not change "
            "the schedule."
        ),
    },
    "pool_usd": {
        "label": "Payout pool",
        "unit": "$",
        "definition": (
            "The most the clearinghouse pays out in total. If avoided tonnes times the carbon "
            "price exceed it, every payout is scaled down in proportion."
        ),
    },
    "background_pct": {
        "label": "Background flexible load",
        "unit": "% of the day's forecast peak demand",
        "definition": (
            "Other flexible load in the region already following a standard carbon-aware tool. "
            "It is fixed context that crowds the clean hours; it is not part of the payout game."
        ),
    },
}

# Words a user might type for each field (lowercase), used by the local fallback.
ALIASES: dict[str, tuple[str, ...]] = {
    "earliest_hour": ("earliest", "window start", "start hour", "start time"),
    "deadline_hour": ("deadline", "window end", "end hour", "end time"),
    "energy_mwh": ("energy", "mwh"),
    "max_mw": ("max mw", "max power", "maximum power", "max_mw", "power limit"),
    "load_type": ("load type", "load_type"),
    "carbon_price": ("carbon price", "price per ton", "$/t"),
    "pool_usd": ("pool", "payout pool"),
    "background_pct": ("background",),
}

BID_FIELDS = ("earliest_hour", "deadline_hour", "energy_mwh", "max_mw")
