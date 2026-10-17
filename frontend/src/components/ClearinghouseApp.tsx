"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  API_BASE,
  API_MODE,
  ApiError,
  EXAMPLE_CLEAR_REQUEST,
  getAdoptionChart,
  getMeta,
  getPresets,
  postClear,
  postEvaluate,
} from "@/lib/api";
import { bidToDraft, draftError, draftToBid, type BidDraft } from "@/lib/bids";
import { scheduleErrors } from "@/lib/feasibility";
import { companyColor } from "@/lib/theme";
import type {
  AdoptionChartResponse,
  Bid,
  ClearRequest,
  ClearResponse,
  MetaResponse,
  Preset,
} from "@/lib/types";
import { BidBuilder, scenarioErrors, type Scenario } from "./BidBuilder";
import { CoordinatorView, type Act, type EvalState, type TraceState } from "./CoordinatorView";
import type { ChartCompany } from "./LoadChart";
import { ResultsView } from "./ResultsView";
import { StoryScreen } from "./StoryScreen";
import { Button, ErrorBanner, FixtureBadge } from "./ui";

type Step = "story" | "bids" | "coordinator" | "results";

const STEPS: { id: Step; n: number; label: string }[] = [
  { id: "story", n: 1, label: "The story" },
  { id: "bids", n: 2, label: "Bids" },
  { id: "coordinator", n: 3, label: "Coordinator" },
  { id: "results", n: 4, label: "Results" },
];

interface Boot {
  meta: MetaResponse;
  presets: Preset[];
  adoption: AdoptionChartResponse;
}

interface Cleared {
  request: ClearRequest;
  response: ClearResponse;
  /** fixture mode served the example scenario instead of the submitted bids */
  substituted: boolean;
}

const EMPTY_EVAL: EvalState = { loading: false, result: null, error: null, clientErrors: {} };

function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError("network", e instanceof Error ? e.message : "Unexpected error");
}

function sameRequest(a: ClearRequest, b: ClearRequest): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

function copySchedule(s: Record<string, number[]>, bids: Bid[]): Record<string, number[]> {
  const out: Record<string, number[]> = {};
  for (const b of bids) out[b.id] = (s[b.id] ?? new Array(24).fill(0)).slice();
  return out;
}

