import { formatG } from "./format";
import { FEASIBILITY_TOL_MWH, HOURS, type Bid } from "./types";

/**
 * Mirror of backend `bid_feasibility_error` (backend/clearinghouse/schemas.py).
 * Returns the same human-readable message, or null if the bid is feasible.
 */
export function bidFeasibilityError(bid: Bid): string | null {
  if (bid.earliest_hour >= bid.deadline_hour) {
    return (
      `earliest_hour (${bid.earliest_hour}) must be before deadline_hour ` +
      `(${bid.deadline_hour}); windows cannot cross midnight`
    );
  }
  const windowH = bid.deadline_hour - bid.earliest_hour;
  const capacity = bid.max_mw * windowH;
  if (bid.energy_mwh > capacity + FEASIBILITY_TOL_MWH) {
    return (
      `energy_mwh (${formatG(bid.energy_mwh)}) exceeds max_mw x window ` +
      `(${formatG(bid.max_mw)} MW x ${windowH} h = ${formatG(capacity)} MWh)`
    );
  }
  return null;
}

/**
 * Malformed-input checks that pydantic model validation would reject
 * (wrong ranges, negative quantities, empty id). Returns null when well-formed.
 */
export function bidShapeError(bid: Bid): string | null {
  if (!bid.id) return "id must not be empty";
  if (!bid.name.trim()) return "name must not be empty";
  for (const k of ["energy_mwh", "max_mw"] as const) {
    if (!Number.isFinite(bid[k])) return `${k} must be a number`;
    if (bid[k] < 0) return `${k} must be >= 0`;
  }
  if (!Number.isInteger(bid.earliest_hour) || bid.earliest_hour < 0 || bid.earliest_hour > HOURS - 1) {
    return `earliest_hour must be an integer 0-${HOURS - 1}`;
  }
  if (!Number.isInteger(bid.deadline_hour) || bid.deadline_hour < 1 || bid.deadline_hour > HOURS) {
    return `deadline_hour must be an integer 1-${HOURS}`;
  }
  return null;
}

export function bidError(bid: Bid): string | null {
  return bidShapeError(bid) ?? bidFeasibilityError(bid);
}

/**
 * Client-side mirror of the /api/evaluate schedule checks (design B6): energy met,
 * inside the window, never above max MW. Tolerance matches the backend's 0.1%
 * delivery check loosely; the backend is authoritative.
 */
export function scheduleErrors(bid: Bid, mw: number[]): string | null {
  const tol = Math.max(1e-6, bid.energy_mwh * 1e-3);
  for (let h = 0; h < HOURS; h++) {
    const v = mw[h] ?? 0;
    if (v < -1e-9) return `negative MW in hour ${h}`;
    if (v > 1e-9 && (h < bid.earliest_hour || h >= bid.deadline_hour)) {
      return `power scheduled in hour ${h}, outside the window [${bid.earliest_hour}, ${bid.deadline_hour})`;
    }
    if (v > bid.max_mw + 1e-6) {
      return `hour ${h} draws ${formatG(v)} MW, above max_mw ${formatG(bid.max_mw)}`;
    }
  }
  const total = mw.reduce((a, b) => a + b, 0);
  if (Math.abs(total - bid.energy_mwh) > tol) {
    return `schedule delivers ${formatG(total)} MWh but the bid needs ${formatG(bid.energy_mwh)} MWh`;
  }
  return null;
}
