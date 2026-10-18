"use client";

import { useEffect, useMemo, useState } from "react";
import { API_MODE, ApiError } from "@/lib/api";
import { fallbackSentence, herdingGapPct, tieSentence } from "@/lib/explain";
import { scheduleErrors } from "@/lib/feasibility";
import { fmtInt, fmtNum1, fmtSignedT, fmtT } from "@/lib/format";
import { STRATEGY_BLURB, STRATEGY_COLORS, STRATEGY_LABEL } from "@/lib/theme";
import type {
  Bid,
  ClearRequest,
  ClearResponse,
  EvaluateResponse,
  MetaResponse,
  StrategyName,
  TraceStep,
} from "@/lib/types";
import { LoadChart, MarginalStrip, type ChartCompany } from "./LoadChart";
import { ScheduleGrid } from "./ScheduleGrid";
import { forecastSourceText } from "./StoryScreen";
import { Button, Details, ErrorBanner, Segmented, SectionTitle, Source, Stat } from "./ui";

export type Act = "1" | "2" | "beat" | "3";

export interface TraceState {
  /** number of trace step GROUPS applied (0..N); entries sharing a `step` form one group */
  idx: number;
  /** furthest idx ever reached; Act 3 counts as run once max === N */
  max: number;
}

export interface EvalState {
  loading: boolean;
  result: EvaluateResponse | null;
  error: ApiError | null;
  clientErrors: Record<string, string>;
}

const ACTS: { id: Act; n: string; title: string; short: string }[] = [
  { id: "1", n: "Act 1", title: "Default", short: "Default" },
  { id: "2", n: "Act 2", title: "Everyone chases the clean hour", short: "Chase the clean hour" },
  { id: "beat", n: "Your turn", title: "Beat the coordinator", short: "Beat the coordinator" },
  { id: "3", n: "Act 3", title: "Coordinated", short: "Coordinated" },
];

/**
 * Group consecutive trace entries that share a step number. The engine aggregates
 * placements into at most ~200 steps, emitting one entry per (bid, hour) per step, so a
 * step can hold several entries; the replay advances one step (group) at a time.
 */
export function traceGroups(steps: TraceStep[]): TraceStep[][] {
  const groups: TraceStep[][] = [];
  for (const st of steps) {
    const last = groups[groups.length - 1];
    if (last && last[0].step === st.step) last.push(st);
    else groups.push([st]);
  }
  return groups;
}

/** Largest |replayed − coordinated| MWh over every (bid, hour). */
function replayMismatch(replayed: Record<string, number[]>, target: Record<string, number[]>): number {
  let m = 0;
  const ids = new Set([...Object.keys(replayed), ...Object.keys(target)]);
  for (const id of ids) {
    for (let h = 0; h < 24; h++) m = Math.max(m, Math.abs((replayed[id]?.[h] ?? 0) - (target[id]?.[h] ?? 0)));
  }
  return m;
}

function niceCeil(x: number): number {
  if (x <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(x)));
  for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= x) return m * p;
  return 10 * p;
}

