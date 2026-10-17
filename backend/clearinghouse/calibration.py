"""M3 calibration and validation of the merit-order simulator (design B1, M3).

Fitted parameters (the named ones only; nothing else is added)
--------------------------------------------------------------
* ``availability["coal"]``, ``availability["gas"]`` in [0.3, 1.0] (global, all months).
* ``monthly_price_scale[month]["coal"]`` in [0.1, 3.0] for each calendar month 1..12: a multiplier
  on the EIA coal price relative to gas. Gas scale is fixed at 1.0 because the merit order
  depends only on relative costs, so a gas scale would be redundant.
* ``availability["oil"]`` stays at its stated default (0.50), not fitted: EIA-930 reports no oil
  generation for ERCO in 2024 (the column is empty, treated as 0) and the 0.26 GW of oil plants
  sit at the top of the stack, so the objective does not depend on it unless the coal + gas stack
  is exhausted (checked and reported by ``scripts/calibrate.py``).

At most 2 + 12 fitted numbers. ``price_scale`` stays 1.0 for every fuel.

Pre-registered fit (fixed before any holdout number was seen)
-------------------------------------------------------------
* Data: ``calibration_train_days`` only. :func:`fit` refuses any date in ``never_trained_days``
  (demo, herding-gate and holdout days).
* Observed hourly fossil CO2 = EIA-930 coal x EF_coal + gas x EF_gas + oil x EF_oil
  (``fuel_emission_factors.json``); observed coal share = coal / (coal + gas + oil).
* Simulated = ``DaySimulator(actual_inputs(day), params)`` with zero added load (hourly fossil
  CO2, served fuel split).
* Objective = mean over training hours of |sim_co2 - obs_co2| / obs_co2 + mean over training
  hours of |sim_coal_share - obs_coal_share| (equal weights).
* Optimizer (deterministic): alternating coordinate descent.
    - availability step: nested grid search over (coal, gas) availability, step 0.05 over the full
      box, then step 0.01 and 0.002 around the incumbent (clipped to the box);
    - coal-scale step: the objective is separable by month for fixed availabilities, and for one
      month it is piecewise constant in the coal scale (the merit order only changes where a coal
      plant's cost crosses another plant's cost). Every merit order reachable inside [0.1, 3.0]
      is evaluated (geometric midpoint of each interval between consecutive crossing points), so
      each month's step is an exact global minimum. Ties (equal objective) go to the scale
      closest to 1.0 in log terms (smallest departure from published prices).
    - repeat until no parameter changes (max ``max_iter`` rounds).
* Final parameters: the first attempt whose holdout hourly fossil CO2 MAPE is <= 0.15; if none
  of the (at most 5) attempts reaches it, the attempt with the lowest TRAINING objective.

Amendment (orchestrator decision after attempt 1; see ``ATTEMPTS`` below)
------------------------------------------------------------------------
Attempt 1 met the target but was rejected: its stack (49.9 GW available) cannot serve observed
peaks. Physical feasibility is now a constraint on the existing parameters (no new parameter),
derived from TRAINING days only (:func:`feasibility_bounds`): coal_av x coal capacity >= max
observed hourly coal MW; gas_av x gas capacity >= max observed hourly gas MW; total available
capacity >= max observed hourly coal + gas + oil MW. Attempt 1b (attempt 1 + constraints) is
adopted whether or not it meets the target; attempts 2-5 (same constraints) run only if it misses,
with the same selection rule among the constrained attempts.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

from clearinghouse.data import load_days, load_emission_factors, load_fuel_prices, load_plants
from clearinghouse.inputs import HOURS, DayInputs, actual_inputs
from clearinghouse.params import DEFAULT_PARAMS, FUELS, SimParams
from clearinghouse.simulator import DaySimulator, build_stack

AVAIL_BOUNDS = (0.3, 1.0)
COAL_SCALE_BOUNDS = (0.1, 3.0)
FITTED_AVAIL_FUELS = ("coal", "gas")
MONTHS = tuple(range(1, 13))
FOSSIL_CO2_TARGET = 0.15
OIL_NOTE = (
    "availability['oil'] is not fitted and stays at the stated default 0.50: EIA-930 reports no "
    "ERCO oil generation in 2024 (treated as 0) and the 0.26 GW of oil plants sit at the top of "
    "the stack, so the objective depends on it only in hours where the coal + gas stack is "
    "exhausted, and then by at most 0.5 x 0.26 GW of capacity (see unserved_* and oil_mwh_sim)."
)

DEFINITIONS = {
    "observed_fossil_co2": "EIA-930 hourly coal_mw x EF_coal + gas_mw x EF_gas + oil_mw x EF_oil "
                           "(t), EF from fuel_emission_factors.json (eGRID2023, t/MWh); oil NaN "
                           "-> 0",
    "simulated_fossil_co2": "DaySimulator(actual_inputs(day), params).hourly_emissions() with zero "
                            "added load (t)",
    "coal_share": "coal / (coal + gas + oil) generation, hourly (observed: EIA-930; simulated: "
                  "DaySimulator.fuel_split)",
    "objective": "mean over hours of |sim_co2 - obs_co2| / obs_co2 + mean over hours of "
                 "|sim_coal_share - obs_coal_share| (equal weights), training days only",
    "coal_gas_split_error": "(a) MAE of hourly coal share on holdout days, as a fraction "
                            "(0.05 = 5 percentage points)",
    "coal_mw_mape / gas_mw_mape": "(a) mean over holdout hours of |sim - obs| / obs for coal MW "
                                  "and gas MW",
    "mef_observed": "(b) OLS slope (with intercept) of hour-to-hour delta observed fossil CO2 on "
                    "delta observed fossil generation (coal + gas + oil), pairs of consecutive "
                    "hours within the same day only (23 per day), holdout days (t/MWh)",
    "mef_simulated": "(b) the same regression on the simulator's hourly fossil CO2 and served "
                     "fossil generation series (t/MWh)",
    "mean_marginal_rate": "(b, context) mean over holdout hours of DaySimulator.marginal_rate() "
                          "at zero added load (average CO2 of the next 100 MW probe block, "
                          "curtailed MWh at 0); mean_fossil_marginal_rate averages only hours "
                          "whose marginal_resource is fossil; "
                          "mean_fossil_margin_rate is the CO2 rate of the fossil plant at the "
                          "margin once curtailment is used up, over all hours",
    "fossil_co2_error": "(c) hourly fossil CO2 MAPE on holdout days: mean over hours of "
                        "|sim - obs| / obs (fraction). Target <= 0.15",
    "daily_co2_mape": "(c) mean over days of |sum_h sim - sum_h obs| / sum_h obs",
    "daily_co2_bias": "(c) mean over days of (sum_h sim - sum_h obs) / sum_h obs (signed)",
    "total_co2_bias": "(c) sum over all hours sim / sum obs - 1 (signed)",
}


# --- observed / simulated series ---------------------------------------------------------------


def observed_hourly(inputs: DayInputs) -> dict[str, np.ndarray]:
    """EIA-930 hourly coal / gas / oil MW, fossil MW, fossil CO2 (t) and coal share."""
    ef = load_emission_factors()
    coal = np.nan_to_num(np.asarray(inputs.actual_fossil["coal"], dtype=float), nan=0.0)
    gas = np.nan_to_num(np.asarray(inputs.actual_fossil["gas"], dtype=float), nan=0.0)
    oil = np.nan_to_num(np.asarray(inputs.actual_fossil["oil"], dtype=float), nan=0.0)
    fossil = coal + gas + oil
    return {
        "coal_mw": coal, "gas_mw": gas, "oil_mw": oil, "fossil_mw": fossil,
        "co2_t": coal * ef["coal"] + gas * ef["gas"] + oil * ef["oil"],
        "coal_share": coal / fossil,
    }


def simulated_hourly(sim: DaySimulator) -> dict[str, np.ndarray]:
    """Simulated hourly coal / gas / oil MW, fossil MW, fossil CO2 (t) and coal share (zero
    added load)."""
    split = sim.fuel_split(None)
    fossil = split["coal"] + split["gas"] + split["oil"]
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(fossil > 0, split["coal"] / np.where(fossil > 0, fossil, 1.0), 0.0)
    return {
        "coal_mw": split["coal"], "gas_mw": split["gas"], "oil_mw": split["oil"],
        "fossil_mw": sim.fossil_generation(None), "co2_t": sim.hourly_emissions(None),
        "coal_share": share,
    }


@dataclass(frozen=True)
class DayData:
    date: str
    inputs: DayInputs
    obs: dict[str, np.ndarray] = field(repr=False)

    @property
    def month(self) -> int:
        return self.inputs.month


def load_day_data(dates: Iterable[str],
                  loader: Callable[[str], DayInputs] | None = None) -> list[DayData]:
    """Actual inputs + observed series for each date (``loader`` defaults to ``actual_inputs``)."""
    load = loader if loader is not None else actual_inputs
    out = []
    for d in dates:
        inp = load(d)
        out.append(DayData(date=d, inputs=inp, obs=observed_hourly(inp)))
    return out


def simulate_days(params: SimParams, days: Sequence[DayData]) -> list[DaySimulator]:
    """One :class:`DaySimulator` per day; merit stacks are built once per price month and NOT put
    in the simulator's module-level cache (the fit evaluates thousands of parameter sets)."""
    plants = load_plants()
    stacks: dict[str, object] = {}
    sims = []
    for d in days:
        pm = d.inputs.price_month
        if pm not in stacks:
            stacks[pm] = build_stack(plants, pm, params)
        sims.append(DaySimulator(d.inputs, params, stack=stacks[pm]))
    return sims


