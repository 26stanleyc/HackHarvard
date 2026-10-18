/**
 * TypeScript mirror of the HTTP API contract (docs/design.md, B6).
 *
 * The authoritative version is backend/clearinghouse/schemas.py (pydantic v2,
 * extra="forbid"). Field names and nullability here match it exactly.
 *
 * Hour convention: a day is 24 slots 0..23, slot h covers [h:00, h+1:00).
 * earliest_hour (0..23) is the INCLUSIVE start slot; deadline_hour (1..24) is the
 * EXCLUSIVE end. A bid may draw power in slot h iff earliest_hour <= h < deadline_hour.
 * Schedules are {bid_id: [24 x MW]}; MW in a one-hour slot equals MWh delivered.
 *
 * Units: MW, MWh, metric tons CO2 (_t), t/MWh, USD. Savings-like fields are
 * positive when CO2 is avoided. gap_t = predicted_t - realized_t.
 */

export const HOURS = 24;
export const FEASIBILITY_TOL_MWH = 1e-6;

export type StrategyName =
  | "default"
  | "independent_average"
  | "independent_marginal"
  | "coordinated";

export const STRATEGY_NAMES: readonly StrategyName[] = [
  "default",
  "independent_average",
  "independent_marginal",
  "coordinated",
] as const;

/** Exactly 24 hourly values (length is validated by the backend, not the type). */
export type Hourly24 = number[];

// --- GET /api/meta ----------------------------------------------------------

export interface Day {
  /** ISO date, e.g. "2024-03-19" */
  date: string;
  label: string;
  description: string;
}

/** Held-out validation metrics (design B1). null until M3 computes them. */
export interface Calibration {
  coal_gas_split_error: number | null;
  mef_simulated: number | null;
  mef_observed: number | null;
  fossil_co2_error: number | null;
}

/** Backend SHAPLEY_MAX_BIDS (= settlement.EXACT_MAX_PLAYERS); fallback when meta lacks it. */
export const SHAPLEY_MAX_BIDS_DEFAULT = 8;

export interface MetaResponse {
  region: string;
  days: Day[];
  default_day: string;
  carbon_price_default: number;
  pool_default_usd: number;
  background_pct_default: number;
  max_bids: number;
  /** above this many bids /api/clear skips the Shapley settlement (absent in old fixtures) */
  shapley_max_bids?: number;
  calibration: Calibration;
  data_sources: string[];
}

// --- GET /api/presets -------------------------------------------------------

export interface Preset {
  name: string;
  load_type: string;
  energy_mwh: number;
  earliest_hour: number;
  deadline_hour: number;
  max_mw: number;
  size_reference: string;
}

export type PresetsResponse = Preset[];

// --- GET /api/adoption-chart ------------------------------------------------

export interface AdoptionSeries {
  mean_co2_t: number[];
  band_low: number[];
  band_high: number[];
}

export interface AdoptionChartResponse {
  x_pct_of_peak: number[];
  strategies: Record<StrategyName, AdoptionSeries>;
}

// --- Bids -------------------------------------------------------------------

export interface Bid {
  id: string;
  name: string;
  load_type: string;
  energy_mwh: number;
  /** inclusive start slot, 0..23 */
  earliest_hour: number;
  /** exclusive end, 1..24 */
  deadline_hour: number;
  max_mw: number;
}

// --- POST /api/clear --------------------------------------------------------

export interface ClearRequest {
  day: string;
  bids: Bid[];
  background_pct: number;
  carbon_price: number;
  pool_usd: number;
}

export interface ForecastHourly {
  /** MW */
  demand: Hourly24;
  /** MW */
  wind: Hourly24;
  /** MW */
  solar: Hourly24;
  /** t/MWh */
  avg_intensity: Hourly24;
}

export interface Forecast {
  source: "chalk" | "local";
  latency_ms: number;
  hourly: ForecastHourly;
  /** Set when Chalk is configured but failed, so the local model served this forecast. */
  fallback_reason?: string | null;
}

// --- bid features (POST /api/bid-features, and ClearResponse.bid_features) ---

/** Derived features of one bid, served by the forecast provider (Chalk when configured). */
export interface BidFeatures {
  window_hours: number;
  min_run_hours: number | null;
  slack_hours: number | null;
  flexibility_ratio: number | null;
  avg_power_mw: number | null;
  feasible: boolean;
  /** Day-specific (from the day's forecast, never actuals); null for an empty window. */
  energy_share_of_day_demand: number | null;
  max_mw_share_of_peak_demand: number | null;
  /** Mean (wind + solar) / demand over the window, 0..1 */
  window_mean_renewable_share: number | null;
  window_curtailed_mwh: number | null;
  window_lowest_net_load_hour: number | null;
}

export interface BidFeaturesBlock {
  source: "chalk" | "local";
  latency_ms: number;
  /** bid_id -> features */
  per_bid: Record<string, BidFeatures>;
}

export interface MarginalResource {
  kind: "fossil" | "curtailed_renewables";
  plant_name: string | null;
  fuel: string | null;
  co2_t_per_mwh: number;
}

export interface StrategyResult {
  /** bid_id -> 24 x MW */
  schedule: Record<string, Hourly24>;
  hourly_co2_t: Hourly24;
  total_co2_t: number;
  marginal_resource: MarginalResource[];
}

export interface HerdingMetric {
  predicted_t: number;
  realized_t: number;
  gap_t: number;
}

export interface Herding {
  independent_average: HerdingMetric;
  independent_marginal: HerdingMetric;
}

export interface TraceStep {
  step: number;
  bid_id: string;
  hour: number;
  mwh: number;
  incremental_co2_t: number;
}

export interface SettlementBid {
  bid_id: string;
  shapley_t: number;
  payout_usd: number;
  vs_default_t: number;
  vs_independent_average_t: number;
}

export interface LeaveOneOutBid {
  bid_id: string;
  shapley_t: number;
  payout_usd: number;
}

export interface LeaveOneOut {
  total_avoided_t: number;
  per_bid: LeaveOneOutBid[];
}

export interface Settlement {
  baseline: "independent_marginal";
  /** "marginal": more than meta.shapley_max_bids bids, shares from each company's marginal
   * contribution; "skipped" (older backends): per_bid and leave_one_out are empty */
  method: "exact" | "marginal" | "skipped";
  coalitions_evaluated: number;
  total_avoided_t: number;
  pool_usd: number;
  prorated: boolean;
  per_bid: SettlementBid[];
  /** removed bid_id -> game without it */
  leave_one_out: Record<string, LeaveOneOut>;
}

export interface ClearResponse {
  forecast: Forecast;
  strategies: Record<StrategyName, StrategyResult>;
  herding: Herding;
  coordinator_trace: TraceStep[];
  fallback_fired: boolean;
  settlement: Settlement;
  /** Absent in older recorded fixtures. */
  bid_features?: BidFeaturesBlock | null;
}

// --- POST /api/evaluate -----------------------------------------------------

export interface EvaluateRequest {
  day: string;
  bids: Bid[];
  /** bid_id -> 24 x MW */
  schedule: Record<string, Hourly24>;
  background_pct: number;
}

export interface BidError {
  bid_id: string;
  message: string;
}

export interface EvaluateResponse {
  feasible: boolean;
  errors: BidError[];
  hourly_co2_t: Hourly24;
  total_co2_t: number;
  marginal_resource: MarginalResource[];
}
