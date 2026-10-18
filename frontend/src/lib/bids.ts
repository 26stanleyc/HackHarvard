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

/** A preset at k times its energy and max MW (the example companies' scale), noted in its size reference. */
export function scalePreset(p: Preset, k: number): Preset {
  if (k === 1) return p;
  return {
    ...p,
    energy_mwh: p.energy_mwh * k,
    max_mw: p.max_mw * k,
    size_reference: `${p.size_reference} Scaled ${k}× from this size.`,
  };
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

/**
 * "Draw a different sample": a plausible random bid for a load type. Windows and sizes are
 * illustrative ranges per type (not data); energy = 30-80% of max_mw x window (cold storage
 * 15-35%), the same rule as the backend's synthetic population, so every sample is feasible.
 */
const SAMPLE_PROFILES: Record<
  string,
  { start: [number, number]; end: [number, number]; mw: [number, number]; fill?: [number, number] }
> = {
  ev_fleet: { start: [0, 2], end: [5, 8], mw: [100, 800] },
  data_center: { start: [0, 0], end: [24, 24], mw: [50, 500] },
  office: { start: [5, 8], end: [16, 19], mw: [30, 300] },
  cold_storage: { start: [0, 0], end: [24, 24], mw: [20, 150], fill: [0.15, 0.35] },
  water_pumping: { start: [0, 2], end: [12, 18], mw: [30, 200] },
};

function uniform(lo: number, hi: number, rand: () => number): number {
  return lo + (hi - lo) * rand();
}

function intBetween(lo: number, hi: number, rand: () => number): number {
  return Math.floor(uniform(lo, hi + 1, rand));
}

function roundNice(v: number): number {
  const step = v >= 1000 ? 50 : v >= 100 ? 10 : 5;
  return Math.max(step, Math.round(v / step) * step);
}

export function sampleBid(
  loadType: string,
  rand: () => number = Math.random,
): Pick<BidDraft, "energy_mwh" | "earliest_hour" | "deadline_hour" | "max_mw"> {
  const p = SAMPLE_PROFILES[loadType];
  let earliest: number;
  let deadline: number;
  if (p) {
    earliest = intBetween(p.start[0], p.start[1], rand);
    deadline = intBetween(p.end[0], p.end[1], rand);
  } else {
    const len = intBetween(3, 16, rand);
    earliest = intBetween(0, 24 - len, rand);
    deadline = earliest + len;
  }
  const maxMw = roundNice(uniform(...(p?.mw ?? [20, 500]), rand));
  const fill = uniform(...(p?.fill ?? [0.3, 0.8]), rand);
  const energy = Math.min(roundNice(fill * maxMw * (deadline - earliest)), maxMw * (deadline - earliest));
  return { energy_mwh: String(energy), earliest_hour: earliest, deadline_hour: deadline, max_mw: String(maxMw) };
}

/** Columns of an uploaded bid CSV (one row per company), in template order. */
export const CSV_COLUMNS = ["name", "load_type", "energy_mwh", "earliest_hour", "deadline_hour", "max_mw"] as const;

export const CSV_TEMPLATE =
  `${CSV_COLUMNS.join(",")}\n` +
  "Northside EV depot,ev_fleet,1200,0,7,300\n" +
  "Lakeview data center,data_center,2400,0,24,150\n" +
  "Downtown offices,office,600,6,18,90\n";

/** Split one CSV line, honoring double-quoted fields ("a, b" and "" escapes). */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cur += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      out.push(cur);
      cur = "";
    } else cur += c;
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

export interface CsvRow {
  line: number;
  draft: BidDraft | null;
  error: string | null;
}

/**
 * Parse an uploaded bid CSV. Needs a header with every column in ``CSV_COLUMNS`` (any order,
 * case-insensitive). Each row becomes a draft (ids assigned from ``existing``) and is checked
 * with the same rules as a hand-entered bid.
 */
export function parseBidCsv(text: string, existing: { id: string }[]): { rows: CsvRow[]; error: string | null } {
  const lines = text.replace(/^﻿/, "").split(/\r?\n/).filter((l) => l.trim() !== "");
  if (lines.length === 0) return { rows: [], error: "The file is empty." };
  const header = splitCsvLine(lines[0]).map((h) => h.toLowerCase());
  const missing = CSV_COLUMNS.filter((c) => !header.includes(c));
  if (missing.length) return { rows: [], error: `Missing column(s): ${missing.join(", ")}.` };
  const col = Object.fromEntries(CSV_COLUMNS.map((c) => [c, header.indexOf(c)])) as Record<
    (typeof CSV_COLUMNS)[number],
    number
  >;
  const taken = [...existing];
  const rows: CsvRow[] = lines.slice(1).map((line, i) => {
    const f = splitCsvLine(line);
    const hour = (s: string | undefined) => (s !== undefined && /^\d+$/.test(s) ? Number(s) : Number.NaN);
    const id = nextBidId(taken);
    taken.push({ id });
    const draft: BidDraft = {
      id,
      name: f[col.name] ?? "",
      load_type: (f[col.load_type] || "custom").toLowerCase().replace(/\s+/g, "_"),
      energy_mwh: f[col.energy_mwh] ?? "",
      earliest_hour: hour(f[col.earliest_hour]),
      deadline_hour: hour(f[col.deadline_hour]),
      max_mw: f[col.max_mw] ?? "",
    };
    return { line: i + 2, draft, error: draftError(draft) };
  });
  if (rows.length === 0) return { rows, error: "No company rows below the header." };
  return { rows, error: null };
}