export function CoordinatorView({
  meta,
  request,
  response,
  bids,
  companies,
  act,
  setAct,
  trace,
  setTrace,
  userSchedule,
  updateUserSchedule,
  resetUserSchedule,
  evalState,
  onScore,
  onRerunDay,
  clearing,
  onNext,
}: {
  meta: MetaResponse;
  request: ClearRequest;
  response: ClearResponse;
  bids: Bid[];
  companies: ChartCompany[];
  act: Act;
  setAct: (a: Act) => void;
  trace: TraceState;
  setTrace: (f: (t: TraceState) => TraceState) => void;
  userSchedule: Record<string, number[]>;
  updateUserSchedule: (bidId: string, f: (prev: number[]) => number[]) => void;
  resetUserSchedule: (mode: "default" | "empty") => void;
  evalState: EvalState;
  onScore: () => void;
  onRerunDay: (day: string) => void;
  clearing: boolean;
  onNext: () => void;
}) {
  const steps = response.coordinator_trace;
  const groups = useMemo(() => traceGroups(steps), [steps]);
  const N = groups.length;
  const [playing, setPlaying] = useState(false);
  const [signal, setSignal] = useState<"independent_average" | "independent_marginal">(
    "independent_average",
  );
  const [hour, setHour] = useState<number | null>(null);
  const isPlaying = playing && trace.idx < N && act === "3";
  const act3Done = N === 0 || trace.max >= N;

  // Trace replay: ~6 s total whatever the number of steps.
  useEffect(() => {
    if (!isPlaying) return;
    const ms = Math.max(25, Math.min(260, 6000 / Math.max(1, N)));
    const t = setInterval(() => {
      setTrace((s) => {
        const idx = Math.min(N, s.idx + 1);
        return { idx, max: Math.max(s.max, idx) };
      });
    }, ms);
    return () => clearInterval(t);
  }, [isPlaying, N, setTrace]);

  const strategyFor: Record<Exclude<Act, "beat">, StrategyName> = {
    "1": "default",
    "2": signal,
    "3": "coordinated",
  };

  const yMax = useMemo(() => {
    let m = 0;
    for (const s of Object.values(response.strategies)) {
      for (let h = 0; h < 24; h++) {
        let t = 0;
        for (const c of companies) t += s.schedule[c.id]?.[h] ?? 0;
        m = Math.max(m, t);
      }
    }
    return niceCeil(m * 1.05);
  }, [response, companies]);

  // Cumulative replay: every entry of every applied step group, summed per (bid, hour).
  const replayed = useMemo(() => {
    const sch: Record<string, number[]> = {};
    for (const c of companies) sch[c.id] = new Array(24).fill(0);
    let mwh = 0;
    let co2 = 0;
    for (let g = 0; g < Math.min(trace.idx, N); g++) {
      for (const st of groups[g]) {
        if (!sch[st.bid_id]) sch[st.bid_id] = new Array(24).fill(0);
        sch[st.bid_id][st.hour] += st.mwh;
        mwh += st.mwh;
        co2 += st.incremental_co2_t;
      }
    }
    return { sch, mwh, co2 };
  }, [trace.idx, N, groups, companies]);

  // End-of-replay check: Σ replayed MWh per (bid, hour) must equal the coordinated schedule.
  const replayCheck = useMemo(() => {
    const full: Record<string, number[]> = {};
    for (const st of steps) {
      if (!full[st.bid_id]) full[st.bid_id] = new Array(24).fill(0);
      full[st.bid_id][st.hour] += st.mwh;
    }
    const mismatch = replayMismatch(full, response.strategies.coordinated.schedule);
    const mwh = steps.reduce((a, st) => a + st.mwh, 0);
    return { mismatch, ok: mismatch <= 1e-6 * Math.max(1, mwh), mwh };
  }, [steps, response]);

  // If the replay ever disagreed with the schedule, the final frame shows the schedule itself.
  const traceSchedule =
    trace.idx >= N && !replayCheck.ok ? response.strategies.coordinated.schedule : replayed.sch;
  const partial = { mwh: replayed.mwh, co2: replayed.co2 };

  const totalEnergy = bids.reduce((a, b) => a + b.energy_mwh, 0);
  const def = response.strategies.default;
  const dayLabel = meta.days.find((d) => d.date === request.day)?.label ?? request.day;

  const goAct = (a: Act) => {
    setAct(a);
    if (a === "3" && trace.max < N) {
      setTrace((s) => (s.idx >= N ? { ...s, idx: 0 } : s));
      setPlaying(true);
    } else {
      setPlaying(false);
    }
  };

  const togglePlay = () => {
    if (isPlaying) {
      setPlaying(false);
      return;
    }
    if (trace.idx >= N) setTrace((s) => ({ ...s, idx: 0 }));
    setPlaying(true);
  };

  const s: StrategyName | null = act === "beat" ? null : strategyFor[act];
  const result = s ? response.strategies[s] : null;
  const chartSchedule = act === "3" ? traceSchedule : act === "beat" ? userSchedule : result!.schedule;
  const chartMarginal =
    act === "beat" ? (evalState.result?.marginal_resource ?? def.marginal_resource) : result!.marginal_resource;
  const chartCo2 = act === "beat" ? (evalState.result?.hourly_co2_t ?? null) : result!.hourly_co2_t;
  const marginalCaption =
    act === "beat"
      ? evalState.result
        ? "marginal plants for your scored schedule (/api/evaluate)"
        : "showing Act 1 (default) margins until you score"
      : `marginal plants under ${STRATEGY_LABEL[s!].toLowerCase()} (scored on actual day)`;

  return (
    <div>
      <SectionTitle kicker="Step 3 · Watch the coordinator" title="Three ways to run the same bids">
        Same companies, same day. Only the scheduling rule changes.
      </SectionTitle>

      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-ink-2">
        <span>
          Day: <span className="font-medium text-ink">{dayLabel}</span>{" "}
          <span className="num text-muted">{request.day}</span>
        </span>
        <label className="flex items-center gap-2 text-xs text-muted">
          Re-run on
          <select
            data-testid="day-rerun"
            value={request.day}
            disabled={clearing}
            onChange={(e) => onRerunDay(e.target.value)}
            className="rounded-md border border-line-strong bg-surface px-2 py-1 text-xs text-ink"
          >
            {meta.days.map((d) => (
              <option key={d.date} value={d.date}>
                {d.label}
              </option>
            ))}
          </select>
          {clearing && <span className="text-warn">clearing…</span>}
        </label>
      </div>

      <nav aria-label="Acts" className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        {ACTS.map((a) => (
          <button
            key={a.id}
            type="button"
            data-testid={a.id === "beat" ? "act-beat" : `act-${a.id}`}
            aria-pressed={act === a.id}
            onClick={() => goAct(a.id)}
            className={`rounded-lg border px-3 py-2 text-left transition-colors ${
              act === a.id
                ? "border-ink bg-surface shadow-sm"
                : "border-line bg-surface/60 hover:border-line-strong"
            }`}
          >
            <div className="kicker">{a.n}</div>
            <div className="mt-0.5 text-sm font-medium leading-snug text-ink">{a.short}</div>
          </button>
        ))}
      </nav>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <section className="card min-w-0 p-3 sm:p-5">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <Legend companies={companies} />
            {act === "2" && (
              <Segmented
                label="Signal used by each company"
                value={signal}
                onChange={setSignal}
                testIdPrefix="act2-signal"
                options={[
                  { value: "independent_average", label: "Average signal" },
                  { value: "independent_marginal", label: "Marginal, solo" },
                ]}
              />
            )}
          </div>

          {act === "beat" ? (
            <BeatPanel
              bids={bids}
              companies={companies}
              userSchedule={userSchedule}
              updateUserSchedule={updateUserSchedule}
              resetUserSchedule={resetUserSchedule}
              evalState={evalState}
              onScore={onScore}
              hour={hour}
              setHour={setHour}
            />
          ) : (
            <LoadChart
              companies={companies}
              schedule={chartSchedule}
              marginal={chartMarginal}
              hourlyCo2={act === "3" && trace.idx < N ? null : chartCo2}
              yMax={yMax}
              animate={act !== "3"}
              selectedHour={hour}
              onSelectHour={setHour}
              marginalCaption={marginalCaption}
            />
          )}

          {act === "3" && N > 0 && (
            <div className="mt-4 rounded-md border border-line bg-surface-2/60 p-3">
              <div className="flex flex-wrap items-center gap-3">
                <Button variant="secondary" onClick={togglePlay} testId="play-trace" className="w-[104px]">
                  {isPlaying ? "Pause" : trace.idx >= N ? "Replay" : "Play"}
                </Button>
                <input
                  type="range"
                  min={0}
                  max={N}
                  step={1}
                  value={trace.idx}
                  aria-label="Coordinator trace step"
                  data-testid="trace-scrub"
                  onChange={(e) => {
                    const idx = Number(e.target.value);
                    setPlaying(false);
                    setTrace((t) => ({ idx, max: Math.max(t.max, idx) }));
                  }}
                  className="min-w-[120px] flex-1"
                />
                <span className="num text-xs text-muted" data-testid="trace-step">
                  step {trace.idx}/{N}
                </span>
              </div>
              <div className="mt-2 text-xs text-ink-2">
                {trace.idx === 0 ? (
                  "The coordinator places each block of energy in the hour where it adds the least CO2."
                ) : trace.idx < N ? (
                  <TraceLine group={groups[trace.idx - 1]} companies={companies} />
                ) : (
                  <span data-testid="trace-done">
                    All {fmtInt(replayed.mwh)} MWh placed.{" "}
                    {replayCheck.ok ? null : (
                      <span className="text-danger">
                        Replay differs from the coordinated schedule by up to {replayCheck.mismatch.toFixed(3)} MWh; the schedule
                        itself is shown.
                      </span>
                    )}
                  </span>
                )}
              </div>
            </div>
          )}

        </section>

        <aside className="min-w-0 space-y-4">
          {act !== "beat" && s && (
            <Co2Counter
              strategy={s}
              total={act === "3" && trace.idx < N ? null : response.strategies[s].total_co2_t}
              defaultTotal={def.total_co2_t}
              partial={act === "3" && trace.idx < N ? { ...partial, totalEnergy } : null}
            />
          )}
          <Narrative
            act={act}
            response={response}
            signal={signal}
            act3Done={act3Done}
            evalState={evalState}
          />
          <div className="flex justify-between gap-2">
            {act !== "3" ? (
              <Button
                variant="secondary"
                onClick={() => goAct(ACTS[ACTS.findIndex((a) => a.id === act) + 1].id)}
                testId="next-act"
              >
                Next: {ACTS[ACTS.findIndex((a) => a.id === act) + 1].short} <span aria-hidden>→</span>
              </Button>
            ) : (
              <Button onClick={onNext} testId="to-results">
                Back to results <span aria-hidden>→</span>
              </Button>
            )}
          </div>
        </aside>
      </div>

      <div className="mt-6">
        <Details>
          {[tieSentence(response), fallbackSentence(response)].filter(Boolean).map((n) => (
            <p key={n} className="text-sm leading-relaxed text-ink-2">{n}</p>
          ))}
          <p className="text-sm leading-relaxed text-ink-2">
            Every strategy is planned on the forecast and scored on the actual EIA-930 day. The coordinator
            places blocks one at a time, round-robin by earliest deadline; greedy is not guaranteed optimal.
          </p>
        <Source>
            schedules: POST /api/clear strategies.{act === "beat" ? "default (start)" : s} · forecast:{" "}
            {forecastSourceText(response.forecast)} · scored on actual EIA-930 day
            {act === "3" &&
              ` · animation replays coordinator_trace (${steps.length} entries in ${N} steps, aggregated by the engine)`}
          </Source>
        </Details>
      </div>
    </div>
  );
}

