/** Formatting helpers. Numbers on screen always come from the API; these only format. */

/**
 * Python's `format(x, "g")` (6 significant digits, trailing zeros stripped,
 * exponent notation when exp < -4 or exp >= 6). Used so client-side feasibility
 * messages match backend `bid_feasibility_error` text exactly.
 */
export function formatG(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (!Number.isFinite(x)) return x > 0 ? "inf" : "-inf";
  if (x === 0) return Object.is(x, -0) ? "-0" : "0";
  const [mantissa, expStr] = x.toExponential(5).split("e");
  const exp = Number(expStr);
  if (exp < -4 || exp >= 6) {
    const m = mantissa.includes(".") ? mantissa.replace(/\.?0+$/, "") : mantissa;
    const sign = exp < 0 ? "-" : "+";
    return `${m}e${sign}${String(Math.abs(exp)).padStart(2, "0")}`;
  }
  const fixed = x.toFixed(Math.max(0, 5 - exp));
  return fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed;
}

const nf0 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1 });
const usd0 = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

/** Tons with sensible precision: 1 decimal under 1,000, else integer. */
export function fmtT(t: number): string {
  return Math.abs(t) < 1000 ? nf1.format(t) : nf0.format(t);
}

/** Signed tons, e.g. "+120.0" / "−45.3" (true minus sign). */
export function fmtSignedT(t: number): string {
  const s = fmtT(Math.abs(t));
  if (Math.abs(t) < 0.05) return s;
  return t > 0 ? `+${s}` : `−${s}`;
}

/** Tons for Shapley shares: like fmtT, but keeps 2 decimals for small non-zero values. */
export function fmtShareT(t: number): string {
  const a = Math.abs(t);
  if (a > 0 && a < 0.1) return t.toFixed(2);
  return fmtT(t);
}

/** "120.9 t avoided" / "16.8 t more" (never "−16.8 t avoided"). */
export function fmtAvoided(t: number): string {
  if (Math.abs(t) < 0.05) return "0 t";
  return t > 0 ? `${fmtT(t)} t avoided` : `${fmtT(-t)} t more`;
}

export function fmtInt(x: number): string {
  return nf0.format(x);
}

export function fmtNum1(x: number): string {
  return nf1.format(x);
}

export function fmtUsd(x: number): string {
  return usd0.format(x);
}

export function fmtPct(fraction: number, digits = 1): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

/** "03:00" */
export function hh(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

/** Slot h as "03:00–04:00". */
export function slotLabel(h: number): string {
  return `${hh(h)}–${hh(h + 1)}`;
}

/** Window [earliest, deadline) as "00:00–08:00". */
export function windowLabel(earliest: number, deadline: number): string {
  return `${hh(earliest)}–${hh(deadline)}`;
}

/**
 * Compress the active hours of a 24-slot MW array into readable runs, e.g.
 * [{start: 0, end: 2, mw: [240, 0]}...]. Consecutive non-zero hours form a run.
 */
export function activeRuns(mw: number[]): { start: number; end: number; mwh: number }[] {
  const runs: { start: number; end: number; mwh: number }[] = [];
  let cur: { start: number; end: number; mwh: number } | null = null;
  mw.forEach((v, h) => {
    if (v > 1e-9) {
      if (cur && cur.end === h) {
        cur.end = h + 1;
        cur.mwh += v;
      } else {
        cur = { start: h, end: h + 1, mwh: v };
        runs.push(cur);
      }
    }
  });
  return runs;
}

export function titleCase(s: string): string {
  return s
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
