"""Clearing engine (design B3, B5, B6): ``clear`` (POST /api/clear minus the ``forecast`` block,
which M6 adds) and ``evaluate`` (POST /api/evaluate, "Beat the coordinator").

Both functions take two simulators built for the requested day: ``plan_sim`` on FORECAST inputs
(every plan is made on it) and ``score_sim`` on the day's ACTUAL inputs (every emissions number
returned is scored on it). M6 wires the forecast provider; tests and dev use
``yesterday_forecast_inputs(day)`` / ``actual_inputs(day)`` with ``load_params()``.

Pinned definitions (M5 orchestrator decisions, implemented exactly)
------------------------------------------------------------------
A. **Background population.** ``make_population(background_pct, peak_mw, seed=BACKGROUND_SEED)``
   with ``peak_mw`` = the day's FORECAST peak demand (``plan_sim.inputs.peak_demand_mw``), each
   bid scheduled by strategy 2 (independent_average) on ``plan_sim`` with no other added load.
   Its summed 24-vector is fixed added load in every strategy, coalition, plan and score. It is
   not part of the payout game.
B. **Strategies for the user bids** (planned on ``plan_sim`` with the background fixed, scored on
   ``score_sim``): ``default``, ``independent_average``, ``independent_marginal`` (strategies.py)
   and ``coordinated`` = ``greedy_place`` over all user bids with fixed load = background
   (trace recorded), with the planning-time fallback: if ``E_plan(coordinated) > E_plan(all
   independent_marginal)``, use the independent_marginal schedules and ``fallback_fired=True``.
   This is exactly the settlement game's full-coalition plan (settlement.py), so the two agree.
C-F. Settlement: see :mod:`clearinghouse.settlement`. Receipt fields per bid:
   ``vs_default_t = phi[u3] + shapley_t`` (sums to ``E(all default) - E(coordinated)``) and
   ``vs_independent_average_t = phi[u3] - phi[u2] + shapley_t`` (sums to ``E(all s2) -
   E(coordinated)``), all scored on actuals.
G. **Herding** for both independent strategies (user bids, background fixed):
   ``predicted = sum_i [E(all default) - E(i shifts alone, others default)]``, ``realized =
   E(all default) - E(all shift)``, ``gap = predicted - realized`` (``strategies.herding_metric``,
   scored on actuals, reusing the already planned schedules).
H. **coordinator_trace.** From the coordinated greedy run: consecutive placements with the same
   ``(bid, hour)`` are merged; if more than ``TRACE_MAX_STEPS`` (200) entries remain, they are
   cut into at most 200 groups of consecutive placements (equal size ``ceil(len / 200)``) and
   each group emits one entry per ``(bid_id, hour)`` present (first-appearance order) with
   ``step`` = group index (several entries may share a step) and ``mwh`` / ``incremental_co2_t``
   summed. Invariant: summed trace MWh per ``(bid, hour)`` equals the coordinated schedule. If
   the fallback fired, the trace is built from the strategy-3 schedules: one entry per bid-hour
   in hour order (bids in request order within an hour), incremental CO2 computed sequentially
   on ``plan_sim`` on top of the background, then the same 200-step aggregation.
I. **Per strategy:** ``hourly_co2_t`` / ``total_co2_t`` = TOTAL fossil CO2 of the day on
   ``score_sim`` with that strategy's total added load (background + user schedules), and
   ``marginal_resource`` = ``score_sim.marginal_resource`` on the same load.
J. **evaluate.** Each provided schedule is validated against its bid: known bid id, 24 finite
   values, non-negative, zero outside ``[earliest, deadline)``, never above ``max_mw`` (absolute
   tolerance ``EVAL_TOL_MW`` = 1e-6 MW), energy within 0.1% of ``energy_mwh`` (+1e-6 MWh); an
   infeasible bid is also reported. One error per offending bid (violations joined by "; ");
   unknown ids are reported under their own id. Design B6 says the hand schedule is scored "with
   the other user companies at default": interpreted as ANY BID WHOSE SCHEDULE IS OMITTED IS
   FILLED WITH ITS DEFAULT SCHEDULE; if all are provided all are used as given. Scored on
   ``score_sim`` with background + all user schedules, exactly like a strategy. When infeasible
   the response has ``feasible=False``, the errors, and the hourly/total/marginal of the
   ALL-DEFAULT schedule (so the shape stays valid).

Every number returned is a Python float (no numpy types).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from clearinghouse.inputs import HOURS
from clearinghouse.population import make_population
from clearinghouse.schemas import STRATEGY_NAMES, Bid, bid_feasibility_error
from clearinghouse.settlement import (
    CoalitionGame,
    SettlementResult,
    canon_sum,
    receipt_shapley,
    settle,
)
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import (
    default_schedules,
    herding_metric,
    independent_average,
    independent_marginal,
)

BACKGROUND_SEED = 4242
"""Fixed seed of the background population (pinned A); stated, never tuned."""
TRACE_MAX_STEPS = 200
EVAL_TOL_MW = 1e-6
EVAL_ENERGY_REL_TOL = 1e-3


class BidValidationError(ValueError):
    """Malformed or infeasible bids; ``errors`` is a list of ``{bid_id, message}`` (API: 422)."""

    def __init__(self, errors: list[dict]):
        self.errors = errors
        super().__init__("; ".join(f"{e['bid_id']}: {e['message']}" for e in errors))


# --- inputs ----------------------------------------------------------------------------------


def _as_bids(bids: Sequence[Bid | Mapping]) -> list[Bid]:
    return [b if isinstance(b, Bid) else Bid.model_validate(b) for b in bids]


def bid_errors(bids: list[Bid]) -> list[dict]:
    errors = []
    seen: set[str] = set()
    for b in bids:
        if b.id in seen:
            errors.append({"bid_id": b.id, "message": "duplicate bid id"})
        seen.add(b.id)
        err = bid_feasibility_error(b)
        if err:
            errors.append({"bid_id": b.id, "message": err})
    return errors


def _check_day(day: str, plan_sim: DaySimulator, score_sim: DaySimulator) -> None:
    for name, sim in (("plan_sim", plan_sim), ("score_sim", score_sim)):
        if sim.inputs.date != day:
            raise ValueError(f"{name} is built for {sim.inputs.date}, request day is {day}")
    if score_sim.inputs.kind != "actual":
        raise ValueError("score_sim must be built on actual inputs")


def background_load(background_pct: float, plan_sim: DaySimulator) -> tuple[np.ndarray, list[Bid]]:
    """Pinned A: the background population's summed strategy-2 schedule (24-vector) and bids."""
    pop = make_population(background_pct, plan_sim.inputs.peak_demand_mw, seed=BACKGROUND_SEED)
    if not pop:
        return np.zeros(HOURS), []
    scheds = independent_average(pop, plan_sim, None)
    return canon_sum([scheds[b.id] for b in pop]), pop


