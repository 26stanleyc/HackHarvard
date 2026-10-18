"use client";

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { API_MODE, ApiError, postBidFeatures } from "@/lib/api";
import {
  customDraft,
  loadTypeLabel,
  loadTypeOptions,
  draftError,
  draftToBid,
  nextBidId,
  presetToDraft,
  type BidDraft,
} from "@/lib/bids";
import { fmtInt, fmtPct, hh, windowLabel } from "@/lib/format";
import { companyColor } from "@/lib/theme";
import {
  SHAPLEY_MAX_BIDS_DEFAULT,
  type Bid,
  type BidFeatures,
  type BidFeaturesBlock,
  type MetaResponse,
  type Preset,
} from "@/lib/types";
import { Button, ErrorBanner, Source } from "./ui";

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

const PROFILE_DEBOUNCE_MS = 400;

/**
 * Bid profiles from POST /api/bid-features for the bids that pass local checks, refetched
 * (debounced) when the day or a bid changes. Served by Chalk when the backend has it configured.
 */
function useBidProfiles(day: string, bids: Bid[]) {
  const [block, setBlock] = useState<BidFeaturesBlock | null>(null);
  const [failed, setFailed] = useState(false);
  const seq = useRef(0);
  const key = JSON.stringify([day, bids]);
  useEffect(() => {
    if (API_MODE === "fixture") return;
    const n = ++seq.current;
    const [d, b] = JSON.parse(key) as [string, Bid[]];
    if (b.length === 0) return;
    const t = setTimeout(() => {
      postBidFeatures(d, b)
        .then((r) => {
          if (n !== seq.current) return; // a newer request superseded this one
          setBlock(r);
          setFailed(false);
        })
        .catch(() => {
          if (n === seq.current) setFailed(true);
        });
    }, PROFILE_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [key]);
  return { block, failed };
}

/**
 * Results sidebar: run button, the company list with Edit links, "Add a company" (opens the
 * editor modal) and the day / price settings. Edits only take effect on the next run.
 */
export function CompanySidebar({
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
  dirty,
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
  /** the companies or settings changed since the last run */
  dirty: boolean;
}) {
  const max = meta.max_bids;
  const shapleyMax = meta.shapley_max_bids ?? SHAPLEY_MAX_BIDS_DEFAULT;
  const full = drafts.length >= max;
  const errors = drafts.map(draftError);
  const sErr = scenarioErrors(scenario);
  const canRun = drafts.length > 0 && errors.every((e) => e === null) && sErr.length === 0 && !clearing;
  const validBids = drafts.filter((_, i) => errors[i] === null).map(draftToBid);
  const profiles = useBidProfiles(scenario.day, validBids);
  const serverErrByBid = new Map<string, string[]>();
  for (const e of clearError?.bidErrors ?? []) {
    if (e.bid_id) serverErrByBid.set(e.bid_id, [...(serverErrByBid.get(e.bid_id) ?? []), e.message]);
  }
  // null = closed, "add" = preset picker, otherwise the id of the company being edited
  const [modal, setModal] = useState<string | null>(null);

  const addPreset = (p: Preset) => {
    const id = nextBidId(drafts);
    setDrafts((prev) => [...prev, presetToDraft(p, id, prev.map((d) => d.name))]);
    setModal(id);
  };
  const addCustom = () => {
    const id = nextBidId(drafts);
    setDrafts((prev) => [...prev, customDraft(id)]);
    setModal(id);
  };
  const update = (id: string, patch: Partial<BidDraft>) =>
    setDrafts((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)));
  const remove = (id: string) => {
    setDrafts((prev) => prev.filter((d) => d.id !== id));
    setModal(null);
  };
  const loadTypes = loadTypeOptions(presets);
  const editIdx = drafts.findIndex((d) => d.id === modal);
  const day = meta.days.find((d) => d.date === scenario.day);

  return (
    <div className="space-y-4" data-testid="company-sidebar">
      <div className="space-y-2">
        {clearError && (
          <ErrorBanner
            title={clearError.kind === "validation" ? "Bids rejected" : "Could not run the clearinghouse"}
            message={clearError.message}
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
        <button
          type="button"
          onClick={onRun}
          disabled={!canRun}
          data-testid="run-clear"
          className={`w-full rounded-xl px-4 py-3.5 text-left text-white shadow-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
            dirty ? "bg-accent hover:bg-accent-ink" : "bg-accent/80 hover:bg-accent"
          }`}
        >
          <div className="flex items-center gap-2 text-[17px] font-semibold">
            {clearing ? (
              <>
                <Spinner /> Running…
              </>
            ) : (
              <>
                {dirty ? "Run with changes" : "Run again"} <span aria-hidden>→</span>
              </>
            )}
          </div>
          <div className="mt-0.5 text-xs text-white/80">
            {drafts.length} {drafts.length === 1 ? "company" : "companies"} · {day?.label ?? scenario.day} ·{" "}
            ${scenario.carbon_price}/t
          </div>
        </button>
      </div>

      <div>
        <div className="mb-2 flex items-baseline justify-between">
          <div className="kicker">Companies</div>
          <button type="button" onClick={onLoadExample} className="text-[11px] text-muted hover:text-ink" data-testid="load-example">
            Reset to example
          </button>
        </div>
        <ul className="space-y-2">
          {drafts.map((d, i) => {
            const bid = draftToBid(d);
            const bad = errors[i] !== null || serverErrByBid.has(d.id);
            return (
              <li key={d.id}>
                <button
                  type="button"
                  onClick={() => setModal(d.id)}
                  data-testid={`company-row-${d.id}`}
                  className={`flex w-full items-start gap-2.5 rounded-lg border bg-surface px-3 py-2.5 text-left hover:border-line-strong ${
                    bad ? "border-danger/50" : "border-line"
                  }`}
                >
                  <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: companyColor(i) }} aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-ink">{d.name || "Unnamed company"}</span>
                    <span className="num block text-[11px] text-muted">
                      {bad
                        ? "needs a fix"
                        : `${fmtInt(bid.energy_mwh)} MWh · ${fmtInt(bid.max_mw)} MW · ${windowLabel(bid.earliest_hour, bid.deadline_hour)}`}
                    </span>
                  </span>
                  <span className="shrink-0 text-xs text-accent">Edit</span>
                </button>
              </li>
            );
          })}
        </ul>
        <button
          type="button"
          disabled={full}
          onClick={() => setModal("add")}
          data-testid="add-company"
          className="mt-2 w-full rounded-lg border border-dashed border-line-strong px-3 py-2.5 text-sm font-medium text-ink-2 hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50"
        >
          + Add a company
        </button>
        {drafts.length > shapleyMax && (
          <p className="mt-2 text-xs text-muted" data-testid="shapley-limit-hint">
            Payouts are computed for up to {shapleyMax} companies.
          </p>
        )}
      </div>

      <ScenarioPanel meta={meta} scenario={scenario} setScenario={setScenario} />

      {modal === "add" && (
        <Modal title="Add a company" onClose={() => setModal(null)}>
          <p className="mb-3 text-sm text-ink-2">Start from a typical company, then adjust it.</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {presets.map((p) => (
              <button
                key={p.name}
                type="button"
                onClick={() => addPreset(p)}
                data-testid={`add-preset-${p.load_type}`}
                className="rounded-lg border border-line bg-surface px-3 py-2.5 text-left text-xs hover:border-line-strong hover:bg-surface-2"
              >
                <span className="flex items-center gap-1.5">
                  <span className="font-medium text-ink">{p.name}</span>
                </span>
                <span className="num mt-0.5 block text-muted">
                  {fmtInt(p.energy_mwh)} MWh · {fmtInt(p.max_mw)} MW · {windowLabel(p.earliest_hour, p.deadline_hour)}
                </span>
              </button>
            ))}
            <button
              type="button"
              onClick={addCustom}
              data-testid="add-custom"
              className="rounded-lg border border-dashed border-line-strong px-3 py-2.5 text-left text-xs font-medium text-ink-2 hover:bg-surface-2"
            >
              Custom company
            </button>
          </div>
        </Modal>
      )}

      {editIdx >= 0 && (
        <Modal title="Company bid" onClose={() => setModal(null)}>
          <BidCard
            index={editIdx}
            draft={drafts[editIdx]}
            error={errors[editIdx]}
            serverErrors={serverErrByBid.get(drafts[editIdx].id) ?? []}
            loadTypes={loadTypes}
            profile={errors[editIdx] === null ? (profiles.block?.per_bid[drafts[editIdx].id] ?? null) : null}
            onChange={(patch) => update(drafts[editIdx].id, patch)}
            onRemove={() => remove(drafts[editIdx].id)}
          />
          <div className="mt-4 flex items-center justify-between gap-3">
            {API_MODE !== "fixture" ? <ProfileSourceNote block={profiles.block} failed={profiles.failed} /> : <span />}
            <Button onClick={() => setModal(null)} testId="company-done">
              Done
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  // portal: the sticky sidebar is its own stacking context, which would trap the overlay under the header
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-ink/40 p-4 pt-[12vh] backdrop-blur-[2px]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-label={title} className="w-full max-w-2xl rounded-2xl bg-paper p-5 shadow-2xl sm:p-6">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="font-serif text-2xl font-semibold text-ink">{title}</h3>
          <button type="button" onClick={onClose} aria-label="Close" className="rounded px-2 text-xl text-muted hover:text-ink">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
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
          Everyone else in the region, already using a standard carbon-aware tool.
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
  loadTypes,
  profile,
  onChange,
  onRemove,
}: {
  index: number;
  draft: BidDraft;
  error: string | null;
  serverErrors: string[];
  loadTypes: { value: string; label: string }[];
  profile: BidFeatures | null;
  onChange: (patch: Partial<BidDraft>) => void;
  onRemove: () => void;
}) {
  const bid = draftToBid(draft);
  const windowH = Math.max(0, bid.deadline_hour - bid.earliest_hour);
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
            {loadTypes.some((t) => t.value === draft.load_type) ? null : (
              <option value={draft.load_type}>{loadTypeLabel(draft.load_type)}</option>
            )}
            {loadTypes.map((t) => (
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
      </div>
      {profile && <BidProfile f={profile} />}
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

/** What the day's forecast says about this bid's window (served bid features). */
function BidProfile({ f }: { f: BidFeatures }) {
  const items: { label: string; value: string; title: string }[] = [];
  if (f.window_mean_renewable_share !== null)
    items.push({
      label: "Wind + solar in window",
      value: fmtPct(f.window_mean_renewable_share, 0),
      title: "Forecast mean (wind + solar) / demand over the bid's window",
    });
  if (f.window_curtailed_mwh !== null)
    items.push({
      label: "Spare clean energy",
      value: `${fmtInt(f.window_curtailed_mwh)} MWh`,
      title: "Forecast curtailed wind + solar in the window: extra load here is absorbed with no fossil ramp",
    });
  if (items.length === 0) return null;
  return (
    <dl className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md bg-surface-2 px-3 py-2 " data-testid="bid-profile">
      {items.map((it) => (
        <div key={it.label} className="min-w-0" title={it.title}>
          <dt className="truncate text-[10px] font-medium uppercase tracking-wider text-muted">{it.label}</dt>
          <dd className="num text-sm text-ink">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function ProfileSourceNote({ block, failed }: { block: BidFeaturesBlock | null; failed: boolean }) {
  if (failed && !block) return <Source>bid profiles unavailable (POST /api/bid-features failed)</Source>;
  if (!block) return <Source>bid profiles: loading…</Source>;
  const src = block.source === "chalk" ? "Chalk" : "local model";
  const ms = block.latency_ms < 10 ? block.latency_ms.toFixed(1) : fmtInt(block.latency_ms);
  return (
    <Source>
      bid profiles: {src} · {ms} ms
      {failed ? " · last refresh failed, showing previous" : ""}
    </Source>
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
