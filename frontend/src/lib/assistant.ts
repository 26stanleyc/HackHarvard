/**
 * Client for the Coordinator Guide assistant (backend/clearinghouse/assistant, /api/assistant/*).
 *
 * The chat streams server-sent events over a POST, so it is read with fetch + ReadableStream
 * (EventSource only does GET). Events: meta (which path serves: chalk / local), token,
 * suggestions, error, done. The assistant needs the live backend; in fixture mode it is off.
 */

import { API_BASE, API_MODE } from "./api";
import type { BidDraft } from "./bids";

export type PersonaId = "ev_fleet" | "data_center" | "facilities";

export const PERSONAS: { id: PersonaId; label: string; option: string }[] = [
  { id: "ev_fleet", label: "EV fleet manager", option: "I run an EV fleet" },
  { id: "data_center", label: "Data-center ops lead", option: "I run data-center jobs" },
  { id: "facilities", label: "Facilities manager", option: "I manage buildings" },
];

export type BidField = "earliest_hour" | "deadline_hour" | "energy_mwh" | "max_mw";

export interface Suggestion {
  bid_id: string;
  field: BidField;
  from: number;
  to: number;
  reason: string;
  kind: "fix" | "improve";
}

export interface AssistantMeta {
  llm: "chalk" | "local";
  model: string;
  fallback_reason: string | null;
  forecast: "chalk" | "local";
  bid_features: "chalk" | "local" | null;
}

export interface AssistantStatus {
  gateway: "chalk" | "unavailable";
  gateway_reason: string | null;
  model: string | null;
  forecast: "chalk" | "local";
  extract: "chalk_function" | "gateway" | "unavailable";
  extract_reason?: string | null;
}

export interface AssistantScenario {
  carbon_price: number | null;
  pool_usd: number | null;
  background_pct: number | null;
}

/** Lenient bid for the assistant: unparsable numbers become null (the assistant explains them). */
export interface AssistantBid {
  id: string;
  name: string;
  load_type: string;
  energy_mwh: number | null;
  earliest_hour: number;
  deadline_hour: number;
  max_mw: number | null;
}

function numOrNull(s: string): number | null {
  const t = s.trim();
  if (t === "") return null;
  const v = Number(t);
  return Number.isFinite(v) ? v : null;
}

export function draftToAssistantBid(d: BidDraft): AssistantBid {
  return {
    id: d.id,
    name: d.name.trim(),
    load_type: d.load_type,
    energy_mwh: numOrNull(d.energy_mwh),
    earliest_hour: d.earliest_hour,
    deadline_hour: d.deadline_hour,
    max_mw: numOrNull(d.max_mw),
  };
}

/** The draft patch that applies a suggestion (numbers in drafts are strings). */
export function suggestionPatch(s: Suggestion): Partial<BidDraft> {
  if (s.field === "energy_mwh") return { energy_mwh: String(s.to) };
  if (s.field === "max_mw") return { max_mw: String(s.to) };
  if (s.field === "earliest_hour") return { earliest_hour: s.to };
  return { deadline_hour: s.to };
}

export const FIELD_LABEL: Record<BidField, string> = {
  earliest_hour: "Earliest",
  deadline_hour: "Deadline",
  energy_mwh: "Energy",
  max_mw: "Max power",
};

export const ASSISTANT_AVAILABLE = API_MODE !== "fixture";

export async function getAssistantStatus(): Promise<AssistantStatus> {
  const r = await fetch(`${API_BASE}/api/assistant/status`);
  if (!r.ok) throw new Error(`status HTTP ${r.status}`);
  return (await r.json()) as AssistantStatus;
}

export async function postSuggest(
  body: { day: string; bids: AssistantBid[]; persona: PersonaId; scenario: AssistantScenario },
  signal?: AbortSignal,
): Promise<{ suggestions: Suggestion[]; source: string; bid_features: string | null }> {
  const r = await fetch(`${API_BASE}/api/assistant/suggest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok) throw new Error(`suggest HTTP ${r.status}`);
  return r.json();
}

export interface ChatHandlers {
  onMeta?: (m: AssistantMeta) => void;
  onToken?: (t: string) => void;
  onSuggestions?: (s: Suggestion[], source: string) => void;
  onError?: (message: string) => void;
}

/** POST /api/assistant/chat and dispatch its SSE frames as they arrive. */
export async function streamChat(
  body: {
    day: string;
    bids: AssistantBid[];
    persona: PersonaId;
    scenario: AssistantScenario;
    messages: { role: "user" | "assistant"; content: string }[];
    focus_field?: string | null;
  },
  h: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(`${API_BASE}/api/assistant/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!r.ok || !r.body) throw new Error(`chat HTTP ${r.status}`);
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let i: number;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      let event = "message";
      let data = "";
      for (const line of frame.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7);
        else if (line.startsWith("data: ")) data += line.slice(6);
      }
      let payload: Record<string, unknown> = {};
      try {
        payload = data ? JSON.parse(data) : {};
      } catch {
        continue;
      }
      if (event === "meta") h.onMeta?.(payload as unknown as AssistantMeta);
      else if (event === "token") h.onToken?.(String(payload.t ?? ""));
      else if (event === "suggestions")
        h.onSuggestions?.((payload.suggestions as Suggestion[]) ?? [], String(payload.source ?? ""));
      else if (event === "error") h.onError?.(String(payload.message ?? "assistant error"));
    }
  }
}

// --- POST /api/assistant/extract ---------------------------------------------

export interface ExtractedBid {
  earliest_hour: number;
  deadline_hour: number;
  energy_mwh: number;
  max_mw: number;
  /** 0..1, the model's own estimate */
  confidence: number;
  notes: string;
  feasible: boolean;
  model?: string;
}

export interface ExtractResponse {
  bid: ExtractedBid;
  /** chalk_function: the Chalk Function extract-bid ran it; gateway: direct to the Model Gateway */
  path: "chalk_function" | "gateway";
  fallback_reason: string | null;
  latency_ms: number;
}

export const EXTRACT_TYPES = ["image/png", "image/jpeg", "image/webp", "image/gif"];
export const EXTRACT_MAX_BYTES = 5 * 1024 * 1024;

/** Upload one image; throws Error with the backend's message on 422 / 503. */
export async function postExtract(file: File, signal?: AbortSignal): Promise<ExtractResponse> {
  const form = new FormData();
  form.append("file", file);
  const r = await fetch(`${API_BASE}/api/assistant/extract`, { method: "POST", body: form, signal });
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = body?.detail;
    const msg = Array.isArray(d) ? d.map((x: { message?: string }) => x.message).join("; ") : d;
    throw new Error(typeof msg === "string" && msg ? msg : `extract HTTP ${r.status}`);
  }
  return body as ExtractResponse;
}
