"""Settlement (design B5): coalition values, Shapley shares, payouts, leave-one-out.

Pinned definitions (M5 orchestrator decisions, implemented exactly)
------------------------------------------------------------------
Players are the user bids, indexed by their position in the request; a coalition is a bitmask
(bit ``i`` = bid ``i``). ``fixed_components`` is the list of fixed added-load 24-vectors that
every plan and every score includes (the background population's summed schedule, plus, for a
direct leave-one-out recomputation, a removed bid's strategy-3 schedule). Planning uses
``plan_sim`` (forecast inputs), every emissions number in a value uses ``score_sim`` (actuals).

* **Baseline (C).** ``s3_i``: bid ``i``'s strategy-3 (independent_marginal) schedule, planned
  assuming the other user bids at default. It does not depend on coalition membership, so it is
  computed once and passed in.
* **Coalition plan (C).** For coalition ``S``: ``greedy_place`` for the members with fixed load
  ``fixed + sum_{j not in S} s3_j``. Per-coalition planning-time fallback: if
  ``E_plan(members greedy + non-members s3) > E_plan(all s3)``, members use ``s3`` instead.
  ``v(S) = E_score(all s3) - E_score(S's schedule + non-members s3)``; ``v(empty) = 0``.
  ``v({i})`` may be positive: value from reacting to the others' known schedules, not
  coordination. The full coalition's plan IS the "coordinated" strategy (same planning, same
  fallback rule).
* **Shares.** Exact Shapley over all ``2^n`` coalitions for ``n <= EXACT_MAX_PLAYERS`` (8):
  method ``"exact"``, ``coalitions_evaluated = 2^n``. Above that, marginal contribution
  (:func:`marginal_shares`, ``n + 1`` coalitions): method ``"marginal"``,
  ``coalitions_evaluated`` = distinct coalitions simulated (main game plus leave-one-out games).
  Both are exactly efficient: shares sum to ``v(N)``.
* **Receipt games (D).** ``u3(S) = E_score(all default) - E_score(S at s3, others default)`` and
  ``u2(S)`` likewise with strategy-2 schedules; both are membership-independent, so they are
  pure batched simulator calls (:func:`scoring_game_values`).
* **Payouts (E).** ``payout_i = max(0, shapley_t_i) x carbon_price``; if the sum exceeds
  ``pool_usd`` every payout is scaled by ``pool / sum`` and ``prorated = True``. A negative share
  pays $0.
* **Leave-one-out (F).** Leaving the COALITION, not the market: bid ``i`` stays and runs
  strategy 3 alone. The game on ``N \\ {i}`` uses ``v_{-i}(S) = v(S)`` for ``S`` not containing
  ``i`` (the same coalition values, no extra simulation in the exact case);
  ``total_avoided_t = v(N \\ {i})``; shares and payouts follow the same pool rule.

Determinism and symmetry
------------------------
Added-load vectors are summed with :func:`canon_sum` (``math.fsum`` per slot: correctly rounded,
so independent of summation order). Together with ``strategies.greedy_order_key`` this makes
the coalition values of identical bids bit-identical (exact symmetry), makes a zero-energy bid's
marginal contribution exactly zero, and makes a direct leave-one-out recomputation reproduce the
same values. No randomness.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from clearinghouse.inputs import HOURS
from clearinghouse.schemas import Bid
from clearinghouse.simulator import DaySimulator
from clearinghouse.strategies import greedy_place

EXACT_MAX_PLAYERS = 8

Mask = int


# --- helpers ---------------------------------------------------------------------------------


def canon_sum(vectors: Iterable[Sequence[float]]) -> np.ndarray:
    """Per-slot ``math.fsum`` of 24-vectors: exact-then-rounded, so the result is independent of
    the order of ``vectors`` (identical multisets give bit-identical sums)."""
    cols = [np.asarray(v, dtype=float).tolist() for v in vectors]
    if not cols:
        return np.zeros(HOURS)
    return np.array([math.fsum(c) for c in zip(*cols, strict=True)])


def members(mask: Mask, n: int) -> list[int]:
    return [i for i in range(n) if mask >> i & 1]


def mask_of(indices: Iterable[int]) -> Mask:
    m = 0
    for i in indices:
        m |= 1 << i
    return m


# --- Shapley ---------------------------------------------------------------------------------


def shapley_exact(players: Sequence[int], value: Callable[[Mask], float]) -> list[float]:
    """Exact Shapley values of ``players`` (global indices) in the game whose coalition values are
    ``value(global_mask)`` for every subset of ``players``. Returns values aligned with
    ``players``."""
    k = len(players)
    if k == 0:
        return []
    glob = [0] * (1 << k)
    for local in range(1, 1 << k):
        low = local & -local
        glob[local] = glob[local ^ low] | (1 << players[low.bit_length() - 1])
    vals = [value(g) for g in glob]
    fk = math.factorial(k)
    w = [math.factorial(s) * math.factorial(k - s - 1) / fk for s in range(k)]
    size = [bin(local).count("1") for local in range(1 << k)]
    out = []
    for li in range(k):
        bit = 1 << li
        acc = 0.0
        for local in range(1 << k):
            if local & bit:
                continue
            acc += w[size[local]] * (vals[local | bit] - vals[local])
        out.append(acc)
    return out


def marginal_masks(players: Sequence[int]) -> list[Mask]:
    """The coalitions :func:`marginal_shares` needs: all of ``players``, and all but each one."""
    full = mask_of(players)
    return [full, *(full ^ (1 << i) for i in players)]


def marginal_shares(players: Sequence[int], value: Callable[[Mask], float]) -> list[float]:
    """Shares from each player's marginal contribution to the whole group (``n + 1`` coalition
    values instead of ``2^n``), used above ``EXACT_MAX_PLAYERS``.

    ``m_i = v(P) - v(P \\ {i})``. A player with ``m_i < 0`` (the group does better without it)
    keeps ``m_i``; the helpers split the rest, ``v(P) - sum(negative m)``, in proportion to their
    ``m_i``. Shares sum to ``v(P)`` exactly; for an additive game they equal the Shapley values.
    If nobody has ``m_i > 0``, ``v(P)`` is split equally.
    """
    if not players:
        return []
    full = mask_of(players)
    vf = value(full)
    m = [vf - value(full ^ (1 << i)) for i in players]
    pos = math.fsum(x for x in m if x > 0)
    if pos <= 0:
        return [vf / len(players)] * len(players)
    rest = vf - math.fsum(x for x in m if x < 0)
    return [x if x < 0 else x / pos * rest for x in m]


# --- payouts ---------------------------------------------------------------------------------


def payouts(shapley_t: Sequence[float], carbon_price: float, pool_usd: float
            ) -> tuple[list[float], bool]:
    """``(payout_usd per player, prorated)``: ``max(0, share) x price``, scaled down to the pool
    if the total exceeds it (then the payouts sum to ``pool_usd``)."""
    raw = [max(0.0, float(s)) * float(carbon_price) for s in shapley_t]
    total = math.fsum(raw)
    if total > pool_usd:
        f = float(pool_usd) / total
        return [r * f for r in raw], True
    return raw, False


# --- coalition planning game -----------------------------------------------------------------


@dataclass
class CoalitionPlan:
    mask: Mask
    schedules: dict[str, np.ndarray]  # every player's executed schedule (members + non-members)
    member_schedules: dict[str, np.ndarray]  # members' schedules after the fallback rule
    fallback_fired: bool
    total_added: np.ndarray  # fixed + all player schedules (24-vector)
    e_plan_t: float
    trace: list[dict] | None = None


@dataclass
class CoalitionGame:
    """Lazily planned and scored coalition values ``v(S)`` (pinned definition C)."""

    bids: list[Bid]
    s3: dict[str, np.ndarray]
    fixed_components: list[np.ndarray]
    plan_sim: DaySimulator
    score_sim: DaySimulator
    block_frac: float = 0.01
    plans: dict[Mask, CoalitionPlan] = field(default_factory=dict)
    values: dict[Mask, float] = field(default_factory=dict)
    e_score: dict[Mask, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.n = len(self.bids)
        self.ids = [b.id for b in self.bids]
        self.full: Mask = (1 << self.n) - 1
        base = canon_sum(list(self.fixed_components) + [self.s3[i] for i in self.ids])
        self.base_total = base
        self.e_plan_base = float(self.plan_sim.emissions_batch(base[None, :])[0])
        self.e_score_base = float(self.score_sim.emissions_batch(base[None, :])[0])

    def plan(self, mask: Mask, record_trace: bool = False) -> CoalitionPlan:
        cached = self.plans.get(mask)
        if cached is not None and (cached.trace is not None or not record_trace):
            return cached
        mem = members(mask, self.n)
        mem_ids = [self.ids[i] for i in mem]
        non_ids = [self.ids[i] for i in range(self.n) if not mask >> i & 1]
        trace = None
        if mem:
            fixed = canon_sum(list(self.fixed_components) + [self.s3[j] for j in non_ids])
            res = greedy_place(self.plan_sim, fixed, [self.bids[i] for i in mem],
                               block_frac=self.block_frac, record_trace=record_trace)
            greedy, trace = res if record_trace else (res, None)
            cand = canon_sum(list(self.fixed_components) + [greedy[i] for i in mem_ids]
                             + [self.s3[j] for j in non_ids])
            e_cand = float(self.plan_sim.emissions_batch(cand[None, :])[0])
            fired = e_cand > self.e_plan_base
        else:
            greedy, cand, e_cand, fired = {}, self.base_total, self.e_plan_base, False
        if fired:
            mem_sched = {i: self.s3[i] for i in mem_ids}
            cand, e_cand = self.base_total, self.e_plan_base
        else:
            mem_sched = {i: np.asarray(greedy[i], dtype=float) for i in mem_ids}
        sched = {i: (mem_sched[i] if i in mem_sched else self.s3[i]) for i in self.ids}
        p = CoalitionPlan(mask=mask, schedules=sched, member_schedules=mem_sched,
                          fallback_fired=fired, total_added=cand, e_plan_t=e_cand, trace=trace)
        self.plans[mask] = p
        return p

    def evaluate(self, masks: Iterable[Mask]) -> None:
        """Plan and score (one batched simulator call) every mask not yet valued."""
        todo = [m for m in dict.fromkeys(masks) if m not in self.values]
        if not todo:
            return
        plans = [self.plan(m) for m in todo]
        e = self.score_sim.emissions_batch(np.vstack([p.total_added for p in plans]))
        for m, p, em in zip(todo, plans, e, strict=True):
            self.e_score[m] = float(em)
            # all-s3 schedule (empty coalition or fallback) -> exactly 0
            self.values[m] = (0.0 if p.total_added is self.base_total
                              else self.e_score_base - float(em))

    def v(self, mask: Mask) -> float:
        if mask not in self.values:
            self.evaluate([mask])
        return self.values[mask]


def scoring_game_values(score_sim: DaySimulator, fixed_components: list[np.ndarray],
                        base: Sequence[np.ndarray], alt: Sequence[np.ndarray],
                        masks: Iterable[Mask]) -> dict[Mask, float]:
    """``u(S) = E(all base) - E(S at alt, others at base)`` for each mask (membership-independent
    schedules, so pure scoring in one batch). ``u(empty) = 0`` exactly."""
    masks = list(dict.fromkeys(masks))
    n = len(base)
    fixed = list(fixed_components)
    rows = [canon_sum(fixed + list(base))]
    for m in masks:
        rows.append(canon_sum(fixed + [alt[i] if m >> i & 1 else base[i] for i in range(n)]))
    e = score_sim.emissions_batch(np.vstack(rows))
    return {m: (0.0 if m == 0 else float(e[0] - e[k + 1])) for k, m in enumerate(masks)}


# --- settlement ------------------------------------------------------------------------------


def _shares(players: list[int], value_batch: Callable[[list[Mask]], None],
            value: Callable[[Mask], float], n_total: int) -> tuple[list[float], str]:
    """Exact Shapley if ``n_total <= EXACT_MAX_PLAYERS`` else marginal contribution."""
    if n_total <= EXACT_MAX_PLAYERS:
        k = len(players)
        masks = [mask_of(players[j] for j in range(k) if local >> j & 1)
                 for local in range(1 << k)]
        value_batch(masks)
        return shapley_exact(players, value), "exact"
    value_batch(marginal_masks(players))
    return marginal_shares(players, value), "marginal"


@dataclass
class SettlementResult:
    method: str
    coalitions_evaluated: int
    total_avoided_t: float
    shapley_t: list[float]
    payout_usd: list[float]
    prorated: bool
    leave_one_out: dict[str, dict]
    game: CoalitionGame


def settle(bids: list[Bid], s3: dict[str, np.ndarray], fixed_components: list[np.ndarray],
           plan_sim: DaySimulator, score_sim: DaySimulator, carbon_price: float,
           pool_usd: float, game: CoalitionGame | None = None,
           leave_one_out: bool = True) -> SettlementResult:
    """Shapley settlement of the coalition game (C), payouts (E) and leave-one-out games (F).

    ``game`` may be passed pre-built (e.g. with the full coalition already planned with a trace).
    """
    if game is None:
        game = CoalitionGame(bids, s3, fixed_components, plan_sim, score_sim)
    n = game.n
    players = list(range(n))
    if n > EXACT_MAX_PLAYERS and leave_one_out:
        # the main game's and every leave-one-out game's coalitions, in one batch
        game.evaluate([*marginal_masks(players), *(
            m for i in players for m in marginal_masks([j for j in players if j != i]))])
    shap, method = _shares(players, game.evaluate, game.v, n)
    total = game.v(game.full)
    pay, prorated = payouts(shap, carbon_price, pool_usd)
    loo: dict[str, dict] = {}
    if leave_one_out:
        for i in players:
            rest = [j for j in players if j != i]
            sh_i, _ = _shares(rest, game.evaluate, game.v, n)
            pay_i, _ = payouts(sh_i, carbon_price, pool_usd)
            loo[game.ids[i]] = {
                "total_avoided_t": game.v(game.full ^ (1 << i)),
                "per_bid": [{"bid_id": game.ids[j], "shapley_t": float(s), "payout_usd": float(p)}
                            for j, s, p in zip(rest, sh_i, pay_i, strict=True)],
            }
    evaluated = (1 << n) if method == "exact" else len(game.values)
    return SettlementResult(method=method, coalitions_evaluated=evaluated,
                            total_avoided_t=float(total), shapley_t=[float(s) for s in shap],
                            payout_usd=[float(p) for p in pay], prorated=prorated,
                            leave_one_out=loo, game=game)


def receipt_shapley(score_sim: DaySimulator, fixed_components: list[np.ndarray],
                    base: Sequence[np.ndarray], alt: Sequence[np.ndarray]) -> list[float]:
    """Shares of the membership-independent scoring game ``u`` (D): exact Shapley for
    ``n <= 8``, else marginal contribution (same rule as the settlement)."""
    n = len(base)
    cache: dict[Mask, float] = {}

    def batch(masks: list[Mask]) -> None:
        new = [m for m in masks if m not in cache]
        if new:
            cache.update(scoring_game_values(score_sim, fixed_components, base, alt, new))

    shap, _ = _shares(list(range(n)), batch, cache.__getitem__, n)
    return shap
