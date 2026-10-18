/**
 * Plain-language explanations of a clear response, every number computed from the
 * response itself (never hard-coded). Used where a result is zero, tied or negative,
 * so the UI explains it instead of showing an unexplained "0".
 */

import { fmtSignedT, fmtT, fmtUsd } from "./format";
import { STRATEGY_LABEL } from "./theme";
import {
  HOURS,
  STRATEGY_NAMES,
  type ClearResponse,
  type HerdingMetric,
  type LeaveOneOutBid,
  type MarginalResource,
  type StrategyName,
} from "./types";

/** Tons below this are shown as 0 by fmtSignedT, so treat them as zero here too. */
export const ZERO_T = 0.05;

export function isZeroT(t: number): boolean {
  return Math.abs(t) < ZERO_T;
}

/** Shapley shares are classified as positive / zero / negative with a tiny tolerance only. */
export const SHARE_EPS_T = 1e-6;

export function curtailedHourCount(m: MarginalResource[]): number {
  return m.filter((x) => x.kind === "curtailed_renewables").length;
}

/** Hours in which the summed schedule draws power. */
export function activeHours(schedule: Record<string, number[]>): number[] {
  const out: number[] = [];
  for (let h = 0; h < HOURS; h++) {
    let t = 0;
    for (const v of Object.values(schedule)) t += v[h] ?? 0;
    if (t > 1e-9) out.push(h);
  }
  return out;
}

export interface StrategyTie {
  tied: boolean;
  /** max − min of the four totals, t */
  spread: number;
  total: number;
  /** curtailed-renewables hours on the margin under the coordinated schedule */
  curtailedHours: number;
  /** hours where any strategy draws power */
  activeAny: number[];
  /** of activeAny, hours where every strategy has curtailed renewables on the margin */
  activeCurtailed: number;
}

export function strategyTie(r: ClearResponse): StrategyTie {
  const totals = STRATEGY_NAMES.map((s) => r.strategies[s].total_co2_t);
  const spread = Math.max(...totals) - Math.min(...totals);
  const act = new Set<number>();
  for (const s of STRATEGY_NAMES) for (const h of activeHours(r.strategies[s].schedule)) act.add(h);
  const activeAny = [...act].sort((a, b) => a - b);
  const activeCurtailed = activeAny.filter((h) =>
    STRATEGY_NAMES.every((s) => r.strategies[s].marginal_resource[h]?.kind === "curtailed_renewables"),
  ).length;
  return {
    tied: spread < ZERO_T,
    spread,
    total: r.strategies.default.total_co2_t,
    curtailedHours: curtailedHourCount(r.strategies.coordinated.marginal_resource),
    activeAny,
    activeCurtailed,
  };
}

/** "the load was absorbed by curtailed renewables" sentence, or a plain tie sentence. */
export function tieSentence(r: ClearResponse): string | null {
  const t = strategyTie(r);
  if (!t.tied) return null;
  if (t.activeAny.length > 0 && t.activeCurtailed === t.activeAny.length) {
    return (
      `On this day the added load was absorbed by curtailed renewables in ${t.curtailedHours} of 24 hours, ` +
      `including all ${t.activeAny.length} hours in which any strategy runs the companies. ` +
      `Every strategy emits the same ${fmtT(t.total)} t, so there was nothing to coordinate.`
    );
  }
  return (
    `All four strategies emit the same ${fmtT(t.total)} t on this day ` +
    `(curtailed renewables on the margin in ${t.curtailedHours} of 24 hours under the coordinated schedule).`
  );
}

export function fallbackSentence(r: ClearResponse): string | null {
  if (!r.fallback_fired) return null;
  const co = r.strategies.coordinated.total_co2_t;
  const im = r.strategies.independent_marginal.total_co2_t;
  const same = isZeroT(co - im);
  return (
    "Planning-time fallback fired: on the forecast the greedy coordinator did worse than the companies' " +
    "solo schedules, so the coordinator kept the independent (marginal-signal) schedules" +
    (same
      ? `. Coordinated and ${STRATEGY_LABEL.independent_marginal.toLowerCase()} are identical (${fmtT(co)} t).`
      : `; coordinated differs from ${STRATEGY_LABEL.independent_marginal.toLowerCase()} by ${fmtSignedT(co - im)} t.`)
  );
}

const FALLBACK_REASON =
  "the coordinator's joint plan looked worse than every company's solo plan on the forecast, so it " +
  "was set aside and each company ran its solo schedule";

const FALLBACK_LIKELIHOOD =
  "This is more common when the run has little flexible load (fewer or smaller companies); with " +
  "more, the joint plan usually wins, though not always.";

/**
 * What a fallback run pays, in plain words. When the fallback fires every company runs its solo
 * schedule, so coordination added exactly 0 t. Shares still compare groups without each company,
 * though: a company whose absence would have let the rest keep a joint plan that did worse on the
 * actual day gets a positive share and is paid, while negative shares are paid $0 (never charged).
 */
