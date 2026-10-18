"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  CSV_COLUMNS,
  CSV_TEMPLATE,
  draftError,
  draftToBid,
  loadTypeLabel,
  parseBidCsv,
  presetToDraft,
  sampleBid,
  type BidDraft,
  type CsvRow,
} from "@/lib/bids";
import { fmtInt, hh, windowLabel } from "@/lib/format";
import type { Preset } from "@/lib/types";
import { LoadTypeTag } from "./ui";

type Tab = "generate" | "csv";

/**
 * "Company data" dialog: build one company's bid (sliders, hour chips, preset quick-fill, random
 * sample) or upload several companies from a CSV. ``Join`` hands the drafts to the caller.
 * Editing an existing company shows only the Generate tab.
 */
export function CompanyModal({
  initial,
  editing,
  presets,
  loadTypes,
  existing,
  slotsLeft,
  onJoin,
  onClose,
}: {
  initial: BidDraft;
  editing: boolean;
  presets: Preset[];
  loadTypes: { value: string; label: string }[];
  existing: BidDraft[];
  slotsLeft: number;
  onJoin: (drafts: BidDraft[]) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<Tab>("generate");
  const [draft, setDraft] = useState<BidDraft>(initial);
  const [csv, setCsv] = useState<{ file: string; rows: CsvRow[]; error: string | null } | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    dialogRef.current?.querySelector<HTMLElement>("input,select,button")?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const set = (patch: Partial<BidDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const error = draftError(draft);
  const csvValid = csv?.rows.filter((r) => r.error === null && r.draft) ?? [];
  const csvAdd = csvValid.slice(0, slotsLeft);

  const join = () => {
    if (tab === "generate") {
      if (!error) onJoin([{ ...draft, name: draft.name.trim() }]);
    } else if (csvAdd.length) {
      onJoin(csvAdd.map((r) => r.draft as BidDraft));
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 p-0 sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        data-testid="company-modal"
        className="flex max-h-[92vh] w-full max-w-xl flex-col overflow-hidden rounded-t-2xl bg-surface shadow-xl sm:rounded-2xl"
      >
        <div className="overflow-y-auto px-5 pb-4 pt-5 sm:px-6">
          <div className="mb-4 flex items-start justify-between gap-3">
            <h2 id={titleId} className="text-xl font-semibold text-ink">
              {editing ? "Edit company" : "Company data"}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded px-2 py-0.5 text-lg leading-none text-muted hover:bg-surface-2 hover:text-ink"
            >
              ×
            </button>
          </div>

          {!editing && (
            <div className="mb-4 flex border-b border-line" role="tablist">
              {(
                [
                  ["generate", "Generate bid"],
                  ["csv", "Upload a CSV"],
                ] as const
              ).map(([v, label]) => (
                <button
                  key={v}
                  type="button"
                  role="tab"
                  aria-selected={tab === v}
                  data-testid={`modal-tab-${v}`}
                  onClick={() => setTab(v)}
                  className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
                    tab === v ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}

          {tab === "generate" ? (
            <GenerateTab draft={draft} set={set} error={error} presets={presets} loadTypes={loadTypes} existing={existing} />
          ) : (
            <CsvTab csv={csv} setCsv={setCsv} existing={existing} slotsLeft={slotsLeft} />
          )}
        </div>

        <button
          type="button"
          onClick={join}
          disabled={tab === "generate" ? !!error : csvAdd.length === 0}
          data-testid="modal-join"
          className="w-full bg-ink py-3.5 text-sm font-semibold uppercase tracking-wider text-surface hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {tab === "generate"
            ? editing
              ? "Save"
              : "Join"
            : csvAdd.length > 1
              ? `Join ${csvAdd.length} companies`
              : "Join"}
        </button>
      </div>
    </div>
  );
}

const SLIDER_ENERGY_MAX = 10_000; // MWh
const SLIDER_MW_MAX = 2_000; // MW

function GenerateTab({
  draft,
  set,
  error,
  presets,
  loadTypes,
  existing,
}: {
  draft: BidDraft;
  set: (patch: Partial<BidDraft>) => void;
  error: string | null;
  presets: Preset[];
  loadTypes: { value: string; label: string }[];
  existing: BidDraft[];
}) {
  const bid = draftToBid(draft);
  const windowH = Math.max(0, bid.deadline_hour - bid.earliest_hour);
  const capacity = Number.isFinite(bid.max_mw) ? bid.max_mw * windowH : Number.NaN;
  const use = capacity > 0 ? bid.energy_mwh / capacity : Number.NaN;
  const others = existing.filter((d) => d.id !== draft.id).map((d) => d.name);

  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)]">
        <Field label="Name">
          <input
            value={draft.name}
            onChange={(e) => set({ name: e.target.value })}
            data-testid="modal-name"
            className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink focus:border-accent focus:outline-none"
          />
        </Field>
        <Field label="Load type">
          <select
            value={draft.load_type}
            onChange={(e) => set({ load_type: e.target.value })}
            data-testid="modal-type"
            className="w-full rounded-lg border border-line-strong bg-surface px-3 py-2 text-[15px] text-ink"
          >
            {loadTypes.some((t) => t.value === draft.load_type) ? null : (
              <option value={draft.load_type}>{loadTypeLabel(draft.load_type)}</option>
            )}
            {loadTypes.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
      </div>

      {presets.length > 0 && (
        <div>
          <Kicker>Start from a preset</Kicker>
          <div className="flex flex-wrap gap-1.5">
            {presets.map((p) => (
              <button
                key={p.name}
                type="button"
                title={p.size_reference}
                data-testid={`modal-preset-${p.load_type}`}
                onClick={() => set(presetToDraft(p, draft.id, others))}
                className="rounded-md border border-line bg-surface px-2 py-1 text-xs text-ink-2 hover:border-line-strong hover:bg-surface-2"
              >
                {p.name}
              </button>
            ))}
          </div>
        </div>
      )}

      <Slider
        label="Energy needed"
        unit="MWh"
        value={draft.energy_mwh}
        max={SLIDER_ENERGY_MAX}
        step={10}
        onChange={(v) => set({ energy_mwh: v })}
        testId="modal-energy"
      />
      <Slider
        label="Max power"
        unit="MW"
        value={draft.max_mw}
        max={SLIDER_MW_MAX}
        step={5}
        onChange={(v) => set({ max_mw: v })}
        testId="modal-maxmw"
      />

      <HourChips earliest={draft.earliest_hour} deadline={draft.deadline_hour} onChange={(e, d) => set({ earliest_hour: e, deadline_hour: d })} />

      <div>
        <button
          type="button"
          onClick={() => set(sampleBid(draft.load_type))}
          data-testid="modal-sample"
          className="inline-flex items-center gap-2 text-sm font-medium text-accent hover:text-accent-ink"
        >
          <span aria-hidden className="grid h-4 w-4 place-items-center rounded-sm bg-accent text-[9px] text-white">
            ⚄
          </span>
          Draw a different sample
        </button>
        <p className="mt-1 text-xs text-muted">
          Fills a plausible random window and size for a <LoadTypeTag type={draft.load_type} /> bid. Ranges are
          illustrative, not drawn from data; energy is 30–80% of what the window allows, so the bid is
          always feasible. Adjust anything afterwards.
        </p>
      </div>

      <div className="rounded-lg bg-surface-2 px-3 py-2.5 text-sm">
        <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 text-ink-2">
          <span>
            Window <span className="num text-ink">{windowLabel(bid.earliest_hour, bid.deadline_hour)}</span> ·{" "}
            <span className="num">{windowH} h</span>
          </span>
          {Number.isFinite(use) && (
            <span>
              Needs <span className="num text-ink">{Math.round(use * 100)}%</span> of window capacity (
              <span className="num">{fmtInt(capacity)}</span> MWh)
            </span>
          )}
        </div>
        {error && (
          <p className="mt-1.5 text-xs font-medium text-danger" data-testid="modal-error">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function Slider({
  label,
  unit,
  value,
  max,
  step,
  onChange,
  testId,
}: {
  label: string;
  unit: string;
  value: string;
  max: number;
  step: number;
  onChange: (v: string) => void;
  testId: string;
}) {
  const n = Number(value);
  const id = useId();
  return (
    <div>
      <div className="mb-1.5 flex items-center justify-between gap-3">
        <label htmlFor={id} className="text-[11px] font-semibold uppercase tracking-wider text-muted">
          {label}
        </label>
        <span className="flex items-center gap-1">
          <input
            aria-label={`${label} (${unit})`}
            inputMode="decimal"
            value={value}
            onChange={(e) => onChange(e.target.value)}
            data-testid={`${testId}-input`}
            className="num w-24 rounded border border-line-strong bg-surface px-2 py-0.5 text-right text-sm text-ink"
          />
          <span className="text-xs text-muted">{unit}</span>
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={0}
        max={Math.max(max, Number.isFinite(n) ? n : 0)}
        step={step}
        value={Number.isFinite(n) ? n : 0}
        onChange={(e) => onChange(e.target.value)}
        data-testid={testId}
        className="w-full"
      />
    </div>
  );
}

/**
 * 24 hour chips. First click sets a one-hour window and an anchor; the second click extends the
 * window from the anchor to that hour (either direction). Chips inside the window are filled.
 */
function HourChips({
  earliest,
  deadline,
  onChange,
}: {
  earliest: number;
  deadline: number;
  onChange: (earliest: number, deadline: number) => void;
}) {
  const [anchor, setAnchor] = useState<number | null>(null);
  const click = (h: number) => {
    if (anchor === null) {
      setAnchor(h);
      onChange(h, h + 1);
    } else {
      onChange(Math.min(anchor, h), Math.max(anchor, h) + 1);
      setAnchor(null);
    }
  };
  return (
    <div>
      <Kicker>
        Allowed hours{" "}
        <span className="font-normal normal-case tracking-normal text-muted">
          {anchor === null ? "· click the first hour, then the last" : `· now click the last hour (from ${hh(anchor)})`}
        </span>
      </Kicker>
      <div className="grid grid-cols-8 gap-1.5 sm:grid-cols-12" data-testid="modal-hours">
        {Array.from({ length: 24 }, (_, h) => {
          const inside = h >= earliest && h < deadline;
          return (
            <button
              key={h}
              type="button"
              onClick={() => click(h)}
              aria-pressed={inside}
              data-testid={`modal-hour-${h}`}
              className={`num rounded-md border py-1.5 text-xs ${
                inside
                  ? "border-accent bg-accent text-white"
                  : h === anchor
                    ? "border-accent text-accent"
                    : "border-line text-ink-2 hover:border-line-strong hover:bg-surface-2"
              }`}
            >
              {hh(h)}
            </button>
          );
        })}
      </div>
      <p className="mt-1 text-xs text-muted">One day, midnight to midnight (CST); windows cannot cross midnight.</p>
    </div>
  );
}

function CsvTab({
  csv,
  setCsv,
  existing,
  slotsLeft,
}: {
  csv: { file: string; rows: CsvRow[]; error: string | null } | null;
  setCsv: (v: { file: string; rows: CsvRow[]; error: string | null } | null) => void;
  existing: BidDraft[];
  slotsLeft: number;
}) {
  const inputId = useId();
  const valid = csv?.rows.filter((r) => r.error === null).length ?? 0;
  const read = async (f: File) => {
    const parsed = parseBidCsv(await f.text(), existing);
    setCsv({ file: f.name, ...parsed });
  };
  return (
    <div className="space-y-4">
      <p className="text-sm text-ink-2">
        One row per company with columns <span className="num text-ink">{CSV_COLUMNS.join(", ")}</span>. Hours are
        0–24 (CST); the earliest hour is included, the deadline excluded. Every row is checked like a hand-entered bid.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <label
          htmlFor={inputId}
          className="cursor-pointer rounded-md border border-line-strong bg-surface px-3.5 py-2 text-sm font-medium text-ink hover:bg-surface-2"
        >
          Choose CSV file
        </label>
        <input
          id={inputId}
          type="file"
          accept=".csv,text/csv"
          className="sr-only"
          data-testid="modal-csv-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void read(f);
            e.target.value = "";
          }}
        />
        <a
          href={`data:text/csv;charset=utf-8,${encodeURIComponent(CSV_TEMPLATE)}`}
          download="companies_template.csv"
          className="text-sm font-medium text-accent hover:text-accent-ink"
        >
          Download template
        </a>
      </div>

      {csv && (
        <div className="rounded-lg border border-line">
          <div className="border-b border-line px-3 py-2 text-xs text-muted">
            <span className="text-ink-2">{csv.file}</span>
            {csv.error ? null : (
              <>
                {" "}
                · {valid} of {csv.rows.length} rows valid
                {valid > slotsLeft && ` · only ${slotsLeft} more companies fit`}
              </>
            )}
          </div>
          {csv.error ? (
            <p className="px-3 py-2 text-sm text-danger">{csv.error}</p>
          ) : (
            <ul className="max-h-56 divide-y divide-line overflow-y-auto text-sm" data-testid="modal-csv-rows">
              {csv.rows.map((r) => (
                <li key={r.line} className="px-3 py-1.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span className="num text-xs text-muted">row {r.line}</span>
                    <span className="font-medium text-ink">{r.draft?.name || "(no name)"}</span>
                    {r.draft && <LoadTypeTag type={r.draft.load_type} />}
                    {r.draft && r.error === null && (
                      <span className="num text-xs text-muted">
                        {r.draft.energy_mwh} MWh · {r.draft.max_mw} MW ·{" "}
                        {windowLabel(r.draft.earliest_hour, r.draft.deadline_hour)}
                      </span>
                    )}
                  </div>
                  {r.error && <p className="text-xs text-danger">{r.error} (skipped)</p>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-muted">{label}</span>
      {children}
    </label>
  );
}

function Kicker({ children }: { children: React.ReactNode }) {
  return <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{children}</div>;
}
