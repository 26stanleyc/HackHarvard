"use client";

import { useEffect, useId, useRef, useState } from "react";
import { API_MODE, ApiError, postBidFeatures } from "@/lib/api";
import {
  customDraft,
  loadTypeLabel,
  loadTypeOptions,
  draftError,
  draftToBid,
  nextBidId,
  sampleBid,
  type BidDraft,
} from "@/lib/bids";
import { fmtInt, fmtPct, fmtUsd, hh, windowLabel } from "@/lib/format";
import { companyColor } from "@/lib/theme";
import {
  SHAPLEY_MAX_BIDS_DEFAULT,
  type Bid,
  type BidFeatures,
  type BidFeaturesBlock,
  type MetaResponse,
  type Preset,
} from "@/lib/types";
import { suggestionPatch, type Suggestion } from "@/lib/assistant";
import { AssistantPanel, type HelpRequest } from "./AssistantPanel";
import { CompanyModal } from "./CompanyModal";
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

  const [modal, setModal] = useState<{ initial: BidDraft; editing: boolean } | null>(null);
  const openNew = () => {
    const d = customDraft(nextBidId(drafts));
    setModal({ initial: { ...d, name: `Company ${d.id.replace(/^b/, "")}`, ...sampleBid(d.load_type) }, editing: false });
  };
  const join = (added: BidDraft[]) => {
    if (modal?.editing) {
      const [d] = added;
      setDrafts((prev) => prev.map((x) => (x.id === d.id ? d : x)));
    } else {
      setDrafts((prev) => [...prev, ...added].slice(0, max));
    }
    setModal(null);
  };
  const update = (id: string, patch: Partial<BidDraft>) =>
    setDrafts((prev) => prev.map((d) => (d.id === id ? { ...d, ...patch } : d)));

  // Coordinator Guide: field help requests, and fields last set by an applied suggestion.
  const [help, setHelp] = useState<HelpRequest | null>(null);
  const [suggested, setSuggested] = useState<Record<string, string[]>>({});
  const editByUser = (id: string, patch: Partial<BidDraft>) => {
    update(id, patch);
    const keys = Object.keys(patch);
    setSuggested((prev) => (prev[id] ? { ...prev, [id]: prev[id].filter((f) => !keys.includes(f)) } : prev));
  };
  const applySuggestion = (sg: Suggestion) => {
    update(sg.bid_id, suggestionPatch(sg));
    setSuggested((prev) => ({ ...prev, [sg.bid_id]: [...(prev[sg.bid_id] ?? []).filter((f) => f !== sg.field), sg.field] }));
  };
  useEffect(() => {
    if (!help) return;
    const t = setTimeout(() => setHelp((h) => (h && h.nonce === help.nonce ? { ...h, field: "", bidId: "" } : h)), 2500);
    return () => clearTimeout(t);
  }, [help]);
  const dayLabel = meta.days.find((d) => d.date === scenario.day)?.label ?? scenario.day;
  const assistantScenario = {
    carbon_price: Number.isFinite(Number(scenario.carbon_price)) && scenario.carbon_price.trim() !== "" ? Number(scenario.carbon_price) : null,
    pool_usd: Number.isFinite(Number(scenario.pool_usd)) && scenario.pool_usd.trim() !== "" ? Number(scenario.pool_usd) : null,
    background_pct: scenario.background_pct,
  };
  const remove = (id: string) => setDrafts((prev) => prev.filter((d) => d.id !== id));
  const loadTypes = loadTypeOptions(presets);

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
            <button
              type="button"
              disabled={full}
              onClick={openNew}
              data-testid="add-company"
              className="flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-line-strong px-4 py-4 text-sm font-medium text-ink hover:border-accent hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span aria-hidden className="text-lg leading-none text-accent">+</span> Add company
              <span className="hidden font-normal text-muted sm:inline">· build a bid, start from a preset, or upload a CSV</span>
            </button>
            {full && <p className="mt-2 text-xs text-muted">Maximum of {max} companies.</p>}
            {drafts.length > shapleyMax && (
              <p className="mt-2 text-xs text-muted" data-testid="shapley-limit-hint">
                More than {shapleyMax} companies: results show CO2 for every strategy, but no Shapley
                payouts (those are computed for up to {shapleyMax} companies).
              </p>
            )}
            <Source className="mt-3">presets (inside Add company): GET /api/presets · sizes are reference values, see each preset&apos;s note</Source>
          </div>

          {drafts.length === 0 && (
            <div className="card border-dashed p-8 text-center text-sm text-muted">
              No companies yet. Add a company above, or load the example scenario.
            </div>
          )}

          {drafts.length > 0 && API_MODE !== "fixture" && (
            <ProfileSourceNote block={profiles.block} failed={profiles.failed} />
          )}

          {drafts.map((d, i) => (
            <BidCard
              key={d.id}
              index={i}
              draft={d}
              error={errors[i]}
              serverErrors={serverErrByBid.get(d.id) ?? []}
              loadTypes={loadTypes}
              profile={errors[i] === null ? (profiles.block?.per_bid[d.id] ?? null) : null}
              onChange={(patch) => editByUser(d.id, patch)}
              suggested={suggested[d.id] ?? []}
              highlight={help && help.bidId === d.id ? help.field : null}
              onHelp={(field) => setHelp({ field, bidId: d.id, nonce: Date.now() })}
              onEdit={() => setModal({ initial: d, editing: true })}
              onRemove={() => remove(d.id)}
            />
          ))}

          <AssistantPanel
            day={scenario.day}
            dayLabel={dayLabel}
            drafts={drafts}
            scenario={assistantScenario}
            onApply={applySuggestion}
            help={help && help.field ? help : null}
          />

          {modal && (
            <CompanyModal
              initial={modal.initial}
              editing={modal.editing}
              presets={presets}
              loadTypes={loadTypes}
              existing={drafts}
              slotsLeft={max - drafts.length}
              onJoin={join}
              onClose={() => setModal(null)}
            />
          )}
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
              Runs four strategies, the coordinator and the Shapley settlement (up to {shapleyMax}{" "}
              companies) in one request (POST /api/clear).
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
  onHelp,
  highlight,
  suggested,
}: {
  label: string;
  suffix?: string;
  value: string;
  onChange: (v: string) => void;
  testId?: string;
  invalid?: boolean;
  onHelp?: () => void;
  highlight?: boolean;
  suggested?: boolean;
}) {
  const id = useId();
  return (
    <div className={`min-w-0 rounded-md ${highlight ? "ring-2 ring-accent ring-offset-2 ring-offset-surface" : ""}`}>
      <FieldLabel htmlFor={id} label={label} onHelp={onHelp} />
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
      {suggested && <SuggestedTag />}
    </div>
  );
}