def objective_sums(params: SimParams, days: Sequence[DayData]) -> tuple[float, float, int]:
    """``(sum of hourly relative CO2 errors, sum of hourly |coal share error|, n hours)``."""
    s_co2 = s_share = 0.0
    n = 0
    for d, sim in zip(days, simulate_days(params, days), strict=True):
        s = simulated_hourly(sim)
        s_co2 += float(np.sum(np.abs(s["co2_t"] - d.obs["co2_t"]) / d.obs["co2_t"]))
        s_share += float(np.sum(np.abs(s["coal_share"] - d.obs["coal_share"])))
        n += HOURS
    return s_co2, s_share, n


def objective(params: SimParams, days: Sequence[DayData]) -> float:
    """Pre-registered objective (see module docstring): mean relative hourly CO2 error + mean
    absolute hourly coal-share error."""
    s_co2, s_share, n = objective_sums(params, days)
    return (s_co2 + s_share) / n


# --- parameter construction --------------------------------------------------------------------


def make_params(avail: dict[str, float], coal_scale: dict[int, float],
                label: str = "calibrated") -> SimParams:
    """SimParams with fitted coal/gas availability, oil at its default, per-month coal scale."""
    availability = {"coal": float(avail["coal"]), "gas": float(avail["gas"]),
                    "oil": float(DEFAULT_PARAMS.availability["oil"])}
    return SimParams(
        availability=availability,
        price_scale={f: 1.0 for f in FUELS},
        monthly_price_scale={int(m): {"coal": float(s)} for m, s in coal_scale.items()},
        label=label,
    )