export function fallbackPayoutSentence(r: ClearResponse): string | null {
  if (!r.fallback_fired) return null;
  const per = r.settlement.per_bid;
  const paid = per.reduce((a, p) => a + p.payout_usd, 0);
  if (r.settlement.method === "skipped" || paid < 0.005) {
    return (
      `Nothing is paid because ${FALLBACK_REASON}. Coordination added nothing beyond what each ` +
      `company would do alone, and payouts only cover that difference. ${FALLBACK_LIKELIHOOD}`
    );
  }
  const paidTo = per.filter((p) => p.payout_usd > 0).length;
  return (
    `Coordination added 0 t overall: ${FALLBACK_REASON}. ${paidTo} ` +
    `${paidTo === 1 ? "company is" : "companies are"} still paid ${fmtUsd(paid)}, because shares also ` +
    `compare groups without each company: without ${paidTo === 1 ? "it" : "them"}, the others would ` +
    "have kept a joint plan that did worse on the actual day, so their presence counts as a saving. " +
    "Companies with negative shares are paid $0 " +
    "(nobody is charged), which is why payouts can exceed the carbon avoided. " +
    FALLBACK_LIKELIHOOD
  );
}

/** The fallback reason alone, for places that state the payouts separately. */
function fallbackTotalSentence(r: ClearResponse): string | null {
  if (!r.fallback_fired) return null;
  return `Coordination avoided 0 t because ${FALLBACK_REASON}.`;
}

export interface SettlementNotes {
  /** headline sentence when total avoided is zero or negative (null when positive) */
  total: string | null;
  /** sentence about who is paid when total ≤ 0 or some shares are negative */
  payouts: string | null;
}

export function settlementNotes(
  r: ClearResponse,
  perBid: LeaveOneOutBid[] = r.settlement.per_bid,
  totalAvoided: number = r.settlement.total_avoided_t,
): SettlementNotes {
  const st = r.settlement;
  const pos = perBid.filter((p) => p.shapley_t > SHARE_EPS_T);
  const neg = perBid.filter((p) => p.shapley_t < -SHARE_EPS_T);
  const paid = perBid.reduce((a, p) => a + p.payout_usd, 0);
  let total: string | null = null;
  const fallback = totalAvoided === st.total_avoided_t ? fallbackTotalSentence(r) : null;
  if (fallback) {
    total = fallback;
  } else if (isZeroT(totalAvoided)) {
    total = "Coordination avoided 0 t beyond the baseline (each company's best solo schedule).";
  } else if (totalAvoided < 0) {
    total =
      `On the actual day the coordinated plan emitted ${fmtT(-totalAvoided)} t more than the solo baseline ` +
      "(it was planned on the forecast and is scored on actuals), so coordination avoided nothing.";
  }
  let payouts: string | null = null;
  if (paid < 0.005) {
    payouts =
      total !== null && pos.length === 0
        ? `No company has a positive share, so every payout is $0 of the ${fmtUsd(st.pool_usd)} pool.`
        : `Every payout is $0 of the ${fmtUsd(st.pool_usd)} pool.`;
  } else if (total !== null || neg.length > 0) {
    const parts: string[] = [];
    parts.push(
      `${pos.length} ${pos.length === 1 ? "company has a positive share and is" : "companies have positive shares and are"} paid ${fmtUsd(paid)} in total`,
    );
    if (neg.length > 0) {
      parts.push(
        `${neg.length} with ${neg.length === 1 ? "a negative share is" : "negative shares are"} paid $0 (nobody is charged)`,
      );
    }
    payouts =
      `Shares sum to ${fmtSignedT(totalAvoided)} t: ${parts.join("; ")}.` +
      (total !== null && neg.length > 0 ? " Because negative shares are not charged, payouts can exceed the value of the carbon avoided." : "");
  }
  return { total, payouts };
}

/** Herding row: a sentence when predicted savings are zero or negative. */
export function herdingNote(m: HerdingMetric): string | null {
  if (isZeroT(m.predicted_t) && isZeroT(m.realized_t)) {
    return "No company could save anything by shifting alone (predicted 0 t), so there is no herding gap on this day.";
  }
  if (m.predicted_t < 0) {
    return (
      `Summed over companies, shifting alone (planned on the forecast) raises CO2 on the actual day ` +
      `(predicted ${fmtSignedT(m.predicted_t)} t), so there is no promised saving to fall short of; the gap compares ` +
      `two outcomes rather than a promise and a shortfall.`
    );
  }
  return null;
}

/** Gap as % of predicted, only meaningful when predicted savings are positive. */
export function herdingGapPct(m: HerdingMetric): number | null {
  return m.predicted_t >= ZERO_T ? (m.gap_t / m.predicted_t) * 100 : null;
}

/** The three comparisons of design B3: positive = CO2 avoided. */
export function comparisons(r: ClearResponse) {
  const t = (s: StrategyName) => r.strategies[s].total_co2_t;
  return {
    vsDefault: t("default") - t("coordinated"),
    betterSignal: t("independent_average") - t("independent_marginal"),
    coordination: t("independent_marginal") - t("coordinated"),
    averageVsDefault: t("default") - t("independent_average"),
    marginalVsDefault: t("default") - t("independent_marginal"),
  };
}
