"""M5 settlement checks: exact Shapley and marginal-contribution shares, efficiency, symmetry,
zero-energy bid, payouts and proration, leave-one-out vs direct recomputation, receipt
efficiency identities. Calibration TRAINING days and demo days only (never herding-gate days)."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from clearinghouse.clearing import clear_with_details
from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data import load_days
from clearinghouse.inputs import actual_inputs, yesterday_forecast_inputs
from clearinghouse.params import load_params
from clearinghouse.schemas import Bid
from clearinghouse.settlement import (
    EXACT_MAX_PLAYERS,
    canon_sum,
    marginal_masks,
    marginal_shares,
    payouts,
    settle,
    shapley_exact,
)
from clearinghouse.simulator import DaySimulator

if not (DATA_PROCESSED / "eia930_erco_hourly.parquet").is_file():
    pytest.skip("processed data absent", allow_module_level=True)

GATE = set(load_days()["herding_gate_days"])
PARAMS, _ = load_params()


def make_sims(day: str) -> tuple[DaySimulator, DaySimulator]:
    assert day not in GATE
    return (DaySimulator(yesterday_forecast_inputs(day), PARAMS),
            DaySimulator(actual_inputs(day), PARAMS))


def bid(bid_id, e, a, d, m) -> Bid:
    return Bid(id=bid_id, name=bid_id, load_type="t", energy_mwh=e, earliest_hour=a,
               deadline_hour=d, max_mw=m)


# --- pure Shapley / payout math --------------------------------------------------------------


def brute_shapley(n, v):
    out = [0.0] * n
    perms = list(itertools.permutations(range(n)))
    for p in perms:
        m = 0
        for i in p:
            out[i] += v(m | 1 << i) - v(m)
            m |= 1 << i
    return [x / len(perms) for x in out]


def test_shapley_exact_matches_brute_force():
    rng = np.random.default_rng(1)
    for n in (1, 2, 3, 4, 5):
        table = rng.normal(size=1 << n)
        table[0] = 0.0
        v = table.__getitem__
        ex = shapley_exact(list(range(n)), v)
        assert ex == pytest.approx(brute_shapley(n, v), abs=1e-12)
        assert sum(ex) == pytest.approx(table[(1 << n) - 1], abs=1e-12)


def test_shapley_exact_subgame_uses_global_masks():
    # glove game: player 0 has a left glove, 1 and 2 right gloves; v = pairs formed
    def v(m):
        return float(min(m & 1, ((m >> 1) & 1) + ((m >> 2) & 1)))
    assert shapley_exact([0, 1, 2], v) == pytest.approx([2 / 3, 1 / 6, 1 / 6])
    assert shapley_exact([0, 2], v) == pytest.approx([0.5, 0.5])


def test_marginal_shares_efficient_and_cheap():
    rng = np.random.default_rng(2)
    n = 10
    players = list(range(n))
    table = rng.normal(size=1 << n)
    table[0] = 0.0
    seen = []

    def v(m):
        seen.append(m)
        return float(table[m])
    sh = marginal_shares(players, v)
    assert math.fsum(sh) == pytest.approx(table[-1], abs=1e-9)
    assert set(seen) == set(marginal_masks(players))  # n + 1 coalitions, not 2^n
    assert len(set(seen)) == n + 1
    # additive game: equal to the Shapley values (the weights)
    w = rng.normal(size=n)

    def add(m):
        return float(sum(w[i] for i in range(n) if m >> i & 1))
    assert marginal_shares(players, add) == pytest.approx(list(w), abs=1e-12)


def test_marginal_shares_negative_contributor_and_no_helpers():
    # v: player 0 adds 6, player 1 adds 2, player 2 costs 3 (the group is better without it)
    w = [6.0, 2.0, -3.0]

    def v(m):
        return sum(w[i] for i in range(3) if m >> i & 1)
    sh = marginal_shares([0, 1, 2], v)
    assert sh[2] == pytest.approx(-3.0)  # keeps its negative contribution
    assert sh[0] / sh[1] == pytest.approx(3.0)  # helpers split the rest 6:2
    assert sum(sh) == pytest.approx(5.0)
    # nobody helps: v(N) split equally
    assert marginal_shares([0, 1], lambda m: -1.0 if m else 0.0) == pytest.approx([-0.5, -0.5])


def test_payouts_proration_and_negative_share():
    pay, pr = payouts([10.0, -5.0, 30.0], 50.0, 1e6)
    assert pay == [500.0, 0.0, 1500.0] and pr is False
    pay, pr = payouts([10.0, -5.0, 30.0], 50.0, 1000.0)
    assert pr is True
    assert sum(pay) == pytest.approx(1000.0, rel=1e-12)
    assert pay[1] == 0.0 and pay[2] == pytest.approx(3 * pay[0])
    pay, pr = payouts([-1.0, -2.0], 50.0, 0.0)
    assert pay == [0.0, 0.0] and pr is False


def test_canon_sum_order_independent():
    rng = np.random.default_rng(5)
    vecs = [rng.uniform(0, 1e4, 24) for _ in range(9)]
    a = canon_sum(vecs)
    for _ in range(5):
        order = rng.permutation(9)
        assert np.array_equal(canon_sum([vecs[i] for i in order]), a)


# --- the coalition game on real days ---------------------------------------------------------

CASES = [  # (day, background %, bids); training day 2024-02-04, demo days 01-16 and 08-20
    ("2024-02-04", 5.0, [bid("ev", 3000, 0, 8, 600), bid("dc", 4000, 0, 24, 300),
                         bid("office", 900, 6, 18, 200), bid("cold", 1500, 0, 24, 120)]),
    ("2024-01-16", 10.0, [bid("ev", 3000, 0, 8, 600), bid("dc", 4000, 0, 24, 300),
                          bid("eve", 2000, 17, 24, 500), bid("gen", 800, 9, 17, 150),
                          bid("ev2", 2500, 1, 7, 700)]),
    ("2024-08-20", 5.0, [bid("dc", 4000, 0, 24, 300), bid("office", 900, 6, 18, 200),
                         bid("eve", 2000, 17, 24, 500)]),
]


@pytest.fixture(scope="module", params=range(len(CASES)))
def case(request):
    day, bg, bids = CASES[request.param]
    plan, score = make_sims(day)
    r, det = clear_with_details(day, bids, bg, 50.0, 1e6, plan, score)
    return day, bg, bids, plan, score, r, det


def test_shapley_efficiency(case):
    *_, r, det = case
    s = r["settlement"]
    assert sum(b["shapley_t"] for b in s["per_bid"]) == pytest.approx(
        s["total_avoided_t"], abs=1e-6)
    assert s["total_avoided_t"] == det.game.v(det.game.full)
    assert det.game.v(0) == 0.0
    for i, lo in s["leave_one_out"].items():
        assert sum(b["shapley_t"] for b in lo["per_bid"]) == pytest.approx(
            lo["total_avoided_t"], abs=1e-6)
        assert [b["bid_id"] for b in lo["per_bid"]] == [
            b["bid_id"] for b in s["per_bid"] if b["bid_id"] != i]


def test_receipt_efficiency_identities(case):
    *_, r, det = case
    s = r["strategies"]
    per = r["settlement"]["per_bid"]
    e_coord = s["coordinated"]["total_co2_t"]
    assert sum(b["vs_default_t"] for b in per) == pytest.approx(
        s["default"]["total_co2_t"] - e_coord, abs=1e-6)
    assert sum(b["vs_independent_average_t"] for b in per) == pytest.approx(
        s["independent_average"]["total_co2_t"] - e_coord, abs=1e-6)
    assert sum(det.phi_u3) == pytest.approx(
        s["default"]["total_co2_t"] - s["independent_marginal"]["total_co2_t"], abs=1e-6)


def test_payouts_follow_shares(case):
    *_, r, _ = case
    for b in r["settlement"]["per_bid"]:
        assert b["payout_usd"] == pytest.approx(max(0.0, b["shapley_t"]) * 50.0, rel=1e-12)
        if b["shapley_t"] < 0:
            assert b["payout_usd"] == 0.0
    assert r["settlement"]["prorated"] is False


def test_leave_one_out_matches_direct_recomputation(case):
    """Leaving the coalition = the bid runs strategy 3 alone as a fixed non-member: re-run the
    whole settlement (fresh planning of every coalition) without it and compare."""
    _, _, bids, plan, score, r, det = case
    s3 = det.schedules["independent_marginal"]
    for b in bids:
        rest = [x for x in bids if x.id != b.id]
        direct = settle(rest, {x.id: s3[x.id] for x in rest}, [det.background_24, s3[b.id]],
                        plan, score, 50.0, 1e6, leave_one_out=False)
        lo = r["settlement"]["leave_one_out"][b.id]
        assert direct.total_avoided_t == pytest.approx(lo["total_avoided_t"], abs=1e-6)
        for j, x in enumerate(lo["per_bid"]):
            assert x["bid_id"] == rest[j].id
            assert direct.shapley_t[j] == pytest.approx(x["shapley_t"], abs=1e-6)
            assert direct.payout_usd[j] == pytest.approx(x["payout_usd"], abs=1e-6)


def test_proration_when_pool_short():
    day, bg, bids = CASES[1]
    plan, score = make_sims(day)
    r, _ = clear_with_details(day, bids, bg, 50.0, 1e9, plan, score)
    full = sum(b["payout_usd"] for b in r["settlement"]["per_bid"])
    assert full > 0, "need a positive total payout to test proration"
    pool = full / 3
    r2, _ = clear_with_details(day, bids, bg, 50.0, pool, plan, score)
    s2 = r2["settlement"]
    assert s2["prorated"] is True and s2["pool_usd"] == pool
    assert sum(b["payout_usd"] for b in s2["per_bid"]) == pytest.approx(pool, rel=1e-12)
    for a, b in zip(r["settlement"]["per_bid"], s2["per_bid"], strict=True):
        assert b["payout_usd"] == pytest.approx(a["payout_usd"] / 3, rel=1e-12)
    for lo in s2["leave_one_out"].values():
        assert sum(b["payout_usd"] for b in lo["per_bid"]) <= pool * (1 + 1e-12)


def test_symmetry_identical_bids_and_zero_energy_bid():
    """Two identical bids (different ids) get identical shares, even with a third bid that
    shares their window and sorts between them by id; a zero-energy bid gets exactly zero."""
    day = "2024-01-16"
    plan, score = make_sims(day)
    # Tight shared window: with the pre-M5 tie-break (deadline, earliest, id) the twins' shares
    # differed by ~4 t here because "m_other" sorted between them.
    bids = [bid("a_twin", 3020.9, 7, 12, 772.7), bid("m_other", 2902.6, 7, 12, 871.4),
            bid("z_twin", 3020.9, 7, 12, 772.7), bid("zero", 0.0, 3, 9, 100),
            bid("ev", 3000, 0, 8, 600)]
    r, det = clear_with_details(day, bids, 10.0, 50.0, 1e6, plan, score)
    g = det.game
    a, z = 1 << 0, 1 << 2
    for m in range(1 << 5):  # coalition values are bit-identical under the twin swap
        if not m & (a | z):
            assert g.v(m | a) == g.v(m | z)
            assert g.v(m | 1 << 3) == g.v(m)  # zero-energy bid adds exactly nothing
    per = {b["bid_id"]: b for b in r["settlement"]["per_bid"]}
    for key in ("shapley_t", "payout_usd", "vs_default_t", "vs_independent_average_t"):
        assert per["a_twin"][key] == pytest.approx(per["z_twin"][key], rel=1e-12, abs=1e-9), key
    assert per["zero"]["shapley_t"] == 0.0 and per["zero"]["payout_usd"] == 0.0
    assert per["zero"]["vs_default_t"] == 0.0
    assert per["zero"]["vs_independent_average_t"] == 0.0
    assert abs(per["a_twin"]["shapley_t"]) > 0 or abs(per["ev"]["shapley_t"]) > 0
    for name, st in r["strategies"].items():
        assert sum(st["schedule"]["zero"]) == 0.0, name
    lo = r["settlement"]["leave_one_out"]
    assert lo["zero"]["total_avoided_t"] == r["settlement"]["total_avoided_t"]
    for i in ("a_twin", "m_other", "ev"):
        z = {b["bid_id"]: b["shapley_t"] for b in lo[i]["per_bid"]}
        assert z["zero"] == 0.0
    print("\nsymmetry shares:", {k: round(v["shapley_t"], 6) for k, v in per.items()})


def test_marginal_above_eight_bids():
    day = "2024-02-04"
    plan, score = make_sims(day)
    rng = np.random.default_rng(9)
    bids = []
    for i in range(9):
        a = int(rng.integers(0, 18))
        d = min(24, a + int(rng.integers(3, 7)))
        m = float(rng.uniform(100, 400))
        bids.append(bid(f"m{i}", 0.6 * m * (d - a), a, d, m))
    r, det = clear_with_details(day, bids, 5.0, 50.0, 1e6, plan, score)
    s = r["settlement"]
    n = len(bids)
    assert n > EXACT_MAX_PLAYERS
    assert s["method"] == "marginal"
    # full + all-but-one + all-but-two: 1 + n + n(n-1)/2, far below 2^n
    assert s["coalitions_evaluated"] == len(det.game.values) == 1 + n + n * (n - 1) // 2
    assert sum(b["shapley_t"] for b in s["per_bid"]) == pytest.approx(
        s["total_avoided_t"], abs=1e-6)
    assert len(s["leave_one_out"]) == n
    for i, lo in s["leave_one_out"].items():
        k = [b.id for b in bids].index(i)
        assert lo["total_avoided_t"] == det.game.v(det.game.full ^ (1 << k))
        assert sum(b["shapley_t"] for b in lo["per_bid"]) == pytest.approx(
            lo["total_avoided_t"], abs=1e-6)
    assert math.isfinite(s["total_avoided_t"])