def coal_scale_edges(price_month: str, lo: float = COAL_SCALE_BOUNDS[0],
                     hi: float = COAL_SCALE_BOUNDS[1]) -> np.ndarray:
    """Sorted ``[lo, crossing points in (lo, hi)..., hi]`` for ``price_month``.

    Coal plant i and non-coal plant j swap merit order at ``s = hr_j p_j / (hr_i p_coal)``, so the
    merit order (and hence the simulator output) is constant between consecutive edges.
    Availability never changes the order, so the edges do not depend on it.
    """
    plants = load_plants()
    row = load_fuel_prices()
    row = row[row["month"] == price_month].iloc[0]
    price = {f: float(row[f"{f}_usd_per_mmbtu"]) for f in FUELS}
    coal = plants[plants["fuel"] == "coal"]["heat_rate_mmbtu_per_mwh"].to_numpy(float)
    other = plants[plants["fuel"] != "coal"]
    other_cost = (other["heat_rate_mmbtu_per_mwh"].to_numpy(float)
                  * other["fuel"].map(price).to_numpy(float))
    cross = (other_cost[None, :] / (coal[:, None] * price["coal"])).ravel()
    cross = np.unique(cross[(cross > lo) & (cross < hi)])
    return np.concatenate([[lo], cross, [hi]])


def coal_scale_candidates(price_month: str) -> np.ndarray:
    """One coal scale per distinct merit order reachable in the bounds: the geometric midpoint
    of each interval between consecutive :func:`coal_scale_edges`."""
    e = coal_scale_edges(price_month)
    return np.sqrt(e[:-1] * e[1:])


# --- fit ---------------------------------------------------------------------------------------


@dataclass
class FitConfig:
    name: str
    description: str
    init_avail: dict[str, float]
    init_coal_scale: float
    first_step: str = "scale"  # "scale" | "avail": which coordinate step runs first
    avail_grid_steps: tuple[float, ...] = (0.05, 0.01, 0.002)
    outer_avail_grid: float | None = None  # if set: global outer grid over availabilities
    outer_grid_offset: float = 0.0  # shift of the outer grid points (kept inside the bounds)
    max_iter: int = 10
    feasibility: bool = False  # enforce the training-data capacity constraints (attempt 1b on)


@dataclass
class FitResult:
    params: SimParams
    train_objective: float
    train_co2_term: float
    train_share_term: float
    iterations: int
    history: list[dict]
    scale_plateaus: dict[int, list[float]]
    config: FitConfig
    feasibility: dict | None = None


def _check_train_only(dates: Sequence[str]) -> None:
    days = load_days()
    forbidden = set(days["never_trained_days"]) | set(days["calibration_holdout_days"]) \
        | {d["date"] for d in days["demo_days"]} | set(days["herding_gate_days"])
    bad = sorted(set(dates) & forbidden)
    if bad:
        raise ValueError(f"fit() refuses never-trained days (holdout/demo/gate): {bad[:5]}")
    if len(set(dates)) != len(dates):
        raise ValueError("duplicate dates in training set")


def _box(v: float, lo: float, hi: float) -> float:
    return float(min(hi, max(lo, v)))