function Legend({ companies }: { companies: ChartCompany[] }) {
  return (
    <ul className="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-xs text-ink-2">
      {companies.map((c) => (
        <li key={c.id} className="flex min-w-0 items-center gap-1.5">
          <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: c.color }} aria-hidden />
          <span className="truncate">{c.name}</span>
        </li>
      ))}
    </ul>
  );
}

function TraceLine({ group, companies }: { group: TraceStep[]; companies: ChartCompany[] }) {
  const mwh = group.reduce((a, s) => a + s.mwh, 0);
  const co2 = group.reduce((a, s) => a + s.incremental_co2_t, 0);
  const nameOf = (id: string) => companies.find((x) => x.id === id)?.name ?? id;
  const hr = (h: number) => String(h).padStart(2, "0");
  if (group.length === 1) {
    const st = group[0];
    return (
      <span>
        Step <span className="num">{st.step}</span>: placed{" "}
        <span className="num font-medium text-ink">{fmtNum1(st.mwh)} MWh</span> of{" "}
        <span className="font-medium text-ink">{nameOf(st.bid_id)}</span> in hour <span className="num">{hr(st.hour)}</span>,
        adding <span className="num font-medium text-ink">{fmtT(st.incremental_co2_t)} t</span> CO2 (planned on the forecast).
      </span>
    );
  }
  return (
    <span>
      Step <span className="num">{group[0].step}</span>: placed{" "}
      <span className="num font-medium text-ink">{fmtNum1(mwh)} MWh</span> in {group.length} blocks (
      {group.map((st, i) => (
        <span key={`${st.bid_id}-${st.hour}`}>
          {nameOf(st.bid_id)} → <span className="num">{hr(st.hour)}</span>
          {i < group.length - 1 ? ", " : ""}
        </span>
      ))}
      ), adding <span className="num font-medium text-ink">{fmtT(co2)} t</span> CO2 (planned on the forecast).
    </span>
  );
}

