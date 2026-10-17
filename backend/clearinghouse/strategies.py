"""Scheduling strategies 1-3, the coordinator greedy core (strategy 4) and the herding metric
(design B3).

Conventions
-----------
* A schedule is ``{bid_id: np.ndarray[24]}`` of MW per CST slot (= MWh per slot).
* Every planner works on a *planning* simulator (forecast inputs, e.g. yesterday-as-forecast);
  every score uses a *scoring* simulator (the actual day). Planning never sees actuals.
* ``background_24`` is fixed added load from everyone else (the background population); ``None``
  means zero.

Strategies
----------
1. :func:`default_schedule`: run at ``max_mw`` from ``earliest_hour`` until the energy is met.
2. :func:`independent_average`: each bid fills its window's hours with the lowest forecast
   average intensity (forecast + background, NO user loads: the standard tool is unaware of
   others), at ``max_mw``, ties by earliest hour.
3. :func:`independent_marginal`: each bid alone runs :func:`greedy_place` on its own blocks with
   the other user bids at default (plus background) as fixed load. Then all execute together.
4. :func:`greedy_place` is the coordinator core used by strategy 4 (M5 adds the fallback and
   settlement around it).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

import numpy as np

from clearinghouse.inputs import HOURS
from clearinghouse.schemas import Bid
from clearinghouse.simulator import DaySimulator

Schedule = dict[str, np.ndarray]
ENERGY_REL_TOL = 1e-3  # schedules must deliver energy within 0.1%
_ALL_HOURS = np.arange(HOURS)

# --- schedule helpers ------------------------------------------------------------------------


def _bg(background_24) -> np.ndarray:
    return np.zeros(HOURS) if background_24 is None else np.asarray(background_24, dtype=float)


def sum_schedules(schedules: Mapping[str, np.ndarray] | Iterable[np.ndarray]) -> np.ndarray:
    """Total MW per slot of a schedule dict (or iterable of 24-vectors)."""
    vals = schedules.values() if isinstance(schedules, Mapping) else schedules
    total = np.zeros(HOURS)
    for v in vals:
        total = total + np.asarray(v, dtype=float)
    return total


def schedule_error(bid: Bid, sched, rel_tol: float = ENERGY_REL_TOL) -> str | None:
    """Why ``sched`` does not satisfy ``bid`` (energy within ``rel_tol``, only inside the window,
    never above ``max_mw``, never negative), or ``None`` if it does."""
    s = np.asarray(sched, dtype=float)
    if s.shape != (HOURS,):
        return f"schedule must have {HOURS} values, got shape {s.shape}"
    eps = 1e-9 * max(1.0, bid.max_mw)
    if (s < -eps).any():
        return "schedule has negative MW"
    outside = np.ones(HOURS, dtype=bool)
    outside[bid.earliest_hour:bid.deadline_hour] = False
    if (np.abs(s[outside]) > eps).any():
        hrs = [int(h) for h in np.flatnonzero(outside & (np.abs(s) > eps))]
        return f"runs outside window [{bid.earliest_hour}, {bid.deadline_hour}) in slots {hrs}"
    if (s > bid.max_mw + eps).any():
        return f"exceeds max_mw {bid.max_mw:g} (peak {s.max():g})"
    delivered = float(s.sum())
    if abs(delivered - bid.energy_mwh) > rel_tol * bid.energy_mwh + 1e-6:
        return f"delivers {delivered:g} MWh, bid needs {bid.energy_mwh:g} MWh (tol {rel_tol:.1%})"
    return None


def validate_schedules(bids: Iterable[Bid], schedules: Mapping[str, np.ndarray]) -> dict[str, str]:
    """``{bid_id: error}`` for every bid whose schedule is missing or invalid (empty if all ok)."""
    errors = {}
    for b in bids:
        if b.id not in schedules:
            errors[b.id] = "missing schedule"
            continue
        err = schedule_error(b, schedules[b.id])
        if err:
            errors[b.id] = err
    return errors


def _fill(bid: Bid, hour_order: Iterable[int]) -> np.ndarray:
    """Run at max_mw through ``hour_order`` until the energy is met (last hour partial)."""
    s = np.zeros(HOURS)
    remaining = bid.energy_mwh
    for h in hour_order:
        if remaining <= 0:
            break
        x = min(bid.max_mw, remaining)
        s[h] = x
        remaining -= x
    return s


# --- strategy 1: default ---------------------------------------------------------------------


def default_schedule(bid: Bid) -> np.ndarray:
    return _fill(bid, range(bid.earliest_hour, bid.deadline_hour))


def default_schedules(bids: Iterable[Bid]) -> Schedule:
    return {b.id: default_schedule(b) for b in bids}


# --- strategy 2: independent-average ---------------------------------------------------------


def independent_average(bids: list[Bid], plan_sim: DaySimulator, other_added_24=None) -> Schedule:
    """Standard carbon-aware tool: lowest forecast average-intensity hours first.

    The signal is ``plan_sim.avg_intensity(other_added_24)``: the forecast with background load
    (if any) and NO user loads, identical for every bid.
    """
    intensity = plan_sim.avg_intensity(_bg(other_added_24))
    out = {}
    for b in bids:
        hrs = np.arange(b.earliest_hour, b.deadline_hour)
        order = hrs[np.argsort(intensity[hrs], kind="stable")]  # ties -> earliest hour
        out[b.id] = _fill(b, order)
    return out


# --- coordinator core (strategy 4) and strategy 3 --------------------------------------------


def greedy_place(plan_sim: DaySimulator, fixed_added_24, bids: list[Bid],
                 block_frac: float = 0.01, record_trace: bool = False
                 ) -> Schedule | tuple[Schedule, list[dict]]:
    """Greedy block placement minimizing incremental simulated emissions (design B3, strategy 4).

    Blocks of ``block_frac`` x each bid's energy are placed round-robin across bids ordered by
    earliest deadline (ties: earliest_hour, then id). Each block goes to the feasible slot (inside
    the window, with MW headroom; a partial block if headroom is short) with the lowest
    incremental CO2 per MWh given ``fixed_added_24`` plus every block already placed (for full
    blocks this is the lowest incremental CO2); ties -> earliest slot. A bid's last block is its
    remaining energy. Deterministic.

    Returns ``schedules`` or, with ``record_trace=True``, ``(schedules, trace)`` where trace is a
    list of ``{step, bid_id, hour, mwh, incremental_co2_t}`` (one entry per placement).
    """
    added = _bg(fixed_added_24).copy()
    co2 = plan_sim.hourly_co2_at(_ALL_HOURS, added)
    sched = {b.id: np.zeros(HOURS) for b in bids}
    order = sorted((b for b in bids if b.energy_mwh > 0),
                   key=lambda b: (b.deadline_hour, b.earliest_hour, b.id))
    remaining = {b.id: float(b.energy_mwh) for b in order}
    block = {b.id: block_frac * b.energy_mwh for b in order}
    windows = {b.id: np.arange(b.earliest_hour, b.deadline_hour) for b in order}
    trace: list[dict] = []
    active = list(order)
    step = 0
    while active:
        still = []
        for b in active:
            rem = remaining[b.id]
            tol = 1e-9 * b.energy_mwh
            amt = min(block[b.id], rem)
            if rem - amt <= tol:
                amt = rem
            hrs = windows[b.id]
            head = b.max_mw - sched[b.id][hrs]
            m = np.minimum(amt, head)
            ok = m > tol
            if not ok.any():  # cannot happen for a feasible bid; never loop forever
                raise RuntimeError(f"bid {b.id}: no headroom left with {rem:g} MWh unplaced")
            hrs, m = hrs[ok], m[ok]
            new = plan_sim.hourly_co2_at(hrs, added[hrs] + m)
            inc = new - co2[hrs]
            j = int(np.argmin(inc / m))
            h, x = int(hrs[j]), float(m[j])
            added[h] += x
            co2[h] = new[j]
            sched[b.id][h] += x
            remaining[b.id] = rem - x
            if record_trace:
                trace.append({"step": step, "bid_id": b.id, "hour": h, "mwh": x,
                              "incremental_co2_t": float(inc[j])})
            step += 1
            if remaining[b.id] > tol:
                still.append(b)
        active = still
    return (sched, trace) if record_trace else sched


def independent_marginal(bids: list[Bid], plan_sim: DaySimulator, background_24=None) -> Schedule:
    """Strategy 3: each bid alone greedily places its own blocks, assuming background + every
    OTHER user bid at default; then all execute together."""
    bg = _bg(background_24)
    defaults = default_schedules(bids)
    total_default = sum_schedules(defaults)
    out = {}
    for b in bids:
        fixed = bg + total_default - defaults[b.id]
        out[b.id] = greedy_place(plan_sim, fixed, [b])[b.id]
    return out


STRATEGIES: dict[str, Callable[..., Schedule]] = {
    "default": lambda bids, plan_sim, background_24=None: default_schedules(bids),
    "independent_average": independent_average,
    "independent_marginal": independent_marginal,
}


# --- herding metric --------------------------------------------------------------------------


@dataclass(frozen=True)
class HerdingResult:
    predicted_t: float  # sum_i [E(all default) - E(i shifts alone, others default)]
    realized_t: float  # E(all default) - E(all shift together)
    gap_t: float  # predicted - realized
    e_default_t: float  # E(all default), scored
    e_together_t: float  # E(all shift together), scored
    schedules: Schedule

    @property
    def gap_pct(self) -> float | None:
        """100 x gap / predicted, or None when predicted <= 0 (undefined)."""
        return 100.0 * self.gap_t / self.predicted_t if self.predicted_t > 0 else None

    def as_metric(self) -> dict:
        """The ``schemas.HerdingMetric`` fields."""
        return {"predicted_t": self.predicted_t, "realized_t": self.realized_t,
                "gap_t": self.gap_t}


def herding_metric(bids: list[Bid], plan_sim: DaySimulator, score_sim: DaySimulator,
                   strategy: str | Callable[..., Schedule], background_24=None) -> HerdingResult:
    """Herding gap for ``strategy`` (B3 / pre-registration). Plans on ``plan_sim``, scores every
    emissions number on ``score_sim``, background fixed throughout."""
    fn = STRATEGIES[strategy] if isinstance(strategy, str) else strategy
    bg = _bg(background_24)
    defaults = default_schedules(bids)
    shifted = fn(bids, plan_sim, bg)
    d_total = bg + sum_schedules(defaults)
    rows = [d_total]
    rows += [d_total - defaults[b.id] + shifted[b.id] for b in bids]
    rows.append(bg + sum_schedules(shifted))
    e = score_sim.emissions_batch(np.vstack(rows))
    e_def, e_solo, e_tog = float(e[0]), e[1:-1], float(e[-1])
    predicted = float(np.sum(e_def - e_solo))
    realized = e_def - e_tog
    return HerdingResult(predicted_t=predicted, realized_t=realized, gap_t=predicted - realized,
                         e_default_t=e_def, e_together_t=e_tog, schedules=shifted)
