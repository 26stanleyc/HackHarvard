"""Vectorized merit-order dispatch simulator for one CST day (design B1).

Model
-----
* **Stack.** eGRID ERCOT fossil plants (``clearinghouse.data.egrid``) sorted by marginal cost
  ``heat_rate x EIA fuel price(month) x params.scale(fuel, month)`` (ties by ``orispl``); each
  plant's available capacity is ``nameplate x params.availability[fuel]``. Precomputed once per
  ``(price month, params)``: cumulative capacity ``K`` and cumulative CO2 ``Q`` (t/h with every
  plant up to ``j`` at full output), both with a leading 0.
* **Dispatch.** For added flexible load ``a_h`` (MW = MWh per slot), added load is first served
  by curtailed wind/solar at 0 t/MWh, then by fossil plants::

      R_h = demand_h + max(0, a_h - curtailed_h) - fixed_supply_h     (fossil requirement)
      G_h = clip(R_h, 0, K_total)                                     (fossil generation)
      CO2_h = interp(G_h; K, Q) + max(0, R_h - K_total) x rate(last plant)

  ``R_h - K_total > 0`` is recorded as unserved energy (never silently dropped); its emissions
  are extrapolated at the most expensive plant's CO2 rate and reported.
* Hourly CO2 is separable by hour and piecewise linear in ``a_h``, so batches of profiles are
  evaluated with one ``np.interp`` call (``*_batch`` methods take arrays of shape ``[n, 24]``).

* **Marginal resource (display only).** ``marginal_resource`` / ``marginal_rate`` describe the
  next ``MARGINAL_PROBE_MW`` (100 MW) block on top of the current added load: curtailed
  renewables if remaining curtailment covers at least half of it, else the fossil plant at the
  requirement after the probe; the rate is the probe's average incremental CO2 per MWh.

All "emissions" are fossil CO2 (metric tons) from the simulated stack. ``avg_intensity``
additionally counts EIA-930 "other" generation at its eGRID factor (fixed, small).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

import numpy as np
import pandas as pd

from clearinghouse.data.egrid import load_emission_factors, load_plants
from clearinghouse.data.fuel_prices import load_fuel_prices
from clearinghouse.inputs import HOURS, DayInputs
from clearinghouse.params import DEFAULT_PARAMS, FUELS, SimParams


@dataclass(frozen=True)
class MeritStack:
    """Plants in merit order with cumulative capacity / CO2 (arrays of length n+1, leading 0)."""

    names: tuple[str, ...]
    fuels: tuple[str, ...]
    orispl: np.ndarray
    capacity_mw: np.ndarray  # available capacity per plant (MW)
    co2_rate: np.ndarray  # t/MWh per plant
    marginal_cost: np.ndarray  # $/MWh per plant
    K: np.ndarray  # cumulative available capacity, K[0] = 0
    Q: np.ndarray  # cumulative CO2 at full output (t/h), Q[0] = 0

    @property
    def total_mw(self) -> float:
        return float(self.K[-1])

    @property
    def last_rate(self) -> float:
        return float(self.co2_rate[-1])


def build_stack(plants: pd.DataFrame, price_month: str, params: SimParams) -> MeritStack:
    prices = load_fuel_prices()
    row = prices[prices["month"] == price_month]
    if len(row) != 1:
        raise ValueError(f"no EIA fuel prices for month {price_month}")
    month = int(price_month[5:7])
    fuel_price = {}
    for f in FUELS:
        p = float(row[f"{f}_usd_per_mmbtu"].iloc[0])
        if not np.isfinite(p):
            raise ValueError(f"EIA {f} price missing for {price_month}")
        fuel_price[f] = p * params.scale(f, month)
    df = plants[["orispl", "name", "fuel", "capacity_mw", "heat_rate_mmbtu_per_mwh",
                 "co2_t_per_mwh"]].copy()
    df["avail_mw"] = df["capacity_mw"] * df["fuel"].map(params.availability).astype(float)
    df["mc"] = df["heat_rate_mmbtu_per_mwh"] * df["fuel"].map(fuel_price).astype(float)
    df = df[df["avail_mw"] > 0].sort_values(["mc", "orispl"], kind="mergesort")
    cap = df["avail_mw"].to_numpy(dtype=float)
    rate = df["co2_t_per_mwh"].to_numpy(dtype=float)
    return MeritStack(
        names=tuple(df["name"].astype(str)),
        fuels=tuple(df["fuel"].astype(str)),
        orispl=df["orispl"].to_numpy(dtype=np.int64),
        capacity_mw=cap,
        co2_rate=rate,
        marginal_cost=df["mc"].to_numpy(dtype=float),
        K=np.concatenate([[0.0], np.cumsum(cap)]),
        Q=np.concatenate([[0.0], np.cumsum(cap * rate)]),
    )


_STACK_CACHE: dict[tuple[str, SimParams], MeritStack] = {}

MARGINAL_PROBE_MW = 100.0
"""Probe block (MW for one slot = MWh) used by :meth:`DaySimulator.marginal_resource` and
:meth:`DaySimulator.marginal_rate` for DISPLAY: "what serves the next 100 MW". A 1 MW probe would
call almost every hour "curtailed renewables" because modeled curtailment is a few MW or more in
every 2024 hour. Emissions math does not use it."""


def get_stack(price_month: str, params: SimParams,
              plants: pd.DataFrame | None = None) -> MeritStack:
    """Cached stack for the default eGRID plant table; uncached if ``plants`` is given."""
    if plants is not None:
        return build_stack(plants, price_month, params)
    key = (price_month, params)
    if key not in _STACK_CACHE:
        _STACK_CACHE[key] = build_stack(load_plants(), price_month, params)
    return _STACK_CACHE[key]


class DaySimulator:
    """Merit-order simulator for one day of inputs.

    ``added`` arguments are 24-vectors of added flexible load in MW per CST slot (``None`` = 0);
    ``*_batch`` methods accept ``[n, 24]``. Results never mutate the inputs.
    """

    def __init__(self, inputs: DayInputs, params: SimParams = DEFAULT_PARAMS,
                 plants: pd.DataFrame | None = None, stack: MeritStack | None = None):
        self.inputs = inputs
        self.params = params
        # ``stack`` (optional) must be ``build_stack(<plants>, inputs.price_month, params)``; it
        # lets callers that sweep many params (M3 calibration) skip the module-level cache.
        self.stack = stack if stack is not None else get_stack(inputs.price_month, params, plants)
        self._base_req = inputs.demand_mw - inputs.fixed_supply_mw  # R_h with zero added load
        self._curt = inputs.curtailed_mw
        self._other_co2 = inputs.other_mw * float(load_emission_factors().get("other", 0.0))

    # --- core -------------------------------------------------------------------------------

    @staticmethod
    def _added(added) -> np.ndarray:
        if added is None:
            return np.zeros(HOURS)
        a = np.asarray(added, dtype=float)
        if a.shape[-1] != HOURS:
            raise ValueError(f"added load must have last dimension {HOURS}, got {a.shape}")
        return a

    def fossil_requirement(self, added=None) -> np.ndarray:
        """``R_h``: fossil MW needed (may exceed stack capacity); shape follows ``added``."""
        a = self._added(added)
        return self._base_req + np.maximum(0.0, a - self._curt)

    def _co2_from_req(self, req: np.ndarray) -> np.ndarray:
        s = self.stack
        g = np.clip(req, 0.0, s.total_mw)
        co2 = np.interp(g.ravel(), s.K, s.Q).reshape(g.shape)
        return co2 + np.maximum(0.0, req - s.total_mw) * s.last_rate

    def hourly_emissions(self, added=None) -> np.ndarray:
        """Fossil CO2 per slot (t), shape ``[24]``."""
        return self._co2_from_req(self.fossil_requirement(added))

    def emissions(self, added=None) -> float:
        """Total fossil CO2 for the day (t)."""
        return float(self.hourly_emissions(added).sum())

    def hourly_emissions_batch(self, added_batch) -> np.ndarray:
        """``[n, 24]`` added-load profiles -> ``[n, 24]`` hourly fossil CO2 (t)."""
        a = np.atleast_2d(self._added(added_batch))
        return self._co2_from_req(self.fossil_requirement(a))

    def emissions_batch(self, added_batch) -> np.ndarray:
        """``[n, 24]`` added-load profiles -> ``[n]`` daily fossil CO2 (t)."""
        return self.hourly_emissions_batch(added_batch).sum(axis=-1)

    def fossil_generation(self, added=None) -> np.ndarray:
        """Served fossil MW per slot (clipped to stack capacity)."""
        return np.clip(self.fossil_requirement(added), 0.0, self.stack.total_mw)

    def unserved_mwh(self, added=None) -> float:
        """MWh of fossil requirement above available stack capacity (0 normally)."""
        return float(np.maximum(0.0, self.fossil_requirement(added) - self.stack.total_mw).sum())

    def absorbed_curtailment_mwh(self, added=None) -> float:
        a = self._added(added)
        return float(np.minimum(np.maximum(a, 0.0), self._curt).sum())

    # --- helpers for planners ---------------------------------------------------------------

    def hourly_co2_at(self, hours: np.ndarray, added_at_hours: np.ndarray) -> np.ndarray:
        """CO2 (t) in the given slots when total added load in those slots is ``added_at_hours``.

        Elementwise over ``hours`` (int array) and ``added_at_hours`` (same shape). Hot path for
        the greedy coordinator: avoids building full 24-vectors.
        """
        req = self._base_req[hours] + np.maximum(0.0, added_at_hours - self._curt[hours])
        return self._co2_from_req(req)

    def incremental_cost(self, added_24, hours, mwh) -> np.ndarray:
        """CO2 increase (t) of adding ``mwh`` (scalar or per-hour array) in each of ``hours``
        separately, on top of ``added_24``. Returns an array aligned with ``hours``."""
        a = self._added(added_24)
        h = np.asarray(hours, dtype=int)
        m = np.broadcast_to(np.asarray(mwh, dtype=float), h.shape)
        return self.hourly_co2_at(h, a[h] + m) - self.hourly_co2_at(h, a[h])

    # --- marginal resource (display) --------------------------------------------------------

    def _probe(self, a: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """``(remaining curtailment, R now, R after the probe)`` for a ``MARGINAL_PROBE_MW`` block
        added on top of ``a`` (the probe uses remaining curtailment first, like any load)."""
        remaining = np.maximum(0.0, self._curt - a)
        req_now = self.fossil_requirement(a)
        req_after = self.fossil_requirement(a + MARGINAL_PROBE_MW)
        return remaining, req_now, req_after

    def marginal_rate(self, added=None) -> np.ndarray:
        """Average CO2 rate (t/MWh) of the next ``MARGINAL_PROBE_MW`` block in each slot on top of
        ``added``: (CO2 with the probe - CO2 without) / probe MWh. Curtailed MWh in the probe count
        at 0 t/MWh, so a partly curtailed probe gives a rate between 0 and the fossil plant's."""
        a = self._added(added)
        _, req_now, req_after = self._probe(a)
        return (self._co2_from_req(req_after) - self._co2_from_req(req_now)) / MARGINAL_PROBE_MW

    def _marginal_index(self, a: np.ndarray) -> np.ndarray:
        """Index of the fossil plant at the fossil requirement after the probe's non-curtailed
        part is added (the plant serving its last MW; the most expensive plant if the stack is
        exhausted, the first plant if the requirement is <= 0)."""
        s = self.stack
        _, _, req_after = self._probe(a)
        idx = np.searchsorted(s.K, np.clip(req_after, 0.0, None), side="left") - 1
        return np.clip(idx, 0, len(s.capacity_mw) - 1)

    def marginal_resource(self, added=None) -> list[dict]:
        """Per slot: what serves the next ``MARGINAL_PROBE_MW`` block on top of ``added``, as
        ``schemas.MarginalResource`` dicts.

        ``curtailed_renewables`` (0 t/MWh) if the remaining curtailment
        ``max(0, curtailed_h - added_h)`` covers at least half the probe; otherwise ``fossil``
        with the plant at the fossil requirement after the probe's non-curtailed part is added
        (the most expensive plant if the stack is exhausted). Emissions are unaffected by this
        display definition.
        """
        a = self._added(added)
        remaining, _, _ = self._probe(a)
        idx = self._marginal_index(a)
        out = []
        for h in range(HOURS):
            if remaining[h] >= 0.5 * MARGINAL_PROBE_MW:
                out.append({"kind": "curtailed_renewables", "plant_name": None, "fuel": None,
                            "co2_t_per_mwh": 0.0})
            else:
                j = int(idx[h])
                out.append({"kind": "fossil", "plant_name": self.stack.names[j],
                            "fuel": self.stack.fuels[j],
                            "co2_t_per_mwh": float(self.stack.co2_rate[j])})
        return out

    def fuel_split(self, added=None) -> dict[str, np.ndarray]:
        """Simulated coal / gas / oil MW per slot (served fossil generation by fuel)."""
        s = self.stack
        g = self.fossil_generation(added)
        served = np.clip(g[:, None] - s.K[None, :-1], 0.0, s.capacity_mw[None, :])
        fuels = np.asarray(s.fuels)
        return {f: served[:, fuels == f].sum(axis=1) for f in FUELS}

    def avg_intensity(self, added=None) -> np.ndarray:
        """Average CO2 intensity per slot (t/MWh) = (fossil CO2 + "other" CO2) / (served fossil
        generation + fixed non-import supply). Imports excluded from both (no data on them)."""
        co2 = self.hourly_emissions(added) + self._other_co2
        gen = self.fossil_generation(added) + self.inputs.fixed_nonimport_mw
        return co2 / gen

    # --- baseline ---------------------------------------------------------------------------

    @cached_property
    def baseline(self) -> dict:
        """Results with zero added load."""
        z = np.zeros(HOURS)
        return {
            "hourly_co2_t": self.hourly_emissions(z),
            "total_co2_t": self.emissions(z),
            "fossil_generation_mw": self.fossil_generation(z),
            "fuel_split_mw": self.fuel_split(z),
            "avg_intensity": self.avg_intensity(z),
            "marginal_resource": self.marginal_resource(z),
            "unserved_mwh": self.unserved_mwh(z),
        }