def _grid(center: float | None, step: float, half_width: float | None,
          lo: float = AVAIL_BOUNDS[0]) -> np.ndarray:
    """Availability grid inside ``[lo, 1.0]``; ``lo`` (a feasibility floor above the 0.3 bound,
    if any) is always included so the optimizer can reach the constraint boundary."""
    base_lo, hi = AVAIL_BOUNDS
    lo = max(lo, base_lo)
    if center is None:
        n = int(round((hi - base_lo) / step))
        vals = np.round(base_lo + step * np.arange(n + 1), 6)
    else:
        k = int(round(half_width / step))
        vals = np.round(center + step * np.arange(-k, k + 1), 6)
    vals = np.clip(vals, lo, hi)
    return np.unique(np.concatenate([vals, [lo]]) if lo > base_lo else vals)


def fleet_capacity_mw() -> dict[str, float]:
    """eGRID nameplate capacity (MW) per fossil fuel in the simulator's plant table."""
    cap = load_plants().groupby("fuel")["capacity_mw"].sum()
    return {f: float(cap.get(f, 0.0)) for f in FUELS}


def feasibility_bounds(days: Sequence[DayData]) -> dict:
    """Physical feasibility constraints derived from the given (TRAINING) days' observations:

    * coal_av x coal capacity >= max observed hourly coal MW,
    * gas_av x gas capacity >= max observed hourly gas MW,
    * total available capacity (coal + gas + oil at its fixed default) >= max observed hourly
      coal + gas + oil MW.
    """
    cap = fleet_capacity_mw()
    coal_max = max(float(d.obs["coal_mw"].max()) for d in days)
    gas_max = max(float(d.obs["gas_mw"].max()) for d in days)
    fossil_max = max(float(d.obs["fossil_mw"].max()) for d in days)
    return {
        "capacity_mw": cap,
        "max_obs_coal_mw": coal_max,
        "max_obs_gas_mw": gas_max,
        "max_obs_fossil_mw": fossil_max,
        "min_availability": {"coal": coal_max / cap["coal"], "gas": gas_max / cap["gas"]},
        "oil_available_mw": cap["oil"] * float(DEFAULT_PARAMS.availability["oil"]),
        "rule": "coal_av x coal_cap >= max obs coal; gas_av x gas_cap >= max obs gas; "
                "coal_av x coal_cap + gas_av x gas_cap + 0.5 x oil_cap >= max obs coal+gas+oil "
                "(maxima over training hours)",
    }


def is_feasible(avail: dict[str, float], fb: dict | None) -> bool:
    if fb is None:
        return True
    cap = fb["capacity_mw"]
    if any(avail[f] < fb["min_availability"][f] - 1e-12 for f in FITTED_AVAIL_FUELS):
        return False
    total = avail["coal"] * cap["coal"] + avail["gas"] * cap["gas"] + fb["oil_available_mw"]
    return total >= fb["max_obs_fossil_mw"] - 1e-9


class _FastMonth:
    """Exact, vectorized re-implementation of the simulator's zero-added-load outputs for all
    training days of one price month (used only inside the fit loop; :func:`fit` re-checks the
    final objective with :class:`DaySimulator`).

    With served fossil generation ``G = clip(R, 0, K_total)``: CO2 = interp(G; K, Q) + unserved x
    last rate (as ``DaySimulator.hourly_emissions``) and served coal = interp(G; K, K_coal) where
    ``K_coal`` is the cumulative available coal capacity (equal to summing
    ``DaySimulator.fuel_split``'s per-plant served MW over coal plants).
    """

    def __init__(self, days: Sequence[DayData], price_month: str):
        plants = load_plants()
        row = load_fuel_prices()
        row = row[row["month"] == price_month].iloc[0]
        price = {f: float(row[f"{f}_usd_per_mmbtu"]) for f in FUELS}
        self.fuel = plants["fuel"].to_numpy(str)
        self.is_coal = self.fuel == "coal"
        self.orispl = plants["orispl"].to_numpy(np.int64)
        self.cap = plants["capacity_mw"].to_numpy(float)
        self.rate = plants["co2_t_per_mwh"].to_numpy(float)
        # base marginal cost at scale 1 (price_scale is 1.0 for every fuel in the fit)
        self.mc0 = (plants["heat_rate_mmbtu_per_mwh"].to_numpy(float)
                    * np.array([price[f] for f in self.fuel]))
        self.R = np.concatenate([d.inputs.demand_mw - d.inputs.fixed_supply_mw for d in days])
        self.obs_co2 = np.concatenate([d.obs["co2_t"] for d in days])
        self.obs_share = np.concatenate([d.obs["coal_share"] for d in days])

    def sums(self, avail: dict[str, float], s: float) -> tuple[float, float]:
        a = np.where(self.is_coal, avail["coal"],
                     np.where(self.fuel == "gas", avail["gas"],
                              float(DEFAULT_PARAMS.availability["oil"])))
        cap = self.cap * a
        mc = np.where(self.is_coal, self.mc0 * s, self.mc0)
        keep = cap > 0
        order = np.flatnonzero(keep)[np.lexsort((self.orispl[keep], mc[keep]))]
        c = cap[order]
        K = np.concatenate([[0.0], np.cumsum(c)])
        Q = np.concatenate([[0.0], np.cumsum(c * self.rate[order])])
        Kc = np.concatenate([[0.0], np.cumsum(c * self.is_coal[order])])
        G = np.clip(self.R, 0.0, K[-1])
        co2 = np.interp(G, K, Q) + np.maximum(0.0, self.R - K[-1]) * self.rate[order][-1]
        coal = np.interp(G, K, Kc)
        share = np.where(G > 0, coal / np.where(G > 0, G, 1.0), 0.0)
        return (float(np.sum(np.abs(co2 - self.obs_co2) / self.obs_co2)),
                float(np.sum(np.abs(share - self.obs_share))))


