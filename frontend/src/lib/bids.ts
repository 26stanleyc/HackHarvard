import { bidError } from "./feasibility";
import type { Bid, Preset } from "./types";

/** Editable bid: numeric fields kept as strings so partial input ("1.") survives. */
export interface BidDraft {
  id: string;
  name: string;
  load_type: string;
  energy_mwh: string;
  earliest_hour: number;
  deadline_hour: number;
  max_mw: string;
  size_reference?: string;
}

function num(s: string): number {
  const t = s.trim();
  return t === "" ? Number.NaN : Number(t);
}

export function draftToBid(d: BidDraft): Bid {
  return {
    id: d.id,
    name: d.name.trim(),
    load_type: d.load_type,
    energy_mwh: num(d.energy_mwh),
    earliest_hour: d.earliest_hour,
    deadline_hour: d.deadline_hour,
    max_mw: num(d.max_mw),
  };
}

export function bidToDraft(b: Bid, size_reference?: string): BidDraft {
  return {
    id: b.id,
    name: b.name,
    load_type: b.load_type,
    energy_mwh: String(b.energy_mwh),
    earliest_hour: b.earliest_hour,
    deadline_hour: b.deadline_hour,
    max_mw: String(b.max_mw),
    size_reference,
  };
}

export function draftError(d: BidDraft): string | null {
  return bidError(draftToBid(d));
}

/** Next free id of the form b1..bN. */
export function nextBidId(existing: { id: string }[]): string {
  const used = new Set(existing.map((b) => b.id));
  for (let i = 1; ; i++) if (!used.has(`b${i}`)) return `b${i}`;
}

export function presetToDraft(p: Preset, id: string, existingNames: string[]): BidDraft {
  let name = p.name;
  let n = 2;
  while (existingNames.includes(name)) name = `${p.name} ${n++}`;
  return {
    id,
    name,
    load_type: p.load_type,
    energy_mwh: String(p.energy_mwh),
    earliest_hour: p.earliest_hour,
    deadline_hour: p.deadline_hour,
    max_mw: String(p.max_mw),
    size_reference: p.size_reference,
  };
}

export function customDraft(id: string): BidDraft {
  return {
    id,
    name: `Custom company ${id.replace(/^b/, "")}`,
    load_type: "custom",
    energy_mwh: "400",
    earliest_hour: 0,
    deadline_hour: 24,
    max_mw: "50",
  };
}

/**
 * Known load types: label plus a muted tag color (used for the small load-type tag only;
 * company identity is always the company color). Types the backend adds later (e.g. from
 * /api/presets) fall back to a title-cased label and a neutral tag.
 */
export const LOAD_TYPE_META: Record<string, { label: string; color: string }> = {
  ev_fleet: { label: "EV fleet", color: "#3d6fa8" },
  data_center: { label: "Data center", color: "#6a5aa6" },
  office: { label: "Office building", color: "#a0663a" },
  cold_storage: { label: "Cold storage", color: "#2f8a8a" },
  water_pumping: { label: "Water pumping", color: "#3f7f4f" },
  custom: { label: "Custom", color: "#7d7c76" },
};

const NEUTRAL_TAG = "#7d7c76";

function titleCaseType(v: string): string {
  const s = v.replace(/_/g, " ").trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : "Unspecified";
}

export function loadTypeLabel(v: string): string {
  return LOAD_TYPE_META[v]?.label ?? titleCaseType(v);
}

export function loadTypeColor(v: string): string {
  return LOAD_TYPE_META[v]?.color ?? NEUTRAL_TAG;
}

/** Static load types (kept for callers that do not have the presets). */
export const LOAD_TYPES: { value: string; label: string }[] = Object.entries(LOAD_TYPE_META).map(
  ([value, m]) => ({ value, label: m.label }),
);

/**
 * Load-type options for the bid form: every known type plus any type that appears in the
 * presets served by /api/presets, "custom" last.
 */
export function loadTypeOptions(presets: { load_type: string }[]): { value: string; label: string }[] {
  const values = new Set<string>(Object.keys(LOAD_TYPE_META));
  for (const p of presets) values.add(p.load_type);
  values.delete("custom");
  return [...values, "custom"].map((value) => ({ value, label: loadTypeLabel(value) }));
}