# --- coordinator trace -----------------------------------------------------------------------


def aggregate_trace(placements: list[dict], max_steps: int = TRACE_MAX_STEPS) -> list[dict]:
    """Pinned H: merge consecutive same-(bid, hour) placements, then (if more than ``max_steps``
    remain) aggregate into at most ``max_steps`` groups of consecutive placements."""
    merged: list[dict] = []
    for p in placements:
        if merged and merged[-1]["bid_id"] == p["bid_id"] and merged[-1]["hour"] == p["hour"]:
            merged[-1]["mwh"] += float(p["mwh"])
            merged[-1]["incremental_co2_t"] += float(p["incremental_co2_t"])
        else:
            merged.append({"bid_id": p["bid_id"], "hour": int(p["hour"]), "mwh": float(p["mwh"]),
                           "incremental_co2_t": float(p["incremental_co2_t"])})
    if len(merged) <= max_steps:
        return [{"step": k, **m} for k, m in enumerate(merged)]
    size = math.ceil(len(merged) / max_steps)
    out: list[dict] = []
    for g, start in enumerate(range(0, len(merged), size)):
        entries: dict[tuple[str, int], dict] = {}
        for m in merged[start:start + size]:
            key = (m["bid_id"], m["hour"])
            if key in entries:
                entries[key]["mwh"] += m["mwh"]
                entries[key]["incremental_co2_t"] += m["incremental_co2_t"]
            else:
                entries[key] = {"step": g, **m}
        out.extend(entries.values())
    return out


def schedule_placements(plan_sim: DaySimulator, fixed_24: np.ndarray, bids: list[Bid],
                        schedules: Mapping[str, np.ndarray]) -> list[dict]:
    """Fallback trace source: one placement per bid-hour in hour order (bids in the given order
    within an hour), incremental CO2 computed sequentially on ``plan_sim`` on top of
    ``fixed_24``."""
    added = np.asarray(fixed_24, dtype=float).copy()
    out = []
    for h in range(HOURS):
        hh = np.array([h])
        for b in bids:
            x = float(schedules[b.id][h])
            if x <= 0:
                continue
            before = float(plan_sim.hourly_co2_at(hh, added[hh])[0])
            after = float(plan_sim.hourly_co2_at(hh, added[hh] + x)[0])
            added[h] += x
            out.append({"bid_id": b.id, "hour": h, "mwh": x, "incremental_co2_t": after - before})
    return out