function Co2Counter({
  strategy,
  total,
  defaultTotal,
  partial,
}: {
  strategy: StrategyName;
  total: number | null;
  defaultTotal: number;
  partial: { mwh: number; co2: number; totalEnergy: number } | null;
}) {
  const avoided = total === null ? null : defaultTotal - total;
  return (
    <div className="card p-4" data-testid="co2-counter">
      <div className="mb-3 flex items-center gap-2">
        <span className="h-2 w-5 rounded-sm" style={{ background: STRATEGY_COLORS[strategy] }} aria-hidden />
        <span className="text-xs font-medium text-ink-2">{STRATEGY_LABEL[strategy]}</span>
      </div>
      {partial ? (
        <div className="grid grid-cols-2 gap-3">
          <Stat
            label="Energy placed"
            value={`${fmtInt(partial.mwh)}`}
            sub={`of ${fmtInt(partial.totalEnergy)} MWh`}
          />
          <Stat label="Added CO2 so far" value={fmtT(partial.co2)} sub="t (forecast)" />
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Grid CO2, whole day" value={fmtInt(total ?? 0)} sub="t" testId="co2-total" />
          <Stat
            label="vs default"
            value={avoided === null ? "—" : strategy === "default" ? "0" : fmtSignedT(-avoided)}
            sub={
              strategy === "default"
                ? "reference"
                : avoided !== null && avoided >= 0
                  ? `t (${fmtT(avoided)} t avoided)`
                  : "t (more than default)"
            }
            tone={strategy === "default" ? "muted" : avoided !== null && avoided >= 0 ? "good" : "bad"}
            testId="co2-delta"
          />
        </div>
      )}
    </div>
  );
}

