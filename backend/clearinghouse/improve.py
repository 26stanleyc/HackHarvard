"""Robust local search for the coordinator (M13 candidate; design doc M13).

Starts from given schedules (normally every member's strategy-3 plan, i.e. what each company
would do alone) and repeatedly moves energy of one bid from one slot to another inside its window,
accepting a move only if it lowers planned emissions on average over the planning scenarios AND in
at least a fraction ``q`` of them (a move that helps on average but hurts in many scenarios is a
bet on the forecast, which is what loses on actuals). Step sizes shrink from coarse to fine.
Deterministic; every schedule stays feasible (energy, window, max MW).
"""

from __future__ import annotations

import os

import numpy as np

from clearinghouse.schemas import Bid

COORDINATOR_MODES = ("greedy", "improve")
DEFAULT_COORDINATOR = "greedy"
DEFAULT_Q = 0.8
STEP_FRACS = (0.10, 0.05, 0.02, 0.01)
MAX_MOVES = 5000


def coordinator_mode() -> str:
    mode = os.environ.get("COORDINATOR", "").strip().lower() or DEFAULT_COORDINATOR
    if mode not in COORDINATOR_MODES:
        raise ValueError(f"COORDINATOR must be one of {COORDINATOR_MODES}, got {mode!r}")
    return mode


def improve_q() -> float:
    return float(os.environ.get("IMPROVE_Q", DEFAULT_Q))


def _co2_s(plan_sim, hours: np.ndarray, added: np.ndarray) -> np.ndarray:
    """Per-scenario CO2 at ``hours`` -> ``[S, len(hours)]`` (S = 1 for a plain simulator)."""
    f = getattr(plan_sim, "hourly_co2_at_scenarios", None)
    if f is not None:
        return f(hours, added)
    return np.asarray(plan_sim.hourly_co2_at(hours, added))[None]


def improve(plan_sim, fixed_added_24, bids: list[Bid], start: dict[str, np.ndarray],
            q: float | None = None) -> dict[str, np.ndarray]:
    q = improve_q() if q is None else q
    hours = np.arange(24)
    order = sorted((b for b in bids if b.energy_mwh > 0),
                   key=lambda b: (b.deadline_hour, b.earliest_hour, b.energy_mwh, b.max_mw, b.id))
    sched = {b.id: np.array(start[b.id], dtype=float) for b in bids}
    load = np.asarray(fixed_added_24, dtype=float) + sum(sched[b.id] for b in bids)
    base = _co2_s(plan_sim, hours, load)  # [S, 24]
    moves = 0
    for frac in STEP_FRACS:
        changed = True
        while changed and moves < MAX_MOVES:
            changed = False
            for b in order:
                d = frac * b.energy_mwh
                tol = 1e-9 * b.energy_mwh
                win = np.zeros(24, dtype=bool)
                win[b.earliest_hour:b.deadline_hour] = True
                src = win & (sched[b.id] >= d - tol)
                dst = win & (b.max_mw - sched[b.id] >= d - tol)
                if not src.any() or not dst.any():
                    continue
                rem = _co2_s(plan_sim, hours, load - d) - base  # [S, 24] (<0 = saving)
                add = _co2_s(plan_sim, hours, load + d) - base  # [S, 24]
                ch = rem[:, :, None] + add[:, None, :]  # [S, a, b]
                mean = ch.mean(axis=0)
                frac_better = (ch < 0).mean(axis=0)
                ok = (src[:, None] & dst[None, :] & ~np.eye(24, dtype=bool)
                      & (mean < -1e-9) & (frac_better >= q))
                if not ok.any():
                    continue
                score = np.where(ok, mean, np.inf)
                a, c = np.unravel_index(int(np.argmin(score)), score.shape)
                sched[b.id][a] -= d
                sched[b.id][c] += d
                if abs(sched[b.id][a]) < tol:
                    sched[b.id][a] = 0.0
                load[a] -= d
                load[c] += d
                base[:, [a, c]] = _co2_s(plan_sim, np.array([a, c]), load[[a, c]])
                moves += 1
                changed = True
    return sched
