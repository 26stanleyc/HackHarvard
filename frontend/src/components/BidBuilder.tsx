"use client";

import { useId } from "react";
import { API_MODE, ApiError } from "@/lib/api";
import {
  LOAD_TYPES,
  customDraft,
  draftError,
  draftToBid,
  nextBidId,
  presetToDraft,
  type BidDraft,
} from "@/lib/bids";
import { fmtInt, fmtUsd, hh, windowLabel } from "@/lib/format";
import { companyColor } from "@/lib/theme";
import type { MetaResponse, Preset } from "@/lib/types";
import { Button, ErrorBanner, SectionTitle, Source } from "./ui";

export interface Scenario {
  day: string;
  background_pct: number;
  carbon_price: string;
  pool_usd: string;
}

export function scenarioErrors(s: Scenario): string[] {
  const out: string[] = [];
  const cp = Number(s.carbon_price);
  const pool = Number(s.pool_usd);
  if (s.carbon_price.trim() === "" || !Number.isFinite(cp) || cp < 0)
    out.push("Carbon price must be a number >= 0.");
  if (s.pool_usd.trim() === "" || !Number.isFinite(pool) || pool < 0)
    out.push("Payout pool must be a number >= 0.");
  return out;
}

export function BidBuilder({
  meta,
  presets,
  drafts,
  setDrafts,
  scenario,
  setScenario,
  onLoadExample,
  onRun,
  clearing,
  clearError,
  onDismissError,
}: {
  meta: MetaResponse;
  presets: Preset[];
  drafts: BidDraft[];
  setDrafts: (f: (prev: BidDraft[]) => BidDraft[]) => void;
  scenario: Scenario;
  setScenario: (f: (prev: Scenario) => Scenario) => void;
  onLoadExample: () => void;
  onRun: () => void;
  clearing: boolean;
  clearError: ApiError | null;
  onDismissError: () => void;
}) {
  const max = meta.max_bids;
  const full = drafts.length >= max;
  const errors = drafts.map(draftError);
  const sErr = scenarioErrors(scenario);
  const canRun = drafts.length > 0 && errors.every((e) => e === null) && sErr.length === 0 && !clearing;
  const serverErrByBid = new Map<string, string[]>();
  for (const e of clearError?.bidErrors ?? []) {
    if (e.bid_id) serverErrByBid.set(e.bid_id, [...(serverErrByBid.get(e.bid_id) ?? []), e.message]);
  }

  const addPreset = (p: Preset) =>
    setDrafts((prev) =>
      prev.length >= max ? prev : [...prev, presetToDraft(p, nextBidId(prev), prev.map((d) => d.name))],
    );
  const addCustom = () =>
    setDrafts((prev) => (prev.length >= max ? prev : [...prev, customDraft(nextBidId(prev))]));
  const update = (id: string, patch: Partial<BidDraft>) =>
    setDrafts((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  const remove = (id: string) => setDrafts((prev) => prev.filter((d) => d.id !== id));

  return (
    <div>
      <SectionTitle kicker="Step 2 · Bids" title="Submit flexibility bids">
        Each company states only how much energy it needs, its window and its maximum power,
        never its usage history. Bids are extra load on top of the real day&apos;s demand.
      </SectionTitle>

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-4">
          <div className="card p-4">
            <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
              <div className="text-sm font-medium text-ink">
                Companies <span className="num text-muted">{drafts.length}/{max}</span>
              </div>
              <Button variant="secondary" onClick={onLoadExample} testId="load-example">
                Load example scenario
              </Button>
            </div>
            <div className="flex flex-wrap gap-2">
              {presets.map((p) => (
                <button
                  key={p.name}
                  type="button"
                  disabled={full}
                  onClick={() => addPreset(p)}
                  data-testid={`add-preset-${p.load_type}`}
                  title={p.size_reference}
                  className="rounded-md border border-line bg-surface px-2.5 py-1.5 text-left text-xs text-ink-2 hover:border-line-strong hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <span className="font-medium text-ink">+ {p.name}</span>
                  <span className="num ml-1.5 text-muted">
                    {fmtInt(p.energy_mwh)} MWh · {fmtInt(p.max_mw)} MW
                  </span>
                </button>
              ))}
              <button
                type="button"
                disabled={full}
                onClick={addCustom}
                data-testid="add-custom"
                className="rounded-md border border-dashed border-line-strong px-2.5 py-1.5 text-xs font-medium text-ink-2 hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                + Custom company
              </button>
            </div>
            {full && (
              <p className="mt-2 text-xs text-muted">Maximum of {max} companies (exact Shapley settlement).</p>
            )}
            <Source className="mt-3">presets: GET /api/presets · sizes are reference values, see each preset&apos;s note</Source>
          </div>

          {drafts.length === 0 && (
            <div className="card border-dashed p-8 text-center text-sm text-muted">
              No companies yet. Add a preset above, or load the example scenario.
            </div>
          )}

          {drafts.map((d, i) => (
            <BidCard
              key={d.id}
              index={i}
              draft={d}
              error={errors[i]}
              serverErrors={serverErrByBid.get(d.id) ?? []}
              onChange={(patch) => update(d.id, patch)}
              onRemove={() => remove(d.id)}
            />
          ))}
        </div>

        <aside className="min-w-0 space-y-4 lg:sticky lg:top-28 lg:self-start">
          <ScenarioPanel meta={meta} scenario={scenario} setScenario={setScenario} />
          <div className="card space-y-3 p-4">
            {clearError && (
              <ErrorBanner
                title={
                  clearError.kind === "validation"
                    ? "Bids rejected"
                    : clearError.kind === "timeout"
                      ? "Clear timed out"
                      : "Could not run the clearinghouse"
                }
                message={`${clearError.message} Your inputs are kept.`}
                items={clearError.bidErrors.map((e) => ({
                  label: drafts.find((d) => d.id === e.bid_id)?.name ?? e.bid_id ?? "Request",
                  message: e.message,
                }))}
                onDismiss={onDismissError}
              />
            )}
            {sErr.map((e) => (
              <p key={e} className="text-xs text-danger">
                {e}
              </p>
            ))}
            <Button onClick={onRun} disabled={!canRun} testId="run-clear" className="w-full py-2.5">
              {clearing ? (
                <>
                  <Spinner /> Clearing {drafts.length} bids…
                </>
              ) : (
                "Run the clearinghouse"
              )}
            </Button>
            <p className="text-xs text-muted">
              Runs four strategies, the coordinator and the Shapley settlement in one request
              (POST /api/clear).
              {API_MODE === "fixture" &&
                " In fixture mode the recorded example response is shown, whatever you enter."}
            </p>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white"
    />
  );
}

function ScenarioPanel({
  meta,
  scenario,
  setScenario,
}: {
  meta: MetaResponse;
  scenario: Scenario;
  setScenario: (f: (prev: Scenario) => Scenario) => void;
}) {
  const id = useId();
  const day = meta.days.find((d) => d.date === scenario.day);
  return (
    <div className="card space-y-4 p-4">
      <div>
        <label htmlFor={`${id}-day`} className="mb-1 block text-xs font-medium uppercase tracking-wider text-muted">
          Real day
        </label>
        <select
          id={`${id}-day`}
          data-testid="day-picker"
          value={scenario.day}
          onChange={(e) => {
            const v = e.target.value;
            setScenario((s) => ({ ...s, day: v }));
          }}
          className="w-full rounded-md border border-line-strong bg-surface px-2.5 py-2 text-sm text-ink"
        >
          {meta.days.map((d) => (
            <option key={d.date} value={d.date}>
              {d.label} ({d.date})
            </option>
          ))}
        </select>
        {day && <p className="mt-1.5 text-xs leading-relaxed text-ink-2">{day.description}</p>}
        <p className="mt-1 text-[11px] text-muted">Days were chosen by weather type before any results were seen.</p>
      </div>

      <div>
        <div className="mb-1 flex items-baseline justify-between">
          <label htmlFor={`${id}-bg`} className="text-xs font-medium uppercase tracking-wider text-muted">
            Background flexible load
          </label>
          <span className="num text-sm text-ink">{scenario.background_pct.toFixed(1)}% of peak</span>
        </div>
        <input
          id={`${id}-bg`}
          data-testid="background-slider"
          type="range"
          min={0}
          max={30}
          step={0.5}
          value={scenario.background_pct}
          onChange={(e) => {
            const v = Number(e.target.value);
            setScenario((s) => ({ ...s, background_pct: v }));
          }}
          className="w-full"
        />
        <p className="mt-1 text-xs leading-relaxed text-ink-2">
          Everyone else in the region already following a standard carbon-aware tool. Fixed
          context, not part of the payout game.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <NumberField
          label="Carbon price"
          suffix="$/t"
          value={scenario.carbon_price}
          testId="carbon-price"
          onChange={(v) => setScenario((s) => ({ ...s, carbon_price: v }))}
        />
        <NumberField
          label="Payout pool"
          suffix="$"
          value={scenario.pool_usd}
          testId="pool-usd"
          onChange={(v) => setScenario((s) => ({ ...s, pool_usd: v }))}
        />
      </div>
      <Source>
        defaults from /api/meta: {meta.background_pct_default}% background, {fmtUsd(meta.carbon_price_default)}/t,{" "}
        {fmtUsd(meta.pool_default_usd)} pool
      </Source>
    </div>
  );
}

function NumberField({
  label,
  suffix,
  value,
  onChange,
  testId,
  invalid,
}: {
  label: string;
  suffix?: string;
  value: string;
  onChange: (v: string) => void;
  testId?: string;
  invalid?: boolean;
}) {
  const id = useId();
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-muted">
        {label}
      </label>
      <div
        className={`flex items-center rounded-md border bg-surface ${invalid ? "border-danger" : "border-line-strong"} focus-within:border-accent`}
      >
        <input
          id={id}
          data-testid={testId}
          inputMode="decimal"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="num w-full min-w-0 bg-transparent px-2.5 py-1.5 text-sm text-ink outline-none"
        />
        {suffix && <span className="pr-2.5 text-xs text-muted">{suffix}</span>}
      </div>
    </div>
  );
}

function HourSelect({
  label,
  value,
  options,
  onChange,
  testId,
}: {
  label: string;
  value: number;
  options: number[];
  onChange: (v: number) => void;
  testId?: string;
}) {
  const id = useId();
  return (
    <div className="min-w-0">
      <label htmlFor={id} className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-muted">
        {label}
      </label>
      <select
        id={id}
        data-testid={testId}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="num w-full rounded-md border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink"
      >
        {options.map((h) => (
          <option key={h} value={h}>
            {hh(h)}
          </option>
        ))}
      </select>
    </div>
  );
}

const EARLIEST = Array.from({ length: 24 }, (_, h) => h);
const DEADLINE = Array.from({ length: 24 }, (_, h) => h + 1);

function BidCard({
  index,
  draft,
  error,
  serverErrors,
  onChange,
  onRemove,
}: {
  index: number;
  draft: BidDraft;
  error: string | null;
  serverErrors: string[];
  onChange: (patch: Partial<BidDraft>) => void;
  onRemove: () => void;
}) {
  const bid = draftToBid(draft);
  const windowH = Math.max(0, bid.deadline_hour - bid.earliest_hour);
  const capacity = Number.isFinite(bid.max_mw) ? bid.max_mw * windowH : NaN;
  const use = Number.isFinite(capacity) && capacity > 0 ? bid.energy_mwh / capacity : NaN;
  return (
    <div
      className={`card p-4 ${error || serverErrors.length ? "border-danger/50" : ""}`}
      data-testid={`bid-card-${draft.id}`}
    >
      <div className="mb-3 flex items-center gap-2">
        <span className="h-3 w-3 shrink-0 rounded-sm" style={{ background: companyColor(index) }} aria-hidden />
        <input
          aria-label="Company name"
          value={draft.name}
          onChange={(e) => onChange({ name: e.target.value })}
          className="min-w-0 flex-1 rounded border border-transparent bg-transparent px-1 py-0.5 text-[15px] font-medium text-ink hover:border-line focus:border-accent focus:outline-none"
        />
        <span className="num hidden text-xs text-muted sm:inline">{draft.id}</span>
        <button
          type="button"
          onClick={onRemove}
          className="rounded px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-danger"
          aria-label={`Remove ${draft.name}`}
        >
          Remove
        </button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <div className="col-span-2 min-w-0 sm:col-span-1">
          <label className="mb-1 block text-[11px] font-medium uppercase tracking-wider text-muted" htmlFor={`${draft.id}-type`}>
            Load type
          </label>
          <select
            id={`${draft.id}-type`}
            value={draft.load_type}
            onChange={(e) => onChange({ load_type: e.target.value })}
            className="w-full rounded-md border border-line-strong bg-surface px-2 py-1.5 text-sm text-ink"
          >
            {LOAD_TYPES.some((t) => t.value === draft.load_type) ? null : (
              <option value={draft.load_type}>{draft.load_type}</option>
            )}
            {LOAD_TYPES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>
        <NumberField label="Energy" suffix="MWh" value={draft.energy_mwh} onChange={(v) => onChange({ energy_mwh: v })} testId={`energy-${draft.id}`} invalid={!!error} />
        <NumberField label="Max power" suffix="MW" value={draft.max_mw} onChange={(v) => onChange({ max_mw: v })} testId={`maxmw-${draft.id}`} invalid={!!error} />
        <HourSelect label="Earliest (incl.)" value={draft.earliest_hour} options={EARLIEST} onChange={(v) => onChange({ earliest_hour: v })} testId={`earliest-${draft.id}`} />
        <HourSelect label="Deadline (excl.)" value={draft.deadline_hour} options={DEADLINE} onChange={(v) => onChange({ deadline_hour: v })} testId={`deadline-${draft.id}`} />
      </div>

      <WindowStrip earliest={bid.earliest_hour} deadline={bid.deadline_hour} color={companyColor(index)} />
      <div className="mt-1.5 flex flex-wrap justify-between gap-x-4 gap-y-1 text-xs text-muted">
        <span>
          Window <span className="num text-ink-2">{windowLabel(bid.earliest_hour, bid.deadline_hour)}</span> ·{" "}
          <span className="num">{windowH} h</span>
        </span>
        {Number.isFinite(use) && (
          <span>
            Needs <span className="num text-ink-2">{Math.round(use * 100)}%</span> of window capacity (
            <span className="num">{fmtInt(capacity)}</span> MWh)
          </span>
        )}
      </div>
      {draft.size_reference && <p className="mt-1.5 text-[11px] text-muted">{draft.size_reference}</p>}
      {error && (
        <p className="mt-2 text-xs font-medium text-danger" data-testid={`bid-error-${draft.id}`}>
          {error}
        </p>
      )}
      {serverErrors.map((m) => (
        <p key={m} className="mt-2 text-xs font-medium text-danger">
          Backend: {m}
        </p>
      ))}
    </div>
  );
}

function WindowStrip({ earliest, deadline, color }: { earliest: number; deadline: number; color: string }) {
  return (
    <div className="mt-3" aria-hidden>
      <div className="grid h-2.5 grid-cols-24 gap-px overflow-hidden rounded-sm" style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}>
        {Array.from({ length: 24 }, (_, h) => {
          const inside = h >= earliest && h < deadline;
          return <div key={h} className={inside ? "" : "hatch bg-surface-2"} style={inside ? { background: color, opacity: 0.55 } : undefined} />;
        })}
      </div>
      <div className="num mt-0.5 flex justify-between text-[10px] text-muted">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>24</span>
      </div>
    </div>
  );
}