function Narrative({
  act,
  response,
  signal,
  act3Done,
  evalState,
}: {
  act: Act;
  response: ClearResponse;
  signal: "independent_average" | "independent_marginal";
  act3Done: boolean;
  evalState: EvalState;
}) {
  const h = response.herding;
  const st = response.strategies;
  if (act === "1") {
    return (
      <div className="card p-4 text-sm leading-relaxed text-ink-2">
        <div className="kicker mb-1.5">Act 1 · Default</div>
        {STRATEGY_BLURB.default} This is the reference every other strategy is compared with.
      </div>
    );
  }
  if (act === "2") {
    const m = h[signal];
    const pct = herdingGapPct(m);
    return (
      <div className="card space-y-3 p-4 text-sm leading-relaxed text-ink-2">
        <div className="kicker">Act 2 · Everyone chases the clean hour</div>
        <p>{STRATEGY_BLURB[signal]}</p>
        <div className="grid grid-cols-3 gap-2 rounded-md bg-surface-2/70 p-3">
          <Stat label="Promised" value={fmtT(m.predicted_t)} sub="t, sum of solo savings" />
          <Stat label="Realized" value={fmtT(m.realized_t)} sub="t, all together" />
          <Stat
            label="Gap"
            value={fmtT(m.gap_t)}
            sub={pct === null ? "t" : `t (${pct.toFixed(0)}% short)`}
            tone={m.gap_t > 0 && pct !== null ? "bad" : "default"}
          />
        </div>
        <p className="text-xs text-muted">
          Promised: each company&apos;s solo savings, added up. Realized: what they save together.
        </p>
      </div>
    );
  }
  if (act === "beat") {
    return (
      <div className="card space-y-2 p-4 text-sm leading-relaxed text-ink-2">
        <div className="kicker">Your turn · Beat the coordinator</div>
        <p>
          Drag in each row to place each company&apos;s energy, then score it.
        </p>
        <ScoreBoard response={response} evalState={evalState} act3Done={act3Done} />
      </div>
    );
  }
  const co = st.coordinated.total_co2_t;
  const im = st.independent_marginal.total_co2_t;
  return (
    <div className="card space-y-3 p-4 text-sm leading-relaxed text-ink-2">
      <div className="kicker">Act 3 · Coordinated</div>
      <p>{STRATEGY_BLURB.coordinated}</p>
      {act3Done && (
        <>
          {im - co > 0.05 && (
            <p>
              Saves <span className="num font-medium text-accent">{fmtT(im - co)} t</span> more than each
              company could alone. This is what payouts are based on.
            </p>
          )}
          <ScoreBoard response={response} evalState={evalState} act3Done={act3Done} />
        </>
      )}
    </div>
  );
}

