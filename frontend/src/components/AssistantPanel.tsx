"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ASSISTANT_AVAILABLE,
  FIELD_LABEL,
  PERSONAS,
  draftToAssistantBid,
  getAssistantStatus,
  postSuggest,
  streamChat,
  type AssistantMeta,
  type AssistantScenario,
  type AssistantStatus,
  type PersonaId,
  type Suggestion,
} from "@/lib/assistant";
import type { BidDraft } from "@/lib/bids";
import { hh } from "@/lib/format";

const SUGGEST_DEBOUNCE_MS = 600;
const CHIPS = ["What is max MW?", "Is my window feasible?", "Which hours are cleanest today?"];
const FIELD_QUESTION: Record<string, string> = {
  earliest_hour: "What does the earliest hour mean for my bid?",
  deadline_hour: "What does the deadline mean for my bid?",
  energy_mwh: "What is energy (MWh) and how should I size it?",
  max_mw: "What is max MW?",
  load_type: "What does load type change?",
};

export interface HelpRequest {
  field: string;
  bidId: string;
  nonce: number;
}

interface Msg {
  role: "user" | "assistant";
  content: string;
  meta?: AssistantMeta;
  suggestions?: Suggestion[];
  error?: string;
  pending?: boolean;
}

function fmtValue(field: string, v: number): string {
  if (field === "earliest_hour" || field === "deadline_hour") return hh(v);
  return `${v} ${field === "max_mw" ? "MW" : "MWh"}`;
}