# --- clear -----------------------------------------------------------------------------------


def _floats(a) -> list[float]:
    return [float(x) for x in np.asarray(a, dtype=float)]


@dataclass
class ClearDetails:
    """Internals of a clear (for tests, scripts and M6 diagnostics; not part of the API)."""

    background_24: np.ndarray
    background_bids: list[Bid]
    schedules: dict[str, dict[str, np.ndarray]]  # strategy -> bid_id -> 24 MW
    total_added: dict[str, np.ndarray]  # strategy -> background + user schedules
    plan_totals_t: dict[str, float]  # E_plan per strategy
    score_totals_t: dict[str, float]  # E_score per strategy
    settlement: SettlementResult
    game: CoalitionGame
    phi_u3: list[float]
    phi_u2: list[float]
    raw_trace: list[dict]


def clear_with_details(day: str, bids: Sequence[Bid | Mapping], background_pct: float,
                       carbon_price: float, pool_usd: float, plan_sim: DaySimulator,
                       score_sim: DaySimulator) -> tuple[dict, ClearDetails]:
    """:func:`clear` plus its internals."""
    bids = _as_bids(bids)
    errors = bid_errors(bids)
    if errors:
        raise BidValidationError(errors)
    _check_day(day, plan_sim, score_sim)
    ids = [b.id for b in bids]

    bg, pop = background_load(background_pct, plan_sim)
    fixed = [bg]
    s1 = default_schedules(bids)
    s2 = independent_average(bids, plan_sim, bg)
    s3 = independent_marginal(bids, plan_sim, bg)

    game = CoalitionGame(bids, s3, fixed, plan_sim, score_sim)
    full = game.plan(game.full, record_trace=True)
    s4 = {i: full.schedules[i] for i in ids}
    fallback = full.fallback_fired
    if fallback:
        raw = schedule_placements(plan_sim, bg, bids, s3)
    else:
        raw = full.trace or []
    trace = aggregate_trace(raw)

    sett = settle(bids, s3, fixed, plan_sim, score_sim, carbon_price, pool_usd, game=game)

    schedules = {"default": s1, "independent_average": s2, "independent_marginal": s3,
                 "coordinated": s4}
    totals = {k: canon_sum(fixed + [schedules[k][i] for i in ids]) for k in STRATEGY_NAMES}
    rows = np.vstack([totals[k] for k in STRATEGY_NAMES])
    hourly = score_sim.hourly_emissions_batch(rows)
    e_score = hourly.sum(axis=-1)
    e_plan = plan_sim.emissions_batch(rows)

    strategies = {}
    for k, name in enumerate(STRATEGY_NAMES):
        strategies[name] = {
            "schedule": {i: _floats(schedules[name][i]) for i in ids},
            "hourly_co2_t": _floats(hourly[k]),
            "total_co2_t": float(e_score[k]),
            "marginal_resource": score_sim.marginal_resource(totals[name]),
        }

    herding = {}
    for name, sched in (("independent_average", s2), ("independent_marginal", s3)):
        res = herding_metric(bids, plan_sim, score_sim,
                             lambda _b, _p, _bg, s=sched: s, background_24=bg)
        herding[name] = {k: float(v) + 0.0 for k, v in res.as_metric().items()}  # no -0.0

    base1 = [s1[i] for i in ids]
    phi_u3 = receipt_shapley(score_sim, fixed, base1, [s3[i] for i in ids])
    phi_u2 = receipt_shapley(score_sim, fixed, base1, [s2[i] for i in ids])
    per_bid = []
    for k, i in enumerate(ids):
        sh = sett.shapley_t[k]
        per_bid.append({
            "bid_id": i,
            "shapley_t": float(sh),
            "payout_usd": float(sett.payout_usd[k]),
            "vs_default_t": float(phi_u3[k] + sh),
            "vs_independent_average_t": float(phi_u3[k] - phi_u2[k] + sh),
        })

    response = {
        "strategies": strategies,
        "herding": herding,
        "coordinator_trace": trace,
        "fallback_fired": bool(fallback),
        "settlement": {
            "baseline": "independent_marginal",
            "method": sett.method,
            "coalitions_evaluated": int(sett.coalitions_evaluated),
            "total_avoided_t": float(sett.total_avoided_t),
            "pool_usd": float(pool_usd),
            "prorated": bool(sett.prorated),
            "per_bid": per_bid,
            "leave_one_out": sett.leave_one_out,
        },
    }
    details = ClearDetails(
        background_24=bg, background_bids=pop, schedules=schedules, total_added=totals,
        plan_totals_t={k: float(e_plan[j]) for j, k in enumerate(STRATEGY_NAMES)},
        score_totals_t={k: float(e_score[j]) for j, k in enumerate(STRATEGY_NAMES)},
        settlement=sett, game=game, phi_u3=phi_u3, phi_u2=phi_u2, raw_trace=raw)
    return response, details


