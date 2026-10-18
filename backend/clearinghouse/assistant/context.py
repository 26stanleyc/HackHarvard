"""The grounding payload: the only numbers the assistant may cite.

Built from real app state on every call:

* the user's current bids (as sent, possibly still invalid) with the same feasibility rules as
  ``schemas.bid_feasibility_error`` / ``frontend/src/lib/feasibility.ts``;
* bid features from the forecast provider (a Chalk ``flex_bid`` online query when Chalk is
  configured, else local), for bids that are well-formed;
* the day's forecast (demand, wind, solar for 24 h) from the same provider the clearing uses
  (a Chalk ``grid_day`` online query when configured), with its source;
* the simulator's marginal CO2 rate per hour on the forecast day (``DaySimulator.marginal_rate``
  on the planning simulator; never actuals), and the 4 cleanest / 4 dirtiest hours by it.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from clearinghouse.assistant.models import AssistantBid, Scenario
from clearinghouse.schemas import FEASIBILITY_TOL_MWH, HOURS

N_EXTREME_HOURS = 4


def shape_error(b: AssistantBid) -> str | None:
    for k in ("energy_mwh", "max_mw"):
        v = getattr(b, k)
        if v is None or not math.isfinite(v):
            return f"{k} must be a number"
        if v < 0:
            return f"{k} must be >= 0"
    if b.earliest_hour is None or not 0 <= b.earliest_hour <= HOURS - 1:
        return f"earliest_hour must be an integer 0-{HOURS - 1}"
    if b.deadline_hour is None or not 1 <= b.deadline_hour <= HOURS:
        return f"deadline_hour must be an integer 1-{HOURS}"
    return None


def feasibility_error(b: AssistantBid) -> str | None:
    """Same messages as ``schemas.bid_feasibility_error`` (for well-formed bids)."""
    if b.earliest_hour >= b.deadline_hour:
        return (f"earliest_hour ({b.earliest_hour}) must be before deadline_hour "
                f"({b.deadline_hour}); windows cannot cross midnight")
    window = b.deadline_hour - b.earliest_hour
    capacity = b.max_mw * window
    if b.energy_mwh > capacity + FEASIBILITY_TOL_MWH:
        return (f"energy_mwh ({b.energy_mwh:g}) exceeds max_mw x window "
                f"({b.max_mw:g} MW x {window} h = {capacity:g} MWh)")
    return None


def _r(x: Any, nd: int = 1):
    if x is None or isinstance(x, bool):
        return x
    if isinstance(x, int):
        return x
    return round(float(x), nd)


def rank_hours(marginal: np.ndarray, intensity: np.ndarray) -> tuple[list[int], list[int]]:
    """Cleanest and dirtiest hours by marginal rate (ties broken by average intensity)."""
    order = sorted(range(HOURS), key=lambda h: (marginal[h], intensity[h], h))
    return sorted(order[:N_EXTREME_HOURS]), sorted(order[-N_EXTREME_HOURS:])


def build_grounding(engine: Any, day: str, bids: list[AssistantBid],
                    scenario: Scenario | None = None) -> dict:
    """Grounding payload for ``day`` and ``bids``. ``engine`` is ``api.Engine``."""
    fc, _ = engine.forecast(day, fresh=False)
    plan, intensity, _ = engine.plan_sim(fc)
    marginal = np.asarray(plan.marginal_rate(np.zeros(HOURS)), dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    cleanest, dirtiest = rank_hours(marginal, intensity)

    rows: list[dict] = []
    well_formed: list[AssistantBid] = []
    for b in bids:
        row = {"id": b.id, "name": b.name, "load_type": b.load_type,
               "energy_mwh": _r(b.energy_mwh), "earliest_hour": b.earliest_hour,
               "deadline_hour": b.deadline_hour, "max_mw": _r(b.max_mw)}
        err = shape_error(b)
        if err is None:
            err = feasibility_error(b)
            well_formed.append(b)
            lo, hi = b.earliest_hour, b.deadline_hour
            window = max(hi - lo, 0)
            row["window_hours"] = window
            row["min_run_hours"] = _r(b.energy_mwh / b.max_mw, 2) if b.max_mw > 0 else None
            row["cleanest_hours_in_window"] = [h for h in cleanest if lo <= h < hi]
            row["dirtiest_hours_in_window"] = [h for h in dirtiest if lo <= h < hi]
        row["feasible"] = err is None
        row["problem"] = err
        rows.append(row)

    features_source = None
    if well_formed:
        source, feats = engine.provider.get_bid_features_many(
            [b.model_dump() for b in well_formed], day)
        features_source = source
        by_id = {b.id: f for b, f in zip(well_formed, feats, strict=True)}
        keep = ("slack_hours", "window_mean_renewable_share", "window_curtailed_mwh",
                "window_lowest_net_load_hour", "energy_share_of_day_demand")
        for row in rows:
            f = by_id.get(row["id"])
            if f is not None:
                row.update({k: _r(f.get(k), 4 if "share" in k else 1) for k in keep})

    payload = {
        "day": day,
        "forecast": {
            "source": fc.source,
            "note": "day-ahead forecast issued 00:00 CST on the day; hour h is h:00-h+1:00 CST",
            "demand_mw": [round(float(x)) for x in fc.demand_mw],
            "wind_mw": [round(float(x)) for x in fc.wind_mw],
            "solar_mw": [round(float(x)) for x in fc.solar_mw],
        },
        "marginal_co2_t_per_mwh": [round(float(x), 3) for x in marginal],
        "avg_intensity_t_per_mwh": [round(float(x), 3) for x in intensity],
        "cleanest_hours": cleanest,
        "dirtiest_hours": dirtiest,
        "bids": rows,
        "bid_features_source": features_source,
        "scenario": scenario.model_dump() if scenario else None,
        "results": None,  # no clearing result is passed to the assistant (never claim savings)
    }
    if fc.methods.get("fallback_reason"):
        payload["forecast"]["fallback_reason"] = fc.methods["fallback_reason"]
    return payload