export function AssistantPanel({
  day,
  dayLabel,
  drafts,
  scenario,
  onApply,
  help,
}: {
  day: string;
  dayLabel: string;
  drafts: BidDraft[];
  scenario: AssistantScenario;
  onApply: (s: Suggestion) => void;
  help: HelpRequest | null;
}) {
  const [open, setOpen] = useState(false);
  const [persona, setPersona] = useState<PersonaId>("ev_fleet");
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [statusError, setStatusError] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [live, setLive] = useState<Suggestion[]>([]);
  const [applied, setApplied] = useState<Set<string>>(new Set());
  const listRef = useRef<HTMLDivElement>(null);
  const chatAbort = useRef<AbortController | null>(null);

  const bids = drafts.map(draftToAssistantBid);
  const bidsKey = JSON.stringify([day, bids, persona, scenario]);
  const nameOf = (id: string) => drafts.find((d) => d.id === id)?.name || id;
  const sugKey = (s: Suggestion) => `${s.bid_id}|${s.field}|${s.from}|${s.to}`;

  useEffect(() => {
    if (!ASSISTANT_AVAILABLE) return;
    getAssistantStatus()
      .then((s) => {
        setStatus(s);
        setStatusError(false);
      })
      .catch(() => setStatusError(true));
  }, [open]);

  // Live suggestions: debounced on any bid, day, scenario or persona change.
  useEffect(() => {
    if (!ASSISTANT_AVAILABLE) return;
    const [d, b, p, sc] = JSON.parse(bidsKey) as [string, typeof bids, PersonaId, AssistantScenario];
    if (b.length === 0) {
      setLive([]);
      return;
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      postSuggest({ day: d, bids: b, persona: p, scenario: sc }, ctrl.signal)
        .then((r) => setLive(r.suggestions))
        .catch(() => {});
    }, SUGGEST_DEBOUNCE_MS);
    return () => {
      clearTimeout(t);
      ctrl.abort();
    };
  }, [bidsKey]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [messages]);

  const send = useCallback(
    async (text: string, focusField: string | null = null) => {
      const q = text.trim();
      if (!q || streaming || !ASSISTANT_AVAILABLE) return;
      chatAbort.current?.abort();
      const ctrl = new AbortController();
      chatAbort.current = ctrl;
      const history: { role: "user" | "assistant"; content: string }[] = messages
        .filter((m) => !m.pending && !m.error && m.content)
        .map((m) => ({ role: m.role, content: m.content }));
      const outgoing: { role: "user" | "assistant"; content: string }[] = [...history, { role: "user", content: q }];
      setMessages((prev) => [...prev, { role: "user", content: q }, { role: "assistant", content: "", pending: true }]);
      setInput("");
      setStreaming(true);
      const patchLast = (f: (m: Msg) => Msg) =>
        setMessages((prev) => [...prev.slice(0, -1), f(prev[prev.length - 1])]);
      try {
        await streamChat(
          { day, bids, persona, scenario, messages: outgoing.slice(-20), focus_field: focusField },
          {
            onMeta: (meta) => patchLast((m) => ({ ...m, meta })),
            onToken: (t) => patchLast((m) => ({ ...m, content: m.content + t })),
            onSuggestions: (s) => patchLast((m) => ({ ...m, suggestions: s })),
            onError: (e) => patchLast((m) => ({ ...m, error: e })),
          },
          ctrl.signal,
        );
        patchLast((m) => ({ ...m, pending: false }));
      } catch (e) {
        if (ctrl.signal.aborted) return;
        patchLast((m) => ({
          ...m,
          pending: false,
          error: `Could not reach the assistant (${e instanceof Error ? e.message : "network error"}).`,
        }));
      } finally {
        setStreaming(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [bidsKey, messages, streaming],
  );

  // Field "?" icons in the bid cards.
  const lastHelp = useRef(0);
  useEffect(() => {
    if (!help || help.nonce === lastHelp.current) return;
    lastHelp.current = help.nonce;
    setOpen(true);
    void send(FIELD_QUESTION[help.field] ?? `What is ${help.field}?`, help.field);
  }, [help, send]);

  const apply = (s: Suggestion) => {
    onApply(s);
    setApplied((prev) => new Set(prev).add(sugKey(s)));
  };

  const liveOpen = live.filter((s) => !applied.has(sugKey(s)));

  if (!ASSISTANT_AVAILABLE) return null;

  return (
    <>
      {!open && (
        <button
          type="button"
          onClick={() => setOpen(true)}
          data-testid="assistant-open"
          aria-label="Open Coordinator Guide"
          className="fixed right-5 z-40 flex items-center gap-2 rounded-full bg-accent py-2.5 pl-2.5 pr-4 text-sm font-medium text-white shadow-lg hover:bg-accent-ink"
          style={{ bottom: "calc(1.25rem + env(safe-area-inset-bottom, 0px))" }}
        >
          <Avatar small />
          Coordinator Guide
          {liveOpen.length > 0 && (
            <span className="num ml-1 rounded-full bg-white px-1.5 text-xs font-semibold text-accent" data-testid="assistant-badge">
              {liveOpen.length}
            </span>
          )}
        </button>
      )}
      {open && (
        <aside
          className="fixed right-0 top-0 z-40 flex h-full w-full flex-col border-l border-line bg-surface shadow-2xl sm:w-[360px]"
          style={{ paddingTop: "env(safe-area-inset-top, 0px)", paddingBottom: "env(safe-area-inset-bottom, 0px)" }}
          data-testid="assistant-panel"
          aria-label="Coordinator Guide"
        >
          <header className="flex items-center gap-2.5 border-b border-line px-4 py-3">
            <Avatar />
            <div className="min-w-0 flex-1">
              <div className="text-sm font-semibold text-ink">Coordinator Guide</div>
              <select
                aria-label="Persona"
                data-testid="assistant-persona"
                value={persona}
                onChange={(e) => setPersona(e.target.value as PersonaId)}
                className="mt-0.5 max-w-full rounded border border-line bg-surface px-1.5 py-0.5 text-xs text-ink-2"
              >
                {PERSONAS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.option}
                  </option>
                ))}
              </select>
            </div>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="rounded px-2 py-1 text-lg leading-none text-muted hover:bg-surface-2 hover:text-ink"
              aria-label="Close Coordinator Guide"
            >
              ×
            </button>
          </header>

          <CredibilityStrip status={status} failed={statusError} />

          <div ref={listRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3" data-testid="assistant-messages">
            <Bubble role="assistant">
              Hi! I explain every bid field and check your bids against {dayLabel} (
              <span className="num">{day}</span>). The forecast I use is served by{" "}
              {status ? (status.forecast === "chalk" ? "Chalk" : "the local model") : "…"}.
            </Bubble>
            {messages.length === 0 && (
              <div className="flex flex-wrap gap-1.5">
                {CHIPS.map((c) => (
                  <button
                    key={c}
                    type="button"
                    onClick={() => void send(c)}
                    className="rounded-full border border-line-strong px-2.5 py-1 text-xs text-ink-2 hover:border-accent hover:bg-accent-soft"
                  >
                    {c}
                  </button>
                ))}
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className="space-y-2">
                <Bubble role={m.role}>
                  {m.content || (m.pending ? <span className="text-muted">thinking…</span> : null)}
                </Bubble>
                {m.meta && (
                  <p className="text-[10px] text-muted" data-testid="assistant-answer-source">
                    {m.meta.llm === "chalk" ? `answered by ${m.meta.model} via Chalk Model Gateway` : "answered by the local rule-based assistant"}
                    {m.meta.fallback_reason ? ` (${m.meta.fallback_reason})` : ""}
                  </p>
                )}
                {m.error && <p className="text-xs text-danger">{m.error}</p>}
                {m.suggestions?.map((s) => (
                  <SuggestionCard key={sugKey(s)} s={s} name={nameOf(s.bid_id)} applied={applied.has(sugKey(s))} onApply={() => apply(s)} />
                ))}
              </div>
            ))}
          </div>

          {liveOpen.length > 0 && (
            <div className="max-h-[40%] space-y-2 overflow-y-auto border-t border-line bg-surface-2 px-4 py-3" data-testid="assistant-live">
              <div className="text-[11px] font-medium uppercase tracking-wider text-muted">Live suggestions</div>
              {liveOpen.map((s) => (
                <SuggestionCard key={sugKey(s)} s={s} name={nameOf(s.bid_id)} applied={false} onApply={() => apply(s)} />
              ))}
            </div>
          )}

          <form
            className="flex gap-2 border-t border-line px-3 py-3"
            onSubmit={(e) => {
              e.preventDefault();
              void send(input);
            }}
          >
            <input
              aria-label="Ask Coordinator Guide"
              data-testid="assistant-input"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="Ask about your bids…"
              maxLength={2000}
              className="min-w-0 flex-1 rounded-md border border-line-strong bg-surface px-2.5 py-2 text-sm text-ink outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={streaming || !input.trim()}
              className="rounded-md bg-accent px-3 text-sm font-medium text-white hover:bg-accent-ink disabled:opacity-50"
            >
              Send
            </button>
          </form>
        </aside>
      )}
    </>
  );
}