class _Fitter:
    def __init__(self, days: Sequence[DayData], fb: dict | None = None):
        self.fb = fb
        self.lo = {f: (max(AVAIL_BOUNDS[0], fb["min_availability"][f]) if fb else AVAIL_BOUNDS[0])
                   for f in FITTED_AVAIL_FUELS}
        self.days = list(days)
        self.n_hours = HOURS * len(self.days)
        self.by_month: dict[int, list[DayData]] = {}
        for d in self.days:
            self.by_month.setdefault(d.month, []).append(d)
        self.months = sorted(self.by_month)
        pm = {m: self.by_month[m][0].inputs.price_month for m in self.months}
        for m in self.months:
            if any(d.inputs.price_month != pm[m] for d in self.by_month[m]):
                raise ValueError("training days of one calendar month span several price months")
        self.fast = {m: _FastMonth(self.by_month[m], pm[m]) for m in self.months}
        # candidate scales per month (merit order does not depend on availability)
        self.edges = {m: coal_scale_edges(pm[m]) for m in self.months}
        self.candidates = {m: np.sqrt(e[:-1] * e[1:]) for m, e in self.edges.items()}
        self.n_evals = 0

    def month_sums(self, avail: dict, m: int, s: float) -> float:
        self.n_evals += 1
        a, b = self.fast[m].sums(avail, s)
        return a + b

    def total(self, avail: dict, scale: dict[int, float]) -> tuple[float, float, float]:
        a = b = 0.0
        for m in self.months:
            x, y = self.fast[m].sums(avail, scale[m])
            a, b = a + x, b + y
        n = self.n_hours
        return (a + b) / n, a / n, b / n

    def scale_step(self, avail: dict, scale: dict[int, float]) -> tuple[dict, dict]:
        new, plateaus = {}, {}
        for m in self.months:
            cands = self.candidates[m]
            vals = np.array([self.month_sums(avail, m, float(s)) for s in cands])
            best = vals.min()
            tied = np.flatnonzero(vals <= best + 1e-9 * max(1.0, abs(best)))
            j = int(tied[np.argmin(np.abs(np.log(cands[tied])))])
            new[m] = float(cands[j])
            # plateau: every scale in [edges[j], edges[j+1]] gives the same merit order
            plateaus[m] = [float(self.edges[m][j]), float(self.edges[m][j + 1])]
        return new, plateaus

    def avail_step(self, avail: dict, scale: dict[int, float], steps: Sequence[float],
                   full_first: bool) -> dict:
        best_av = dict(avail)
        best_val = self.total(best_av, scale)[0]
        self.n_evals += 1
        for i, step in enumerate(steps):
            if i == 0 and full_first:
                gc = _grid(None, step, None, self.lo["coal"])
                gg = _grid(None, step, None, self.lo["gas"])
            else:
                hw = steps[i - 1] if i > 0 else step * 5
                gc = _grid(best_av["coal"], step, hw, self.lo["coal"])
                gg = _grid(best_av["gas"], step, hw, self.lo["gas"])
            for c in gc:
                for g in gg:
                    cand = {"coal": float(c), "gas": float(g)}
                    if not is_feasible(cand, self.fb):
                        continue
                    v = self.total(cand, scale)[0]
                    self.n_evals += 1
                    if v < best_val - 1e-12:
                        best_val, best_av = v, cand
        return best_av


