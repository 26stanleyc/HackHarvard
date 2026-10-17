"""Synthetic flexible-load population (design B3 background population, B4 adoption chart, M2 gate).

Stated distributions (fixed before any gate result was seen; cite these in the pitch)
-----------------------------------------------------------------------------------
Each draw has ``n`` bids (default 30). For each bid, independently, with ``rng =
numpy.random.default_rng(seed)``:

1. **Load type** ~ categorical over :data:`LOAD_TYPES` with these probabilities and windows
   (hours are CST slots; ``earliest`` inclusive, ``deadline`` exclusive; integers drawn
   uniformly inclusive of both ends):

   ===================== ===== ===================================== ==========================
   load type             prob  window                                energy duty U(lo, hi)
   ===================== ===== ===================================== ==========================
   ev_fleet_overnight    0.25  earliest 0..4, deadline 5..9          0.30-0.80
   ev_fleet_evening      0.10  earliest 16..19, deadline 24          0.30-0.80
   data_center_batch     0.20  earliest 0, deadline 24               0.30-0.80
   office_precooling     0.20  earliest 5..10, deadline 12..18       0.30-0.80
   cold_storage          0.10  earliest 0, deadline 24               0.15-0.35 (low duty)
   generic               0.15  earliest 0..20, length 3..12 h,       0.30-0.80
                               deadline = min(24, earliest + length)
   ===================== ===== ===================================== ==========================

2. **Relative size** ``max_mw ~ LogNormal(mu=0, sigma=1.0)`` (heavy-tailed: a few aggregators,
   many small sites).
3. **Energy** ``energy_mwh = duty x max_mw x window_hours`` with ``duty`` from the table, so every
   bid is feasible with slack (duty <= 0.8).
4. **Scaling.** All ``max_mw`` and ``energy_mwh`` are multiplied by one factor so that
   ``sum(max_mw) = share_pct / 100 x peak_mw`` (the pre-registered share definition: share of the
   day's actual peak demand). Scaling both keeps every bid feasible and keeps the population
   shape identical across shares for a given seed.

Bid ids are ``p00, p01, ...`` (sorted order == creation order).
"""

from __future__ import annotations

import numpy as np

from clearinghouse.schemas import Bid, bid_feasibility_error

DEFAULT_N = 30
SIZE_SIGMA = 1.0

# name -> probability, energy duty range U(lo, hi), display label (windows: see _window)
LOAD_TYPES: dict[str, dict] = {
    "ev_fleet_overnight": {"p": 0.25, "duty": (0.30, 0.80), "label": "EV fleet (overnight)"},
    "ev_fleet_evening": {"p": 0.10, "duty": (0.30, 0.80), "label": "EV fleet (evening)"},
    "data_center_batch": {"p": 0.20, "duty": (0.30, 0.80), "label": "Data center batch"},
    "office_precooling": {"p": 0.20, "duty": (0.30, 0.80), "label": "Office pre-cooling"},
    "cold_storage": {"p": 0.10, "duty": (0.15, 0.35), "label": "Cold storage"},
    "generic": {"p": 0.15, "duty": (0.30, 0.80), "label": "Flexible load"},
}


def _window(load_type: str, rng: np.random.Generator) -> tuple[int, int]:
    if load_type == "ev_fleet_overnight":
        return int(rng.integers(0, 5)), int(rng.integers(5, 10))
    if load_type == "ev_fleet_evening":
        return int(rng.integers(16, 20)), 24
    if load_type in ("data_center_batch", "cold_storage"):
        return 0, 24
    if load_type == "office_precooling":
        return int(rng.integers(5, 11)), int(rng.integers(12, 19))
    if load_type == "generic":
        e = int(rng.integers(0, 21))
        return e, min(24, e + int(rng.integers(3, 13)))
    raise ValueError(load_type)


def distribution_summary() -> dict:
    """JSON-able statement of the distributions (written into result files)."""
    return {
        "n_default": DEFAULT_N,
        "load_types": {k: {"p": v["p"], "duty": list(v["duty"])} for k, v in LOAD_TYPES.items()},
        "windows": {
            "ev_fleet_overnight": "earliest U{0..4}, deadline U{5..9}",
            "ev_fleet_evening": "earliest U{16..19}, deadline 24",
            "data_center_batch": "0-24",
            "office_precooling": "earliest U{5..10}, deadline U{12..18}",
            "cold_storage": "0-24",
            "generic": "earliest U{0..20}, length U{3..12}, deadline min(24, earliest+length)",
        },
        "size": f"max_mw ~ LogNormal(0, {SIZE_SIGMA}) relative, then scaled",
        "energy": "duty x max_mw x window_hours",
        "scaling": "sum(max_mw) = share_pct/100 x day's actual peak demand (energy scaled alike)",
    }


def make_population(share_pct: float, peak_mw: float, seed: int, n: int = DEFAULT_N) -> list[Bid]:
    """``n`` synthetic bids with ``sum(max_mw) = share_pct% x peak_mw``; [] if share is 0."""
    if share_pct <= 0 or n <= 0:
        return []
    rng = np.random.default_rng(seed)
    names = list(LOAD_TYPES)
    probs = np.array([LOAD_TYPES[k]["p"] for k in names])
    probs = probs / probs.sum()
    raw = []
    for _ in range(n):
        lt = names[int(rng.choice(len(names), p=probs))]
        e, d = _window(lt, rng)
        size = float(rng.lognormal(0.0, SIZE_SIGMA))
        lo, hi = LOAD_TYPES[lt]["duty"]
        duty = float(rng.uniform(lo, hi))
        raw.append((lt, e, d, size, duty * size * (d - e)))
    factor = share_pct / 100.0 * peak_mw / sum(r[3] for r in raw)
    bids = []
    for i, (lt, e, d, size, energy) in enumerate(raw):
        b = Bid(id=f"p{i:02d}", name=f"{LOAD_TYPES[lt]['label']} {i:02d}", load_type=lt,
                energy_mwh=energy * factor, earliest_hour=e, deadline_hour=d,
                max_mw=size * factor)
        err = bid_feasibility_error(b)
        if err:  # cannot happen (duty <= 0.8); guard anyway
            raise AssertionError(f"population produced infeasible bid {b.id}: {err}")
        bids.append(b)
    return bids
