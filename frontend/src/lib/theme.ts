import type { MarginalResource, StrategyName } from "./types";

/**
 * Company colors: fixed categorical order (validated reference palette, adjacent
 * pairs CVD-safe in stacks). Color follows the company's position in the bid
 * list, never its rank, and never cycles past 8 (max_bids = 8).
 */
export const COMPANY_COLORS = [
  "#2a78d6",
  "#eb6834",
  "#1baf7a",
  "#eda100",
  "#e87ba4",
  "#008300",
  "#6250d6",
  "#e34948",
] as const;

export function companyColor(index: number): string {
  return COMPANY_COLORS[Math.max(0, Math.min(COMPANY_COLORS.length - 1, index))];
}

/**
 * Strategy colors. "Default" is a deliberate neutral (the do-nothing reference),
 * drawn dashed so it never relies on color alone.
 */
export const STRATEGY_COLORS: Record<StrategyName, string> = {
  default: "#7d7c76",
  independent_average: "#eb6834",
  independent_marginal: "#2a78d6",
  coordinated: "#0f7a55",
};

export const STRATEGY_LABEL: Record<StrategyName, string> = {
  default: "Default",
  independent_average: "Independent, average signal",
  independent_marginal: "Independent, marginal signal",
  coordinated: "Coordinated",
};

export const STRATEGY_SHORT: Record<StrategyName, string> = {
  default: "Default",
  independent_average: "Indep. average",
  independent_marginal: "Indep. marginal",
  coordinated: "Coordinated",
};

export const STRATEGY_BLURB: Record<StrategyName, string> = {
  default: "Each company runs at full power as soon as its window opens.",
  independent_average:
    "Each company uses the standard carbon-aware signal (forecast average intensity) and runs in its cleanest hours, unaware of the others.",
  independent_marginal:
    "Each company, alone, places its own energy where its added load emits least, assuming the others run default. The best a company can do by itself.",
  coordinated:
    "The coordinator places small blocks of everyone's energy, one at a time, wherever the next block adds the least simulated CO2 given all blocks already placed.",
};

/** Fuel shading for the plant-on-the-margin strip (sequential by CO2 rate, one hue). */
export function marginalFill(m: MarginalResource): string {
  if (m.kind === "curtailed_renewables" || m.co2_t_per_mwh <= 0) return "#d8eadf";
  const r = Math.min(1.1, m.co2_t_per_mwh);
  // light -> dark ink as the rate rises (0 .. ~1.1 t/MWh)
  const steps = ["#e9e6dd", "#cfc9b8", "#ada591", "#867d69", "#5e5645", "#3b3528"];
  return steps[Math.min(steps.length - 1, Math.floor((r / 1.1) * (steps.length - 1) + 0.5))];
}

export function marginalLabel(m: MarginalResource): string {
  if (m.kind === "curtailed_renewables") {
    return `Curtailed renewables (0 t/MWh)${m.fuel ? ` · ${m.fuel}` : ""}`;
  }
  const fuel = m.fuel ? m.fuel : "fossil";
  return `${m.plant_name ?? "Unnamed plant"} · ${fuel} · ${m.co2_t_per_mwh.toFixed(2)} t/MWh`;
}