def fit(train_dates: Sequence[str], config: FitConfig,
        loader: Callable[[str], DayInputs] | None = None,
        days: Sequence[DayData] | None = None, verbose: bool = False) -> FitResult:
    """Fit availability (coal, gas) and per-month coal scale on ``train_dates`` only."""
    train_dates = list(train_dates)
    _check_train_only(train_dates)
    if days is None:
        days = load_day_data(train_dates, loader)
    elif [d.date for d in days] != train_dates:
        raise ValueError("days do not match train_dates")
    fb = feasibility_bounds(days) if config.feasibility else None
    f = _Fitter(days, fb)
    avail = {k: _box(config.init_avail[k], f.lo[k], AVAIL_BOUNDS[1]) for k in FITTED_AVAIL_FUELS}
    if not is_feasible(avail, fb):
        raise ValueError(f"initial availability {avail} infeasible under {fb}")
    scale = {m: _box(config.init_coal_scale, *COAL_SCALE_BOUNDS) for m in f.months}
    plateaus: dict[int, list[float]] = {}
    history = []

    if config.outer_avail_grid is not None:
        # global outer grid over availabilities, exact per-month inner minimization
        best = None
        g = _grid(None, config.outer_avail_grid, None) + config.outer_grid_offset
        g = g[(g >= AVAIL_BOUNDS[0]) & (g <= AVAIL_BOUNDS[1])]
        gc = np.unique(np.concatenate([g[g >= f.lo["coal"]], [f.lo["coal"]]]))
        gg_ = np.unique(np.concatenate([g[g >= f.lo["gas"]], [f.lo["gas"]]]))
        for c in gc:
            for gg in gg_:
                av = {"coal": float(c), "gas": float(gg)}
                if not is_feasible(av, fb):
                    continue
                sc, pl = f.scale_step(av, scale)
                v = f.total(av, sc)[0]
                if best is None or v < best[0] - 1e-12:
                    best = (v, av, sc, pl)
        _, avail, scale, plateaus = best
        history.append({"step": "outer_grid", "objective": best[0], "avail": dict(avail)})

    first_full = config.outer_avail_grid is None
    it = 0
    for it in range(1, config.max_iter + 1):
        prev = (dict(avail), dict(scale))
        order = ("scale", "avail") if config.first_step == "scale" else ("avail", "scale")
        for step in order:
            if step == "scale":
                scale, plateaus = f.scale_step(avail, scale)
            else:
                avail = f.avail_step(avail, scale, config.avail_grid_steps,
                                     full_first=first_full)
                first_full = False  # later rounds refine locally
            obj = f.total(avail, scale)
            history.append({"iteration": it, "step": step, "objective": obj[0],
                            "avail": dict(avail),
                            "coal_scale": {str(m): s for m, s in scale.items()}})
            if verbose:
                print(f"  [{config.name}] it {it} {step:5s} obj {obj[0]:.5f} "
                      f"(co2 {obj[1]:.4f} share {obj[2]:.4f}) avail {avail}")
        if (avail, scale) == prev:
            break
    obj = f.total(avail, scale)
    params = make_params(avail, scale)
    exact = objective(params, days)  # DaySimulator-based; must equal the fast path
    if not math.isclose(exact, obj[0], rel_tol=1e-7, abs_tol=1e-9):
        raise AssertionError(f"fast objective {obj[0]} != DaySimulator objective {exact}")
    return FitResult(
        params=params, train_objective=exact, train_co2_term=obj[1],
        train_share_term=obj[2], iterations=it, history=history, scale_plateaus=plateaus,
        config=config, feasibility=fb,
    )


# --- validation --------------------------------------------------------------------------------


