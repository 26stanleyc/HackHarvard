"""Robust planning against forecast error (M11, design doc).

Planning on a single forecast concentrates flexible load in the hours the forecast thinks are
cleanest, so a forecast miss can turn a planned saving into a realized loss. Robust planning
instead minimizes the AVERAGE emissions over scenarios of the day:

* the day's forecast inputs, and
* for each day d in D-2..D-11, ``forecast(D) + (actual(d) - forecast(d))`` for every hourly input
  field (demand, wind, solar and curtailment clipped at 0).

D-1 is excluded because its last hours are not published by the forecast issue time (00:00 CST
on D), so every scenario uses only information available before planning. Past-day forecasts come
from the same provider as the day's forecast. Days with missing inputs are skipped; with fewer
than ``MIN_ERROR_DAYS`` usable days the plain single-forecast simulator is returned.

:class:`ScenarioSimulator` has the planning interface of :class:`~clearinghouse.simulator.
DaySimulator` (the methods the strategies, coordinator and settlement call), each averaged over
the scenario simulators. Scoring always stays on a plain ``DaySimulator`` on actual inputs.
"""

from __future__ import annotations

import dataclasses
import os
from collections.abc import Callable
from datetime import date, timedelta

import numpy as np

from clearinghouse.inputs import DayInputs
from clearinghouse.params import SimParams
from clearinghouse.simulator import DaySimulator

PLANNING_MODES = ("plain", "robust")
DEFAULT_PLANNING_MODE = "plain"  # set by the M11 adoption rule (docs/design.md, docs/progress.md)
ERROR_LAGS = tuple(range(2, 2 + int(os.environ.get("ROBUST_DAYS", "10"))))  # D-2 .. D-11 by default
MIN_ERROR_DAYS = 5
ERROR_SCALE = float(os.environ.get("ROBUST_ERROR_SCALE", "1.0"))  # M13 experiment knob
SHIFT_FIELDS = ("demand_mw", "fixed_supply_mw", "fixed_nonimport_mw", "net_imports_mw",
                "other_mw", "wind_mw", "solar_mw", "curtailed_mw")
CLIP_FIELDS = ("demand_mw", "wind_mw", "solar_mw", "curtailed_mw")


def planning_mode() -> str:
    """``PLANNING_MODE`` from the environment, else :data:`DEFAULT_PLANNING_MODE`."""
    mode = os.environ.get("PLANNING_MODE", "").strip().lower() or DEFAULT_PLANNING_MODE
    if mode not in PLANNING_MODES:
        raise ValueError(f"PLANNING_MODE must be one of {PLANNING_MODES}, got {mode!r}")
    return mode


def error_days(day: str) -> list[str]:
    """The past days whose forecast errors define the scenarios (D-2..D-11)."""
    d0 = date.fromisoformat(day)
    return [(d0 - timedelta(days=k)).isoformat() for k in ERROR_LAGS]


def shifted_inputs(fc: DayInputs, actual_d: DayInputs, forecast_d: DayInputs) -> DayInputs:
    """``fc`` with every hourly field shifted by day d's forecast error (actual - forecast)."""
    new = {}
    for f in SHIFT_FIELDS:
        v = (np.asarray(getattr(fc, f), dtype=float)
             + ERROR_SCALE * (np.asarray(getattr(actual_d, f), dtype=float)
                              - np.asarray(getattr(forecast_d, f), dtype=float)))
        new[f] = np.clip(v, 0.0, None) if f in CLIP_FIELDS else v
    return dataclasses.replace(fc, **new)


