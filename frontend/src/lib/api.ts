/**
 * API client for the Carbon Clearinghouse backend (docs/design.md, B6).
 *
 * Two modes, chosen at build time:
 *   NEXT_PUBLIC_API_MODE = "live" (default) | "fixture"
 *   NEXT_PUBLIC_API_BASE = backend origin for live mode (default http://localhost:8000)
 *
 * Fixture mode serves the JSON files in src/fixtures/ (real backend responses recorded for the
 * example scenario), which scripts/sync-fixtures.mjs copies from the repo's top-level fixtures/
 * before every dev/build run. It is always badged in the UI.
 */

import adoptionFixture from "@/fixtures/adoption_chart.json";
import clearRequestFixture from "@/fixtures/clear_request.json";
import clearResponseFixture from "@/fixtures/clear_response.json";
import evaluateResponseFixture from "@/fixtures/evaluate_response.json";
import metaFixture from "@/fixtures/meta.json";
import presetsFixture from "@/fixtures/presets.json";
import type {
  AdoptionChartResponse,
  Bid,
  BidError,
  ClearRequest,
  ClearResponse,
  EvaluateRequest,
  EvaluateResponse,
  MetaResponse,
  PresetsResponse,
} from "./types";

export type ApiMode = "fixture" | "live";

export const API_MODE: ApiMode =
  process.env.NEXT_PUBLIC_API_MODE === "fixture" ? "fixture" : "live";

export const API_BASE: string = (
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"
).replace(/\/+$/, "");

/** The request that produced fixtures/clear_response.json (the example scenario). */
export const EXAMPLE_CLEAR_REQUEST: ClearRequest = asType<ClearRequest>(clearRequestFixture);

export type ApiErrorKind = "network" | "timeout" | "server" | "validation" | "http" | "parse";

/** Typed error for every failure path of a live request. */
export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  /** Per-bid messages from a 422 response (bid_id is null when not attributable). */
  readonly bidErrors: { bid_id: string | null; message: string }[];

  constructor(
    kind: ApiErrorKind,
    message: string,
    status: number | null = null,
    bidErrors: { bid_id: string | null; message: string }[] = [],
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
    this.bidErrors = bidErrors;
  }
}

function asType<T>(json: unknown): T {
  return json as T;
}

/** Deep copy so callers can never mutate the shared fixture objects. */
function clone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T;
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

const TIMEOUT_MS = {
  get: 10_000,
  clear: 30_000,
  evaluate: 10_000,
} as const;

// --- 422 parsing ------------------------------------------------------------

type Loc = (string | number)[];

function isRecord(x: unknown): x is Record<string, unknown> {
  return typeof x === "object" && x !== null && !Array.isArray(x);
}

/**
 * FastAPI may return either our custom shape `{detail: [{bid_id, message}]}` or the
 * standard pydantic list `{detail: [{loc, msg, type}]}` (or a plain string). Map both
 * to per-bid messages, using the request's bids to resolve `["body","bids",i,...]`.
 */
export function parseValidationDetail(
  body: unknown,
  bids: Bid[] = [],
): { bid_id: string | null; message: string }[] {
  const detail = isRecord(body) && "detail" in body ? body.detail : body;
  if (typeof detail === "string") return [{ bid_id: null, message: detail }];
  const items: unknown[] = Array.isArray(detail)
    ? detail
    : isRecord(detail) && Array.isArray(detail.errors)
      ? detail.errors
      : isRecord(detail)
        ? [detail]
        : [];
  const out: { bid_id: string | null; message: string }[] = [];
  for (const item of items) {
    if (!isRecord(item)) {
      out.push({ bid_id: null, message: String(item) });
      continue;
    }
    if (typeof item.message === "string") {
      out.push({
        bid_id: typeof item.bid_id === "string" ? item.bid_id : null,
        message: item.message,
      });
      continue;
    }
    const msg = typeof item.msg === "string" ? item.msg : JSON.stringify(item);
    const loc: Loc = Array.isArray(item.loc) ? (item.loc as Loc) : [];
    let bidId: string | null = null;
    let field = "";
    const i = loc.findIndex((p) => p === "bids" || p === "schedule");
    if (i >= 0 && loc[i] === "bids" && typeof loc[i + 1] === "number") {
      bidId = bids[loc[i + 1] as number]?.id ?? `bid #${(loc[i + 1] as number) + 1}`;
      field = loc.slice(i + 2).join(".");
    } else if (i >= 0 && loc[i] === "schedule" && typeof loc[i + 1] === "string") {
      bidId = loc[i + 1] as string;
      field = loc.slice(i + 2).join(".");
    } else {
      field = loc.filter((p) => p !== "body").join(".");
    }
    out.push({ bid_id: bidId, message: field ? `${field}: ${msg}` : msg });
  }
  return out.length ? out : [{ bid_id: null, message: "The request was rejected (422)." }];
}

