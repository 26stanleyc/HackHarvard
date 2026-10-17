"""Pydantic v2 models for the HTTP API contract (docs/design.md, section B6).

Field names match B6 exactly. All models forbid unknown fields, so fixtures and
responses must be schema-pure.

Hour convention (used everywhere: bids, schedules, hourly arrays)
-----------------------------------------------------------------
* A day is 24 hourly slots indexed ``0..23``; slot ``h`` covers ``[h:00, h+1:00)``
  local time, midnight to midnight. No window crosses midnight.
* ``earliest_hour`` is an int in ``0..23`` and is the INCLUSIVE start slot.
* ``deadline_hour`` is an int in ``1..24`` and is the EXCLUSIVE end: the bid must be
  finished by ``deadline_hour:00``. ``deadline_hour = 24`` means "by midnight".
* A bid may draw power in slot ``h`` iff ``earliest_hour <= h < deadline_hour``, so its
  window has ``deadline_hour - earliest_hour`` hours.
* Schedules are ``{bid_id: [24 x MW]}``; because each slot is one hour, MW in a slot
  equals MWh delivered in that slot.

Bid feasibility (design B3): ``earliest_hour < deadline_hour`` and
``energy_mwh <= max_mw * (deadline_hour - earliest_hour)``. Request models do NOT
reject infeasible bids; the API layer calls :func:`bid_feasibility_error` per bid and
returns 422 with per-bid messages. Only malformed input (wrong types, out-of-range
hours, negative quantities) fails model validation.

Units and sign conventions
--------------------------
* Power in MW, energy in MWh, emissions in metric tons CO2 (``_t``), intensity and
  emission rates in t/MWh, money in USD.
* Savings-like fields (``predicted_t``, ``realized_t``, ``total_avoided_t``,
  ``shapley_t``, ``vs_default_t``, ``vs_independent_average_t``) are positive when
  CO2 is avoided relative to the stated comparison. ``gap_t = predicted_t - realized_t``.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

HOURS = 24
FEASIBILITY_TOL_MWH = 1e-6

StrategyName = Literal["default", "independent_average", "independent_marginal", "coordinated"]
STRATEGY_NAMES: tuple[str, ...] = (
    "default",
    "independent_average",
    "independent_marginal",
    "coordinated",
)

# Exactly 24 hourly values.
Hourly24 = Annotated[list[float], Field(min_length=HOURS, max_length=HOURS)]
Hour = Annotated[int, Field(ge=0, le=HOURS - 1)]
NonNeg = Annotated[float, Field(ge=0)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_all_strategies(strategies: dict[str, object]) -> None:
    missing = [s for s in STRATEGY_NAMES if s not in strategies]
    if missing:
        raise ValueError(f"strategies missing keys: {missing}")


# --- GET /api/meta ---------------------------------------------------------------------------


class Day(Model):
    date: str  # ISO date, e.g. "2024-03-19"
    label: str
    description: str


class Calibration(Model):
    """Held-out validation metrics (design B1). ``None`` until M3 computes them."""

    coal_gas_split_error: float | None
    mef_simulated: float | None
    mef_observed: float | None
    fossil_co2_error: float | None


class MetaResponse(Model):
    region: str
    days: list[Day]
    default_day: str
    carbon_price_default: NonNeg
    pool_default_usd: NonNeg
    background_pct_default: NonNeg
    max_bids: int = 8
    calibration: Calibration
    data_sources: list[str]

    @model_validator(mode="after")
    def _default_day_listed(self) -> MetaResponse:
        if self.default_day not in {d.date for d in self.days}:
            raise ValueError("default_day must be one of days[].date")
        return self


# --- GET /api/presets ------------------------------------------------------------------------


class Preset(Model):
    name: str
    load_type: str
    energy_mwh: NonNeg
    earliest_hour: Hour
    deadline_hour: Annotated[int, Field(ge=1, le=HOURS)]
    max_mw: NonNeg
    size_reference: str


class PresetsResponse(RootModel[list[Preset]]):
    pass


# --- GET /api/adoption-chart -----------------------------------------------------------------


class AdoptionSeries(Model):
    mean_co2_t: list[float]
    band_low: list[float]
    band_high: list[float]


class AdoptionChartResponse(Model):
    x_pct_of_peak: list[float]
    strategies: dict[StrategyName, AdoptionSeries]

    @model_validator(mode="after")
    def _check(self) -> AdoptionChartResponse:
        _require_all_strategies(self.strategies)
        n = len(self.x_pct_of_peak)
        for name, s in self.strategies.items():
            for field in ("mean_co2_t", "band_low", "band_high"):
                if len(getattr(s, field)) != n:
                    raise ValueError(f"{name}.{field} must have len(x_pct_of_peak)={n}")
        return self


# --- Bids ------------------------------------------------------------------------------------


class Bid(Model):
    id: str = Field(min_length=1)
    name: str
    load_type: str
    energy_mwh: NonNeg
    earliest_hour: Hour  # inclusive start slot, 0..23
    deadline_hour: Annotated[int, Field(ge=1, le=HOURS)]  # exclusive end, 1..24
    max_mw: NonNeg


def bid_feasibility_error(bid: Bid) -> str | None:
    """Return a human-readable reason the bid is infeasible, or ``None`` if feasible."""
    if bid.earliest_hour >= bid.deadline_hour:
        return (
            f"earliest_hour ({bid.earliest_hour}) must be before deadline_hour "
            f"({bid.deadline_hour}); windows cannot cross midnight"
        )
    window_h = bid.deadline_hour - bid.earliest_hour
    capacity = bid.max_mw * window_h
    if bid.energy_mwh > capacity + FEASIBILITY_TOL_MWH:
        return (
            f"energy_mwh ({bid.energy_mwh:g}) exceeds max_mw x window "
            f"({bid.max_mw:g} MW x {window_h} h = {capacity:g} MWh)"
        )
    return None


def _check_unique_ids(bids: list[Bid]) -> None:
    ids = [b.id for b in bids]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate bid ids: {dupes}")


# --- POST /api/clear -------------------------------------------------------------------------


class ClearRequest(Model):
    day: str
    bids: list[Bid]
    background_pct: NonNeg
    carbon_price: NonNeg
    pool_usd: NonNeg

    @model_validator(mode="after")
    def _unique(self) -> ClearRequest:
        _check_unique_ids(self.bids)
        return self


class ForecastHourly(Model):
    demand: Hourly24  # MW
    wind: Hourly24  # MW
    solar: Hourly24  # MW
    avg_intensity: Hourly24  # t/MWh


class Forecast(Model):
    source: Literal["chalk", "local"]
    latency_ms: NonNeg
    hourly: ForecastHourly
    # Set when Chalk is configured but failed, so the local model served this forecast.
    fallback_reason: str | None = None


class BidFeatures(Model):
    """Derived features of one bid (``provider.BID_FEATURE_NAMES``), served by the provider.

    Day-specific fields use the day's forecast (never actuals) and are ``None`` when the bid's
    window is empty.
    """

    window_hours: int
    min_run_hours: float | None
    slack_hours: float | None
    flexibility_ratio: float | None
    avg_power_mw: float | None
    feasible: bool
    energy_share_of_day_demand: float | None
    max_mw_share_of_peak_demand: float | None
    window_mean_renewable_share: float | None
    window_curtailed_mwh: float | None
    window_lowest_net_load_hour: int | None


class BidFeaturesBlock(Model):
    source: Literal["chalk", "local"]
    latency_ms: NonNeg
    per_bid: dict[str, BidFeatures]  # bid_id -> features


class BidFeaturesRequest(Model):
    day: str
    bids: list[Bid]

    @model_validator(mode="after")
    def _unique(self) -> BidFeaturesRequest:
        _check_unique_ids(self.bids)
        return self


class MarginalResource(Model):
    kind: Literal["fossil", "curtailed_renewables"]
    plant_name: str | None
    fuel: str | None
    co2_t_per_mwh: NonNeg


MarginalResource24 = Annotated[list[MarginalResource], Field(min_length=HOURS, max_length=HOURS)]


class StrategyResult(Model):
    schedule: dict[str, Hourly24]  # bid_id -> 24 x MW
    hourly_co2_t: Hourly24
    total_co2_t: float
    marginal_resource: MarginalResource24


class HerdingMetric(Model):
    predicted_t: float
    realized_t: float
    gap_t: float


class Herding(Model):
    independent_average: HerdingMetric
    independent_marginal: HerdingMetric


class TraceStep(Model):
    step: Annotated[int, Field(ge=0)]
    bid_id: str
    hour: Hour
    mwh: NonNeg
    incremental_co2_t: float


class SettlementBid(Model):
    bid_id: str
    shapley_t: float
    payout_usd: NonNeg
    vs_default_t: float
    vs_independent_average_t: float


class LeaveOneOutBid(Model):
    bid_id: str
    shapley_t: float
    payout_usd: NonNeg


class LeaveOneOut(Model):
    total_avoided_t: float
    per_bid: list[LeaveOneOutBid]


class Settlement(Model):
    baseline: Literal["independent_marginal"]
    method: Literal["exact", "monte_carlo"]
    coalitions_evaluated: Annotated[int, Field(ge=0)]
    total_avoided_t: float
    pool_usd: NonNeg
    prorated: bool
    per_bid: list[SettlementBid]
    leave_one_out: dict[str, LeaveOneOut]  # removed bid_id -> game without it


class ClearResponse(Model):
    forecast: Forecast
    strategies: dict[StrategyName, StrategyResult]
    herding: Herding
    coordinator_trace: list[TraceStep]
    fallback_fired: bool
    settlement: Settlement
    bid_features: BidFeaturesBlock | None = None  # optional: older recorded fixtures lack it

    @model_validator(mode="after")
    def _all_strategies(self) -> ClearResponse:
        _require_all_strategies(self.strategies)
        return self


# --- POST /api/evaluate ----------------------------------------------------------------------


class EvaluateRequest(Model):
    day: str
    bids: list[Bid]
    schedule: dict[str, Hourly24]  # bid_id -> 24 x MW
    background_pct: NonNeg

    @model_validator(mode="after")
    def _unique(self) -> EvaluateRequest:
        _check_unique_ids(self.bids)
        return self


class BidError(Model):
    bid_id: str
    message: str


class EvaluateResponse(Model):
    feasible: bool
    errors: list[BidError]
    hourly_co2_t: Hourly24
    total_co2_t: float
    marginal_resource: MarginalResource24
