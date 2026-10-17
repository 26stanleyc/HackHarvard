import type { MarginalResource, StrategyName } from "./types";

/**
 * Company colors: fixed categorical order (validated reference palette, adjacent
 * pairs CVD-safe in stacks). Color follows the company's position in the bid
 * list, never its rank. Companies 9+ (up to max_bids = 30) reuse the 8 hues as
 * darker, lighter, then darker-still shades, so no two companies share a color.
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

const SHADE_TIERS = [0, -0.35, 0.45, -0.6]; // <0 mixes toward black, >0 toward white

function shade(hex: string, t: number): string {
  const target = t < 0 ? 0 : 255;
  const a = Math.abs(t);
  const ch = [1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * (1 - a) + target * a));
  return `#${ch.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

export function companyColor(index: number): string {
  const i = Math.max(0, index);
  const n = COMPANY_COLORS.length;
  const tier = SHADE_TIERS[Math.min(SHADE_TIERS.length - 1, Math.floor(i / n))];
  return tier === 0 ? COMPANY_COLORS[i % n] : shade(COMPANY_COLORS[i % n], tier);
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