// --- live transport ---------------------------------------------------------

async function request<T>(
  path: string,
  init: RequestInit & { timeoutMs: number; bids?: Bid[] },
): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init.timeoutMs);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
    });
  } catch (e) {
    clearTimeout(timer);
    if (ctrl.signal.aborted) {
      throw new ApiError(
        "timeout",
        `The backend did not answer within ${Math.round(init.timeoutMs / 1000)} s (${path}).`,
      );
    }
    throw new ApiError(
      "network",
      `Could not reach the backend at ${API_BASE} (${e instanceof Error ? e.message : "network error"}).`,
    );
  }
  clearTimeout(timer);

  let body: unknown = null;
  const text = await res.text().catch(() => "");
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (res.status === 422) {
    const bidErrors = parseValidationDetail(body, init.bids);
    throw new ApiError(
      "validation",
      "The backend rejected some bids. Fix the highlighted companies and run again.",
      422,
      bidErrors,
    );
  }
  if (res.status >= 500) {
    throw new ApiError(
      "server",
      `The backend failed with HTTP ${res.status}${typeof body === "string" && body ? `: ${body.slice(0, 200)}` : ""}.`,
      res.status,
    );
  }
  if (!res.ok) {
    const detail = isRecord(body) && typeof body.detail === "string" ? `: ${body.detail}` : "";
    throw new ApiError("http", `HTTP ${res.status} from ${path}${detail}.`, res.status);
  }
  if (body === null || typeof body === "string") {
    throw new ApiError("parse", `Expected JSON from ${path}.`, res.status);
  }
  return body as T;
}

// --- public API -------------------------------------------------------------

export async function getMeta(): Promise<MetaResponse> {
  if (API_MODE === "fixture") return clone(asType<MetaResponse>(metaFixture));
  return request<MetaResponse>("/api/meta", { method: "GET", timeoutMs: TIMEOUT_MS.get });
}

export async function getPresets(): Promise<PresetsResponse> {
  if (API_MODE === "fixture") return clone(asType<PresetsResponse>(presetsFixture));
  return request<PresetsResponse>("/api/presets", { method: "GET", timeoutMs: TIMEOUT_MS.get });
}

export async function getAdoptionChart(): Promise<AdoptionChartResponse> {
  if (API_MODE === "fixture") return clone(asType<AdoptionChartResponse>(adoptionFixture));
  return request<AdoptionChartResponse>("/api/adoption-chart", {
    method: "GET",
    timeoutMs: TIMEOUT_MS.get,
  });
}

/**
 * POST /api/clear. In fixture mode the recorded response for the example scenario
 * (fixtures/clear_request.json) is returned whatever the request; callers should
 * display it against EXAMPLE_CLEAR_REQUEST's bids.
 */
export async function postClear(req: ClearRequest): Promise<ClearResponse> {
  if (API_MODE === "fixture") {
    await delay(450); // makes the loading state visible, like a real clear
    return clone(asType<ClearResponse>(clearResponseFixture));
  }
  return request<ClearResponse>("/api/clear", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: TIMEOUT_MS.clear,
    bids: req.bids,
  });
}

/** POST /api/evaluate. Fixture mode returns the recorded score of the flat hand schedule. */
export async function postEvaluate(req: EvaluateRequest): Promise<EvaluateResponse> {
  if (API_MODE === "fixture") {
    await delay(250);
    return clone(asType<EvaluateResponse>(evaluateResponseFixture));
  }
  return request<EvaluateResponse>("/api/evaluate", {
    method: "POST",
    body: JSON.stringify(req),
    timeoutMs: TIMEOUT_MS.evaluate,
    bids: req.bids,
  });
}

export type { BidError };