export function ClearinghouseApp() {
  const [boot, setBoot] = useState<Boot | null>(null);
  const [bootError, setBootError] = useState<ApiError | null>(null);
  const [bootTry, setBootTry] = useState(0);

  const [drafts, setDrafts] = useState<BidDraft[]>([]);
  const [scenario, setScenario] = useState<Scenario | null>(null);
  const [step, setStepRaw] = useState<Step>("story");

  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<ApiError | null>(null);
  const [cleared, setCleared] = useState<Cleared | null>(null);

  const [act, setAct] = useState<Act>("1");
  const [trace, setTrace] = useState<TraceState>({ idx: 0, max: 0 });
  const [userSchedule, setUserSchedule] = useState<Record<string, number[]>>({});
  const [evalState, setEvalState] = useState<EvalState>(EMPTY_EVAL);
  const [leftOut, setLeftOut] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [meta, presets, adoption] = await Promise.all([getMeta(), getPresets(), getAdoptionChart()]);
        if (cancelled) return;
        setBoot({ meta, presets, adoption });
        setBootError(null);
        setScenario(
          (s) =>
            s ?? {
              day: meta.default_day,
              background_pct: meta.background_pct_default,
              carbon_price: String(meta.carbon_price_default),
              pool_usd: String(meta.pool_default_usd),
            },
        );
      } catch (e) {
        if (!cancelled) setBootError(toApiError(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [bootTry]);

  const setStep = useCallback((s: Step) => {
    setStepRaw(s);
    if (typeof window !== "undefined") window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  const loadExample = useCallback(() => {
    const ex = EXAMPLE_CLEAR_REQUEST;
    setDrafts(ex.bids.map((b) => bidToDraft(b)));
    setScenario({
      day: ex.day,
      background_pct: ex.background_pct,
      carbon_price: String(ex.carbon_price),
      pool_usd: String(ex.pool_usd),
    });
    setClearError(null);
  }, []);

  const runClear = useCallback(
    async (dayOverride?: string) => {
      if (!scenario || clearing) return;
      const sc = dayOverride ? { ...scenario, day: dayOverride } : scenario;
      if (dayOverride) setScenario(sc);
      if (drafts.some((d) => draftError(d) !== null) || scenarioErrors(sc).length) return;
      const req: ClearRequest = {
        day: sc.day,
        bids: drafts.map(draftToBid),
        background_pct: sc.background_pct,
        carbon_price: Number(sc.carbon_price),
        pool_usd: Number(sc.pool_usd),
      };
      setClearing(true);
      setClearError(null);
      try {
        const response = await postClear(req);
        const substituted = API_MODE === "fixture" && !sameRequest(req, EXAMPLE_CLEAR_REQUEST);
        const request = API_MODE === "fixture" ? EXAMPLE_CLEAR_REQUEST : req;
        setCleared({ request, response, substituted });
        setAct("1");
        setTrace({ idx: 0, max: 0 });
        setUserSchedule(copySchedule(response.strategies.default.schedule, request.bids));
        setEvalState(EMPTY_EVAL);
        setLeftOut(null);
        setStep("coordinator");
      } catch (e) {
        setClearError(toApiError(e));
      } finally {
        setClearing(false);
      }
    },
    [scenario, clearing, drafts, setStep],
  );

  const companies = useMemo<ChartCompany[]>(
    () =>
      (cleared?.request.bids ?? []).map((b, i) => ({ id: b.id, name: b.name, color: companyColor(i) })),
    [cleared],
  );

  const updateUserSchedule = useCallback((bidId: string, f: (prev: number[]) => number[]) => {
    setUserSchedule((s) => {
      const prev = s[bidId] ?? new Array(24).fill(0);
      const next = f(prev);
      return next === prev ? s : { ...s, [bidId]: next };
    });
    setEvalState((e) => {
      if (!(bidId in e.clientErrors)) return e;
      const rest = { ...e.clientErrors };
      delete rest[bidId];
      return { ...e, clientErrors: rest };
    });
  }, []);

  const resetUserSchedule = useCallback(
    (mode: "default" | "empty") => {
      if (!cleared) return;
      const bids = cleared.request.bids;
      setUserSchedule(mode === "default" ? copySchedule(cleared.response.strategies.default.schedule, bids) : copySchedule({}, bids));
      setEvalState((e) => ({ ...e, clientErrors: {} }));
    },
    [cleared],
  );

  const onScore = useCallback(async () => {
    if (!cleared) return;
    const bids = cleared.request.bids;
    const clientErrors: Record<string, string> = {};
    for (const b of bids) {
      const err = scheduleErrors(b, userSchedule[b.id] ?? []);
      if (err) clientErrors[b.id] = err;
    }
    if (Object.keys(clientErrors).length) {
      setEvalState((e) => ({ ...e, clientErrors, error: null }));
      return;
    }
    setEvalState((e) => ({ ...e, loading: true, error: null, clientErrors: {} }));
    try {
      const result = await postEvaluate({
        day: cleared.request.day,
        bids,
        schedule: copySchedule(userSchedule, bids),
        background_pct: cleared.request.background_pct,
      });
      setEvalState({ loading: false, result, error: null, clientErrors: {} });
    } catch (e) {
      setEvalState((s) => ({ ...s, loading: false, error: toApiError(e) }));
    }
  }, [cleared, userSchedule]);

  const ready = boot && scenario;

  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-30 border-b border-line bg-paper/95 backdrop-blur">
        <div className="mx-auto flex max-w-[1180px] flex-wrap items-center justify-between gap-x-3 gap-y-1.5 px-4 py-2.5">
          <div className="flex min-w-0 items-center gap-2.5">
            <Mark />
            <div className="min-w-0">
              <div className="whitespace-nowrap font-serif text-[17px] font-semibold leading-tight text-ink">Carbon Clearinghouse</div>
              <div className="hidden truncate text-[11px] text-muted sm:block">
                Coordinated load shifting with avoided-carbon payouts
              </div>
            </div>
          </div>
          <FixtureBadge />
        </div>
        <nav aria-label="Steps" className="mx-auto max-w-[1180px] px-4 pb-2">
          <ol className="grid grid-cols-4 gap-1.5">
            {STEPS.map((s) => {
              const enabled = s.id === "story" || s.id === "bids" ? !!ready : !!cleared;
              const current = step === s.id;
              return (
                <li key={s.id} className="min-w-0">
                  <button
                    type="button"
                    disabled={!enabled}
                    onClick={() => setStep(s.id)}
                    data-testid={`step-${s.id}`}
                    aria-current={current ? "step" : undefined}
                    className={`w-full border-t-2 pt-1.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                      current ? "border-accent text-ink" : "border-line text-muted hover:border-line-strong hover:text-ink-2"
                    }`}
                  >
                    <span className="num mr-1 text-[11px]">{s.n}</span>
                    <span className="text-xs font-medium sm:text-[13px]">{s.label}</span>
                  </button>
                </li>
              );
            })}
          </ol>
        </nav>
      </header>

      <main className="mx-auto w-full max-w-[1180px] flex-1 px-4 py-6 sm:py-8">
        {bootError && !boot && (
          <div className="space-y-3">
            <ErrorBanner
              title="Could not load the clearinghouse"
              message={`${bootError.message}${API_MODE === "live" ? ` Is the backend running at ${API_BASE}?` : ""}`}
            />
            <Button variant="secondary" onClick={() => setBootTry((n) => n + 1)}>
              Retry
            </Button>
          </div>
        )}
        {!ready && !bootError && <Skeleton />}

        {ready && cleared?.substituted && (step === "coordinator" || step === "results") && (
          <div className="mb-5 rounded-lg border border-warn/30 bg-warn-soft px-4 py-3 text-sm text-warn" role="status" data-testid="fixture-substitution">
            Fixture mode: showing the recorded example scenario ({cleared.request.bids.length} companies,{" "}
            {cleared.request.day}) instead of the bids you entered. Set NEXT_PUBLIC_API_MODE=live to clear your own bids.
          </div>
        )}
        {ready && clearError && step !== "bids" && (
          <div className="mb-5">
            <ErrorBanner
              title="Re-run failed"
              message={`${clearError.message} The previous results are still shown.`}
              items={clearError.bidErrors.map((e) => ({ label: e.bid_id ?? "Request", message: e.message }))}
              onDismiss={() => setClearError(null)}
            />
          </div>
        )}

        {ready && step === "story" && (
          <StoryScreen
            meta={boot.meta}
            adoption={boot.adoption}
            day={cleared?.request.day ?? scenario.day}
            forecast={cleared?.response.forecast ?? null}
            onNext={() => setStep("bids")}
          />
        )}
        {ready && step === "bids" && (
          <BidBuilder
            meta={boot.meta}
            presets={boot.presets}
            drafts={drafts}
            setDrafts={setDrafts}
            scenario={scenario}
            setScenario={(f) => setScenario((s) => (s ? f(s) : s))}
            onLoadExample={loadExample}
            onRun={() => void runClear()}
            clearing={clearing}
            clearError={clearError}
            onDismissError={() => setClearError(null)}
          />
        )}
        {ready && cleared && step === "coordinator" && (
          <CoordinatorView
            meta={boot.meta}
            request={cleared.request}
            response={cleared.response}
            bids={cleared.request.bids}
            companies={companies}
            act={act}
            setAct={setAct}
            trace={trace}
            setTrace={setTrace}
            userSchedule={userSchedule}
            updateUserSchedule={updateUserSchedule}
            resetUserSchedule={resetUserSchedule}
            evalState={evalState}
            onScore={() => void onScore()}
            onRerunDay={(d) => void runClear(d)}
            clearing={clearing}
            onNext={() => setStep("results")}
          />
        )}
        {ready && cleared && step === "results" && (
          <ResultsView
            request={cleared.request}
            response={cleared.response}
            bids={cleared.request.bids}
            companies={companies}
            leftOut={leftOut}
            setLeftOut={setLeftOut}
          />
        )}
      </main>

      <footer className="border-t border-line">
        <div className="mx-auto max-w-[1180px] px-4 py-5 text-xs leading-relaxed text-muted">
          The coordinator is an optimizer, not an LLM. Every number on screen comes from the API
          ({API_MODE === "fixture" ? "fixture mode: backend responses recorded in fixtures/ for the example scenario" : `live: ${API_BASE}`}).
          Bids are additional load on top of real EIA-930 demand; strategies are planned on the
          forecast and scored on the actual day.
        </div>
      </footer>
    </div>
  );
}

function Mark() {
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden className="shrink-0">
      <rect x="0.5" y="0.5" width="25" height="25" rx="6" fill="#0f5c45" />
      <rect x="5" y="14" width="3" height="7" rx="1" fill="#fdfdfb" opacity="0.55" />
      <rect x="9.5" y="10" width="3" height="11" rx="1" fill="#fdfdfb" opacity="0.75" />
      <rect x="14" y="7" width="3" height="14" rx="1" fill="#fdfdfb" />
      <rect x="18.5" y="11" width="3" height="10" rx="1" fill="#fdfdfb" opacity="0.75" />
    </svg>
  );
}

function Skeleton() {
  return (
    <div className="animate-pulse space-y-6" aria-busy="true" aria-label="Loading">
      <div className="h-4 w-56 rounded bg-surface-2" />
      <div className="h-24 max-w-3xl rounded bg-surface-2" />
      <div className="h-80 rounded-lg bg-surface-2" />
    </div>
  );
}
