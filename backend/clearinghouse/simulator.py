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
                 plants: pd.DataFrame | None = None):
        self.inputs = inputs
        self.params = params
        self.stack = get_stack(inputs.price_month, params, plants)
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

    def marginal_rate(self, added=None) -> np.ndarray:
        """CO2 rate (t/MWh) of the next MWh in each slot (0 where curtailment would absorb it)."""
        a = self._added(added)
        idx = self._marginal_index(a)
        rate = self.stack.co2_rate[idx]
        return np.where(a < self._curt, 0.0, rate)

    def _marginal_index(self, a: np.ndarray) -> np.ndarray:
        s = self.stack
        g = np.clip(self.fossil_requirement(a), 0.0, None)
        idx = np.searchsorted(s.K, g, side="right") - 1  # plant serving the next MWh
        return np.clip(idx, 0, len(s.capacity_mw) - 1)

    def marginal_resource(self, added=None) -> list[dict]:
        """Per slot: what serves the next MWh, as ``schemas.MarginalResource`` dicts.

        ``curtailed_renewables`` when ``added_h < curtailed_h`` (the next MWh is still absorbed by
        curtailed wind/solar), else the fossil plant at the margin of ``G_h`` (the most expensive
        plant if the stack is exhausted). If ``R_h <= 0`` (never in the 2024 data) the first plant
        in the stack is reported.
        """
        a = self._added(added)
        idx = self._marginal_index(a)
        out = []
        for h in range(HOURS):
            if a[h] < self._curt[h]:
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
