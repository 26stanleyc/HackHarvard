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

export const LOAD_TYPES: { value: string; label: string }[] = [
  { value: "ev_fleet", label: "EV fleet" },
  { value: "data_center", label: "Data center" },
  { value: "office", label: "Office building" },
  { value: "cold_storage", label: "Cold storage" },
  { value: "custom", label: "Custom" },
];

export function loadTypeLabel(v: string): string {
  return LOAD_TYPES.find((t) => t.value === v)?.label ?? v.replace(/_/g, " ");
}