def _ols_slope(dx: np.ndarray, dy: np.ndarray) -> dict:
    X = np.column_stack([np.ones_like(dx), dx])
    coef, *_ = np.linalg.lstsq(X, dy, rcond=None)
    pred = X @ coef
    ss_res = float(np.sum((dy - pred) ** 2))
    ss_tot = float(np.sum((dy - dy.mean()) ** 2))
    return {"slope_t_per_mwh": float(coef[1]), "intercept_t": float(coef[0]),
            "r2": 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan"), "n_pairs": int(len(dx))}


def _mape(sim: np.ndarray, obs: np.ndarray) -> float:
    return float(np.mean(np.abs(sim - obs) / obs))


def validate(params: SimParams, dates: Sequence[str] | None = None,
             days: Sequence[DayData] | None = None) -> dict:
    """Validation (a), (b), (c) of ``params`` on ``dates`` (see :data:`DEFINITIONS`)."""
    if days is None:
        days = load_day_data(list(dates))
    sims = simulate_days(params, days)
    keys = ("coal_mw", "gas_mw", "oil_mw", "fossil_mw", "co2_t", "coal_share")
    obs_all = {k: [] for k in keys}
    sim_all = {k: [] for k in keys}
    dO_co2, dO_gen, dS_co2, dS_gen = [], [], [], []
    mrate, frate, curt_margin, curt_mw, daily_rel = [], [], [], [], []
    margin_counts = {"curtailed_renewables": 0, "coal": 0, "gas": 0, "oil": 0}
    fossil_margin_counts = {"coal": 0, "gas": 0, "oil": 0}
    unserved = 0.0
    unserved_mw = []
    for d, sim in zip(days, sims, strict=True):
        s = simulated_hourly(sim)
        for k in keys:
            obs_all[k].append(d.obs[k])
            sim_all[k].append(s[k])
        dO_co2.append(np.diff(d.obs["co2_t"]))
        dO_gen.append(np.diff(d.obs["fossil_mw"]))
        dS_co2.append(np.diff(s["co2_t"]))
        dS_gen.append(np.diff(s["fossil_mw"]))
        mr = sim.marginal_rate(None)
        mrate.append(mr)
        curt_mw.append(sim.inputs.curtailed_mw)
        is_curt = []
        for res in sim.marginal_resource(None):
            margin_counts[res["kind"] if res["kind"] != "fossil" else res["fuel"]] += 1
            is_curt.append(res["kind"] == "curtailed_renewables")
        curt_margin.append(np.asarray(is_curt))
        # fossil plant at the margin once curtailment is used up (added load = curtailed MW)
        frate.append(sim.marginal_rate(sim.inputs.curtailed_mw))
        for res in sim.marginal_resource(sim.inputs.curtailed_mw):
            fossil_margin_counts[res["fuel"]] += 1
        daily_rel.append((s["co2_t"].sum() - d.obs["co2_t"].sum()) / d.obs["co2_t"].sum())
        unserved += sim.unserved_mwh(None)
        unserved_mw.append(np.maximum(0.0, sim.fossil_requirement(None) - sim.stack.total_mw))
    obs_all = {k: np.concatenate(v) for k, v in obs_all.items()}
    sim_all = {k: np.concatenate(v) for k, v in sim_all.items()}
    mrate = np.concatenate(mrate)
    curt_mask = np.concatenate(curt_margin)
    frate = np.concatenate(frate)
    curt_mw = np.concatenate(curt_mw)
    daily_rel = np.asarray(daily_rel)
    n_hours = len(obs_all["co2_t"])
    mef_obs = _ols_slope(np.concatenate(dO_gen), np.concatenate(dO_co2))
    mef_sim = _ols_slope(np.concatenate(dS_gen), np.concatenate(dS_co2))
    obj = (float(np.sum(np.abs(sim_all["co2_t"] - obs_all["co2_t"]) / obs_all["co2_t"]))
           + float(np.sum(np.abs(sim_all["coal_share"] - obs_all["coal_share"])))) / n_hours
    share_mae = float(np.mean(np.abs(sim_all["coal_share"] - obs_all["coal_share"])))
    return {
        "n_days": len(days),
        "n_hours": n_hours,
        "objective": obj,
        "a_coal_gas_split": {
            "coal_share_mae": share_mae,
            "coal_share_mae_pp": 100.0 * share_mae,
            "coal_share_bias_pp": 100.0 * float(np.mean(sim_all["coal_share"]
                                                         - obs_all["coal_share"])),
            "coal_mw_mape": _mape(sim_all["coal_mw"], obs_all["coal_mw"]),
            "gas_mw_mape": _mape(sim_all["gas_mw"], obs_all["gas_mw"]),
            "mean_coal_share_obs": float(obs_all["coal_share"].mean()),
            "mean_coal_share_sim": float(sim_all["coal_share"].mean()),
            "coal_mwh_obs": float(obs_all["coal_mw"].sum()),
            "coal_mwh_sim": float(sim_all["coal_mw"].sum()),
            "gas_mwh_obs": float(obs_all["gas_mw"].sum()),
            "gas_mwh_sim": float(sim_all["gas_mw"].sum()),
            "oil_mwh_sim": float(sim_all["oil_mw"].sum()),
            "coal_dispatched_hours_frac": float(np.mean(sim_all["coal_mw"] > 0)),
        },
        "b_marginal_emission_factor": {
            "mef_observed": mef_obs["slope_t_per_mwh"],
            "mef_simulated": mef_sim["slope_t_per_mwh"],
            "observed_regression": mef_obs,
            "simulated_regression": mef_sim,
            "mean_marginal_rate": float(mrate.mean()),
            "mean_fossil_marginal_rate": (float(mrate[~curt_mask].mean())
                                          if (~curt_mask).any() else None),
            "marginal_resource_share_of_hours": {k: v / n_hours
                                                 for k, v in margin_counts.items()},
            "fossil_margin_share_of_hours": {k: v / n_hours
                                             for k, v in fossil_margin_counts.items()},
            "mean_fossil_margin_rate": float(frate.mean()),
            "hours_with_curtailment_frac": float(np.mean(curt_mw > 0)),
            "curtailed_mw_median": float(np.median(curt_mw)),
            "curtailed_mw_min": float(curt_mw.min()),
            "margin_note": "marginal_resource / marginal_rate describe the next "
                           "MARGINAL_PROBE_MW (100 MW) block at zero added load: curtailed "
                           "renewables if remaining curtailment covers >= 50 MW of it, else the "
                           "fossil plant at the requirement after the probe; the rate is the "
                           "probe's average incremental CO2. fossil_margin_* is the same probe "
                           "once all curtailment is used up "
                           "(DaySimulator.marginal_resource(added=curtailed_mw)).",
        },
        "c_fossil_co2": {
            "hourly_mape": _mape(sim_all["co2_t"], obs_all["co2_t"]),
            "hourly_bias": float(np.mean((sim_all["co2_t"] - obs_all["co2_t"]) / obs_all["co2_t"])),
            "daily_co2_mape": float(np.mean(np.abs(daily_rel))),
            "daily_co2_bias": float(np.mean(daily_rel)),
            "total_co2_bias": float(sim_all["co2_t"].sum() / obs_all["co2_t"].sum() - 1.0),
            "total_co2_obs_t": float(obs_all["co2_t"].sum()),
            "total_co2_sim_t": float(sim_all["co2_t"].sum()),
            "target": FOSSIL_CO2_TARGET,
            "target_met": bool(_mape(sim_all["co2_t"], obs_all["co2_t"]) <= FOSSIL_CO2_TARGET),
        },
        "fossil_mwh_obs": float(obs_all["fossil_mw"].sum()),
        "fossil_mwh_sim": float(sim_all["fossil_mw"].sum()),
        "unserved_mwh": unserved,
        "unserved_hours_frac": float(np.mean(np.concatenate(unserved_mw) > 0)),
        "unserved_mw_max": float(np.concatenate(unserved_mw).max()),
        "stack_available_mw": {pm: float(st) for pm, st in sorted(
            {d.inputs.price_month: s.stack.total_mw for d, s in zip(days, sims, strict=True)}
            .items())},
        "obs_fossil_mw_max": float(obs_all["fossil_mw"].max()),
        "obs_gas_mw_max": float(obs_all["gas_mw"].max()),
    }


def headline(v: dict) -> dict:
    """The four numbers served by /api/meta's calibration block."""
    return {
        "coal_gas_split_error": v["a_coal_gas_split"]["coal_share_mae"],
        "mef_simulated": v["b_marginal_emission_factor"]["mef_simulated"],
        "mef_observed": v["b_marginal_emission_factor"]["mef_observed"],
        "fossil_co2_error": v["c_fossil_co2"]["hourly_mape"],
    }


# --- attempts ----------------------------------------------------------------------------------
# Attempt 1 (unconstrained) was the first pre-registered run. It met the holdout target but was
# REJECTED by the orchestrator because the fitted stack cannot serve observed peaks (unserved
# energy in ~3% of hours). Attempt 1b is the same optimizer, objective and bounds with the
# physical feasibility constraints of :func:`feasibility_bounds` (training data only) and is
# adopted regardless of the target; attempts 2-5 run (under the same constraints) only if 1b
# misses the target.

ATTEMPT_1_UNCONSTRAINED = FitConfig(
    "1_alt_from_default",
    "Alternating coordinate descent from the uncalibrated defaults (availability 0.9 / 0.9, coal "
    "scale 1.0); coal-scale step first. No feasibility constraint.",
    init_avail={"coal": 0.9, "gas": 0.9}, init_coal_scale=1.0, first_step="scale")
ATTEMPT_1_REJECTION = ("rejected: stack infeasible at observed peaks (unserved energy) -- fitted "
                       "gas availability leaves less available capacity than the observed peak "
                       "fossil and gas generation")

ATTEMPTS: tuple[FitConfig, ...] = (
    FitConfig("1b_feasible",
              "Attempt 1 (same optimizer, objective, bounds, start) with the feasibility "
              "constraints: coal_av x coal_cap >= max training coal MW, gas_av x gas_cap >= max "
              "training gas MW, total available >= max training fossil MW.",
              init_avail={"coal": 0.9, "gas": 0.9}, init_coal_scale=1.0, first_step="scale",
              feasibility=True),
    FitConfig("2_alt_from_low_feasible",
              "Feasibility-constrained; start availability coal 0.6 / gas 0.9 (raised to the "
              "feasibility floor), coal scale 0.5; availability step first.",
              init_avail={"coal": 0.6, "gas": 0.9}, init_coal_scale=0.5, first_step="avail",
              feasibility=True),
    FitConfig("3_outer_grid_feasible",
              "Feasibility-constrained; global outer grid over (coal, gas) availability at step "
              "0.05 (plus the floors) with exact per-month inner minimization, then alternating "
              "refinement.",
              init_avail={"coal": 0.9, "gas": 0.9}, init_coal_scale=1.0,
              outer_avail_grid=0.05, feasibility=True),
    FitConfig("4_alt_from_high_scale_feasible",
              "Feasibility-constrained; start availability at the floors, coal scale 2.0; "
              "availability step first, local steps (0.05, 0.005, 0.001).",
              init_avail={"coal": 0.3, "gas": 0.3}, init_coal_scale=2.0, first_step="avail",
              avail_grid_steps=(0.05, 0.005, 0.001), feasibility=True),
    FitConfig("5_outer_grid_offset_feasible",
              "Feasibility-constrained; outer grid on the offset points 0.325, ..., 0.975 (plus "
              "the floors) with exact per-month inner minimization, then alternating refinement.",
              init_avail={"coal": 0.9, "gas": 0.9}, init_coal_scale=1.0,
              outer_avail_grid=0.05, outer_grid_offset=0.025, feasibility=True),
)