function ScoreBoard({
  response,
  evalState,
  act3Done,
}: {
  response: ClearResponse;
  evalState: EvalState;
  act3Done: boolean;
}) {
  const r = evalState.result;
  const co = response.strategies.coordinated.total_co2_t;
  const def = response.strategies.default.total_co2_t;
  return (
    <div className="grid grid-cols-2 gap-3 rounded-md bg-surface-2/70 p-3" data-testid="scoreboard">
      <Stat
        label="Your schedule"
        value={r ? fmtInt(r.total_co2_t) : "—"}
        sub={r ? `t · ${fmtSignedT(r.total_co2_t - def)} vs default` : "not scored yet"}
        testId="user-score"
      />
      <Stat
        label="Coordinator"
        value={act3Done ? fmtInt(co) : "?"}
        sub={act3Done ? `t · ${fmtSignedT(co - def)} vs default` : "revealed after Act 3"}
        testId="coordinator-score"
      />
      {r && act3Done && (
        <div className="col-span-2 text-xs text-ink-2">
          {r.total_co2_t < co ? (
            <span className="font-medium text-accent">
              You beat the coordinator by {fmtT(co - r.total_co2_t)} t.
            </span>
          ) : (
            <span>
              The coordinator emits <span className="num font-medium">{fmtT(r.total_co2_t - co)} t</span> less than
              your schedule.
            </span>
          )}
          {API_MODE === "fixture" && " (Fixture mode: your score is the recorded score of a flat hand schedule, not your drag.)"}
        </div>
      )}
    </div>
  );
}

function BeatPanel({
  bids,
  companies,
  userSchedule,
  updateUserSchedule,
  resetUserSchedule,
  evalState,
  onScore,
  hour,
  setHour,
}: {
  bids: Bid[];
  companies: ChartCompany[];
  userSchedule: Record<string, number[]>;
  updateUserSchedule: (bidId: string, f: (prev: number[]) => number[]) => void;
  resetUserSchedule: (mode: "default" | "empty") => void;
  evalState: EvalState;
  onScore: () => void;
  hour: number | null;
  setHour: (h: number | null) => void;
}) {
  const serverErr = new Map<string, string>();
  for (const e of evalState.result?.errors ?? []) serverErr.set(e.bid_id, e.message);
  for (const e of evalState.error?.bidErrors ?? []) if (e.bid_id) serverErr.set(e.bid_id, e.message);
  const allPlaced = bids.every((b) => scheduleErrors(b, userSchedule[b.id] ?? []) === null);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2">
        <Button variant="ghost" onClick={() => resetUserSchedule("default")} testId="beat-reset-default">
          Start from default
        </Button>
        <Button variant="ghost" onClick={() => resetUserSchedule("empty")} testId="beat-clear">
          Clear all
        </Button>
      </div>
      <div className="space-y-4">
        {bids.map((b) => {
          const c = companies.find((x) => x.id === b.id);
          return (
            <ScheduleGrid
              key={b.id}
              bid={b}
              color={c?.color ?? "#2a78d6"}
              mw={userSchedule[b.id] ?? new Array(24).fill(0)}
              onUpdate={(f) => updateUserSchedule(b.id, f)}
              error={evalState.clientErrors[b.id] ?? serverErr.get(b.id) ?? null}
            />
          );
        })}
      </div>
      {evalState.error && (
        <ErrorBanner
          title="Could not score the schedule"
          message={evalState.error.message}
          items={evalState.error.bidErrors.map((e) => ({
            label: bids.find((b) => b.id === e.bid_id)?.name ?? e.bid_id ?? "Request",
            message: e.message,
          }))}
        />
      )}
      {evalState.result && !evalState.result.feasible && (
        <ErrorBanner
          title="Schedule infeasible"
          message="The backend rejected parts of this schedule; see the messages under each company."
          items={evalState.result.errors.map((e) => ({
            label: bids.find((b) => b.id === e.bid_id)?.name ?? e.bid_id,
            message: e.message,
          }))}
        />
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button onClick={onScore} disabled={evalState.loading} testId="score-schedule">
          {evalState.loading ? "Scoring…" : "Score my schedule"}
        </Button>
        {!allPlaced && <span className="text-xs text-warn">Place every company&apos;s full energy to score.</span>}
      </div>
      {evalState.result && (
        <div>
          <MarginalStrip
            marginal={evalState.result.marginal_resource}
            selectedHour={hour}
            onSelectHour={setHour}
            caption="marginal plants for your schedule (/api/evaluate)"
            inset={false}
          />
          <Source className="mt-2">
            POST /api/evaluate · scored on the actual EIA-930 day, same rules as the strategies
            {API_MODE === "fixture" ? " · fixture: recorded score of a flat hand schedule, not computed from your drag" : ""}
          </Source>
        </div>
      )}
    </div>
  );
}