function FieldLabel({ htmlFor, label, onHelp }: { htmlFor: string; label: string; onHelp?: () => void }) {
  return (
    <div className="mb-1 flex items-center gap-1">
      <label htmlFor={htmlFor} className="block text-[11px] font-medium uppercase tracking-wider text-muted">
        {label}
      </label>
      {onHelp && (
        <button
          type="button"
          onClick={onHelp}
          aria-label={`Ask Coordinator Guide about ${label}`}
          title="Ask Coordinator Guide"
          className="flex h-3.5 w-3.5 items-center justify-center rounded-full border border-line-strong text-[9px] font-semibold leading-none text-muted hover:border-accent hover:text-accent"
        >
          ?
        </button>
      )}
    </div>
  );
}

function SuggestedTag() {
  return (
    <p className="mt-0.5 text-[10px] font-medium text-accent" data-testid="suggested-tag">
      suggested by Coordinator Guide
    </p>
  );
}

function HourSelect({
  label,
  value,
  options,
  onChange,
  testId,
  onHelp,
  highlight,
  suggested,
}: {
  label: string;
  value: number;
  options: number[];
  onChange: (v: number) => void;
  testId?: string;
  onHelp?: () => void;
  highlight?: boolean;
  suggested?: boolean;
}) {
  const id = useId();
  return (
    <div className={`min-w-0 rounded-md ${highlight ? "ring-2 ring-accent ring-offset-2 ring-offset-surface" : ""}`}>
      <FieldLabel htmlFor={id} label={label} onHelp={onHelp} />
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
      {suggested && <SuggestedTag />}
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
  suggested,
  highlight,
  onHelp,
  onEdit,
  onRemove,
}: {
  index: number;
  draft: BidDraft;
  error: string | null;
  serverErrors: string[];
  loadTypes: { value: string; label: string }[];
  profile: BidFeatures | null;
  onChange: (patch: Partial<BidDraft>) => void;
  suggested: string[];
  highlight: string | null;
  onHelp: (field: string) => void;
  onEdit: () => void;
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
          onClick={onEdit}
          data-testid={`edit-${draft.id}`}
          className="rounded px-2 py-1 text-xs text-muted hover:bg-surface-2 hover:text-ink"
          aria-label={`Edit ${draft.name}`}
        >
          Edit
        </button>
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
          <FieldLabel htmlFor={`${draft.id}-type`} label="Load type" onHelp={() => onHelp("load_type")} />
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
        <NumberField label="Energy" suffix="MWh" value={draft.energy_mwh} onChange={(v) => onChange({ energy_mwh: v })} testId={`energy-${draft.id}`} invalid={!!error} onHelp={() => onHelp("energy_mwh")} highlight={highlight === "energy_mwh"} suggested={suggested.includes("energy_mwh")} />
        <NumberField label="Max power" suffix="MW" value={draft.max_mw} onChange={(v) => onChange({ max_mw: v })} testId={`maxmw-${draft.id}`} invalid={!!error} onHelp={() => onHelp("max_mw")} highlight={highlight === "max_mw"} suggested={suggested.includes("max_mw")} />
        <HourSelect label="Earliest (incl.)" value={draft.earliest_hour} options={EARLIEST} onChange={(v) => onChange({ earliest_hour: v })} testId={`earliest-${draft.id}`} onHelp={() => onHelp("earliest_hour")} highlight={highlight === "earliest_hour"} suggested={suggested.includes("earliest_hour")} />
        <HourSelect label="Deadline (excl.)" value={draft.deadline_hour} options={DEADLINE} onChange={(v) => onChange({ deadline_hour: v })} testId={`deadline-${draft.id}`} onHelp={() => onHelp("deadline_hour")} highlight={highlight === "deadline_hour"} suggested={suggested.includes("deadline_hour")} />
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
      {profile && <BidProfile f={profile} />}
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
  if (f.window_lowest_net_load_hour !== null)
    items.push({
      label: "Lowest net load",
      value: hh(f.window_lowest_net_load_hour),
      title: "Hour in the window with the lowest forecast demand minus wind and solar",
    });
  if (f.slack_hours !== null)
    items.push({
      label: "Slack",
      value: `${f.slack_hours.toFixed(1)} h`,
      title: "Window length minus the hours needed at max power: room the coordinator has to move this bid",
    });
  if (items.length === 0) return null;
  return (
    <dl className="mt-2.5 grid grid-cols-2 gap-x-4 gap-y-1.5 rounded-md bg-surface-2 px-3 py-2 sm:grid-cols-4" data-testid="bid-profile">
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
  const src = block.source === "chalk" ? "Chalk online query (flex_bid features)" : "local model (Chalk credentials pending)";
  const ms = block.latency_ms < 10 ? block.latency_ms.toFixed(1) : fmtInt(block.latency_ms);
  return (
    <Source>
      bid profiles: {src} · {ms} ms · from the day&apos;s forecast, never actuals
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