def clear(day: str, bids: Sequence[Bid | Mapping], background_pct: float, carbon_price: float,
          pool_usd: float, plan_sim: DaySimulator, score_sim: DaySimulator) -> dict:
    """Clear the bids for ``day``: every field of ``schemas.ClearResponse`` except ``forecast``.

    Raises :class:`BidValidationError` (per-bid messages) for duplicate ids or infeasible bids.
    """
    return clear_with_details(day, bids, background_pct, carbon_price, pool_usd, plan_sim,
                              score_sim)[0]


# --- evaluate (Beat the coordinator) ---------------------------------------------------------


def evaluate_schedule_error(bid: Bid, values) -> str | None:
    """Pinned J validation of one hand-made schedule; ``None`` if it satisfies the bid."""
    try:
        s = np.asarray(values, dtype=float)
    except (TypeError, ValueError):
        return "schedule must be a list of numbers"
    if s.shape != (HOURS,):
        return f"schedule must have {HOURS} hourly values, got {s.size}"
    if not np.isfinite(s).all():
        return "schedule has non-finite values"
    problems = []
    if (s < -EVAL_TOL_MW).any():
        problems.append("schedule has negative MW in slots "
                        f"{[int(h) for h in np.flatnonzero(s < -EVAL_TOL_MW)]}")
    outside = np.ones(HOURS, dtype=bool)
    outside[bid.earliest_hour:bid.deadline_hour] = False
    bad = outside & (np.abs(s) > EVAL_TOL_MW)
    if bad.any():
        problems.append(f"runs outside its window [{bid.earliest_hour}, {bid.deadline_hour}) "
                        f"in slots {[int(h) for h in np.flatnonzero(bad)]}")
    over = s > bid.max_mw + EVAL_TOL_MW
    if over.any():
        problems.append(f"exceeds max_mw {bid.max_mw:g} MW in slots "
                        f"{[int(h) for h in np.flatnonzero(over)]} (peak {s.max():g} MW)")
    delivered = math.fsum(s.tolist())
    if abs(delivered - bid.energy_mwh) > EVAL_ENERGY_REL_TOL * bid.energy_mwh + 1e-6:
        problems.append(f"delivers {delivered:g} MWh but the bid needs {bid.energy_mwh:g} MWh "
                        f"(tolerance {EVAL_ENERGY_REL_TOL:.1%})")
    return "; ".join(problems) if problems else None


def evaluate(day: str, bids: Sequence[Bid | Mapping], schedule: Mapping[str, Sequence[float]],
             background_pct: float, plan_sim: DaySimulator, score_sim: DaySimulator) -> dict:
    """Score a hand-made schedule (``schemas.EvaluateResponse``); see pinned J above."""
    bids = _as_bids(bids)
    _check_day(day, plan_sim, score_sim)
    by_id = {b.id: b for b in bids}
    errors = list(bid_errors(bids))
    for bid_id in schedule:
        if bid_id not in by_id:
            errors.append({"bid_id": str(bid_id), "message": "unknown bid id (not in bids)"})
    for b in bids:
        if b.id in schedule and bid_feasibility_error(b) is None:
            err = evaluate_schedule_error(b, schedule[b.id])
            if err:
                errors.append({"bid_id": b.id, "message": err})
    defaults = default_schedules(bids)
    feasible = not errors
    if feasible:
        user = {b.id: (np.asarray(schedule[b.id], dtype=float) if b.id in schedule
                       else defaults[b.id]) for b in bids}
    else:
        user = defaults
    bg, _ = background_load(background_pct, plan_sim)
    total = canon_sum([bg] + [user[b.id] for b in bids])
    hourly = score_sim.hourly_emissions_batch(total[None, :])[0]
    return {
        "feasible": feasible,
        "errors": errors,
        "hourly_co2_t": _floats(hourly),
        "total_co2_t": float(hourly.sum()),
        "marginal_resource": score_sim.marginal_resource(total),
    }