function Avatar({ small = false }: { small?: boolean }) {
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full font-semibold ${
        small ? "h-6 w-6 bg-white text-[10px] text-accent" : "h-9 w-9 bg-accent text-xs text-white"
      }`}
    >
      CG
    </span>
  );
}

function Bubble({ role, children }: { role: "user" | "assistant"; children: React.ReactNode }) {
  return (
    <div className={role === "user" ? "flex justify-end" : "flex"}>
      <div
        className={`max-w-[88%] whitespace-pre-wrap rounded-lg px-3 py-2 text-sm leading-relaxed ${
          role === "user" ? "bg-accent text-white" : "bg-surface-2 text-ink"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

function SuggestionCard({ s, name, applied, onApply }: { s: Suggestion; name: string; applied: boolean; onApply: () => void }) {
  return (
    <div className="rounded-md border border-line bg-surface p-2.5" data-testid="assistant-suggestion">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 text-xs">
          <span className={`mr-1.5 rounded px-1 py-px text-[10px] font-semibold uppercase ${s.kind === "fix" ? "bg-danger-soft text-danger" : "bg-accent-soft text-accent-ink"}`}>
            {s.kind}
          </span>
          <span className="font-medium text-ink">{name}</span>
          <span className="text-muted"> · {FIELD_LABEL[s.field]} </span>
          <span className="num text-ink-2">
            {fmtValue(s.field, s.from)} → {fmtValue(s.field, s.to)}
          </span>
        </div>
        <button
          type="button"
          onClick={onApply}
          disabled={applied}
          data-testid="assistant-apply"
          className="shrink-0 rounded bg-accent px-2 py-0.5 text-xs font-medium text-white hover:bg-accent-ink disabled:bg-surface-2 disabled:text-muted"
        >
          {applied ? "Applied" : "Apply"}
        </button>
      </div>
      {s.reason && <p className="mt-1 text-xs leading-relaxed text-ink-2">{s.reason}</p>}
    </div>
  );
}

function CredibilityStrip({ status, failed }: { status: AssistantStatus | null; failed: boolean }) {
  if (failed) return <p className="border-b border-line bg-warn-soft px-4 py-1.5 text-[11px] text-warn">Assistant status unavailable (backend not reachable).</p>;
  if (!status) return <p className="border-b border-line px-4 py-1.5 text-[11px] text-muted">Checking assistant sources…</p>;
  const llm = status.gateway === "chalk" ? `Chalk Model Gateway (${status.model})` : "local rule-based";
  const extract = status.extract === "chalk_function" ? "Chalk function" : status.extract === "local" ? "local" : "off";
  return (
    <p className="border-b border-line bg-surface-2 px-4 py-1.5 font-mono text-[10.5px] leading-relaxed text-muted" data-testid="assistant-strip" title={status.gateway_reason ?? undefined}>
      LLM: <span className="text-ink-2">{llm}</span> · Forecast: <span className="text-ink-2">{status.forecast}</span> · Extract:{" "}
      <span className="text-ink-2">{extract}</span>
    </p>
  );
}