class ScenarioSimulator:
    """Planning simulator averaging every planning quantity over scenario simulators.

    All scenarios share one merit stack (same params and price month), so each method evaluates
    every scenario in one vectorized pass (``[S, ...]``) and averages over the scenario axis; with
    a single scenario the results equal the plain simulator's exactly.
    """

    def __init__(self, sims: list[DaySimulator], inputs: DayInputs, error_days_used: list[str]):
        if not sims:
            raise ValueError("ScenarioSimulator needs at least one scenario")
        stack = sims[0].stack
        if any(s.stack is not stack for s in sims):
            raise ValueError("all scenarios must share one merit stack")
        self.sims = sims
        self.inputs = inputs  # the day's forecast inputs (date, peak demand, price month)
        self.params = sims[0].params
        self.stack = stack
        self.error_days_used = error_days_used
        self._base_req = np.stack([s._base_req for s in sims])  # [S, 24]
        self._curt = np.stack([s._curt for s in sims])  # [S, 24]
        self._other_co2 = np.stack([s._other_co2 for s in sims])  # [S, 24]
        self._fixed_nonimport = np.stack([np.asarray(s.inputs.fixed_nonimport_mw, dtype=float)
                                          for s in sims])  # [S, 24]

    @property
    def n_scenarios(self) -> int:
        return len(self.sims)

    # per-scenario hourly CO2 / fossil generation for added profiles [..., 24] -> [S, ..., 24]
    def _req(self, a: np.ndarray) -> np.ndarray:
        extra = a.ndim - 1
        base = self._base_req.reshape(self._base_req.shape[:1] + (1,) * extra + (24,))
        curt = self._curt.reshape(base.shape)
        return base + np.maximum(0.0, a[None] - curt)

    def _co2(self, req: np.ndarray) -> np.ndarray:
        return self.sims[0]._co2_from_req(req)

    def hourly_emissions_batch(self, added_batch) -> np.ndarray:
        a = np.atleast_2d(DaySimulator._added(added_batch))
        return self._co2(self._req(a)).mean(axis=0)

    def emissions_batch(self, added_batch) -> np.ndarray:
        return self.hourly_emissions_batch(added_batch).sum(axis=-1)

    def hourly_emissions(self, added=None) -> np.ndarray:
        return self._co2(self._req(DaySimulator._added(added))).mean(axis=0)

    def emissions(self, added=None) -> float:
        return float(self.hourly_emissions(added).sum())

    def hourly_co2_at_scenarios(self, hours: np.ndarray, added_at_hours: np.ndarray) -> np.ndarray:
        """Per-scenario CO2 ``[S, len(hours)]`` (used by the M13 robust local search)."""
        h = np.asarray(hours, dtype=int)
        req = self._base_req[:, h] + np.maximum(0.0, np.asarray(added_at_hours)[None]
                                                - self._curt[:, h])
        return self._co2(req)

    def hourly_co2_at(self, hours: np.ndarray, added_at_hours: np.ndarray) -> np.ndarray:
        return self.hourly_co2_at_scenarios(hours, added_at_hours).mean(axis=0)

    def avg_intensity(self, added=None) -> np.ndarray:
        req = self._req(DaySimulator._added(added))  # [S, 24]
        co2 = self._co2(req) + self._other_co2
        gen = np.clip(req, 0.0, self.stack.total_mw) + self._fixed_nonimport
        return (co2 / gen).mean(axis=0)


def build_planning_sim(day: str, fc_inputs: DayInputs, params: SimParams, mode: str,
                       forecast_for: Callable[[str], DayInputs],
                       actual_for: Callable[[str], DayInputs]) -> DaySimulator | ScenarioSimulator:
    """The planning simulator for ``day``: plain, or robust per the M11 method.

    ``forecast_for(d)`` / ``actual_for(d)`` return a past day's forecast / actual inputs; they are
    only ever called for days in :func:`error_days` (never D or D-1).
    """
    plain = DaySimulator(fc_inputs, params)
    if mode == "plain":
        return plain
    if mode != "robust":
        raise ValueError(f"unknown planning mode {mode!r}")
    sims, used = [plain], []
    for d in error_days(day):
        try:
            shifted = shifted_inputs(fc_inputs, actual_for(d), forecast_for(d))
        except (ValueError, KeyError, FileNotFoundError):
            continue  # missing inputs for that day
        if not all(np.isfinite(getattr(shifted, f)).all() for f in SHIFT_FIELDS):
            continue
        sims.append(DaySimulator(shifted, params, stack=plain.stack))
        used.append(d)
    if len(used) < MIN_ERROR_DAYS:
        return plain
    return ScenarioSimulator(sims, fc_inputs, used)
