"use client";

import { useEffect, useRef, useState } from "react";
import {
  ASSISTANT_AVAILABLE,
  EXTRACT_MAX_BYTES,
  EXTRACT_TYPES,
  postExtract,
  type ExtractResponse,
} from "@/lib/assistant";
import { fmtNum1, hh } from "@/lib/format";
import { Button, Source } from "./ui";

/**
 * "Upload a bill or chart": an image goes to POST /api/assistant/extract (the Chalk Function
 * extract-bid, or the Model Gateway directly), and the extracted bid is shown as a card. It is
 * never applied automatically: Add as company creates a new bid the user can still edit.
 */
export function ExtractDropzone({
  disabled,
  onAdd,
}: {
  disabled: boolean;
  onAdd: (bid: ExtractResponse["bid"], sourceName: string) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ExtractResponse | null>(null);
  const [added, setAdded] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  if (!ASSISTANT_AVAILABLE) return null;

  const run = async (f: File) => {
    setError(null);
    setResult(null);
    setAdded(false);
    if (!EXTRACT_TYPES.includes(f.type)) {
      setError("Use a PNG, JPEG, WebP or GIF image (screenshot a PDF page first).");
      return;
    }
    if (f.size > EXTRACT_MAX_BYTES) {
      setError(`That image is ${(f.size / 1e6).toFixed(1)} MB; the limit is 5 MB.`);
      return;
    }
    setFile(f);
    setPreview(URL.createObjectURL(f));
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    setBusy(true);
    try {
      setResult(await postExtract(f, ctrl.signal));
    } catch (e) {
      if (!ctrl.signal.aborted) setError(e instanceof Error ? e.message : "Extraction failed.");
    } finally {
      setBusy(false);
    }
  };

  const b = result?.bid;
  return (
    <div className="card p-4" data-testid="extract-dropzone">
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <div className="text-sm font-medium text-ink">Upload a bill or chart</div>
        <span className="text-xs text-muted">prefills a bid; you review it before it is added</span>
      </div>
      <div
        role="button"
        tabIndex={0}
        aria-disabled={disabled}
        onClick={() => !disabled && input.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !disabled) input.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const f = e.dataTransfer.files?.[0];
          if (f && !disabled) void run(f);
        }}
        className={`flex cursor-pointer items-center justify-center rounded-lg border border-dashed px-4 py-5 text-center text-sm ${
          over ? "border-accent bg-accent-soft" : "border-line-strong hover:border-accent hover:bg-accent-soft"
        } ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
      >
        <span className="text-ink-2">
          Drop a utility bill, load chart, charging schedule or emissions report here, or{" "}
          <span className="font-medium text-accent">choose an image</span>
        </span>
        <input
          ref={input}
          type="file"
          accept={EXTRACT_TYPES.join(",")}
          className="hidden"
          data-testid="extract-input"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void run(f);
            e.target.value = "";
          }}
        />
      </div>

      {busy && <p className="mt-3 text-xs text-muted">Reading {file?.name}… (Claude vision via Chalk, a few seconds)</p>}
      {error && (
        <p className="mt-3 text-xs font-medium text-danger" data-testid="extract-error">
          {error}
        </p>
      )}

      {b && result && (
        <div className="mt-3 flex gap-3 rounded-md border border-line bg-surface p-3" data-testid="extract-result">
          {preview && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={preview} alt={`Uploaded ${file?.name ?? "image"}`} className="h-24 w-24 shrink-0 rounded border border-line object-cover object-top" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="text-sm font-medium text-ink">Extracted bid</span>
              <span className="text-xs text-muted">
                confidence <span className="num text-ink-2">{Math.round(b.confidence * 100)}%</span>
              </span>
            </div>
            <div className="num mt-1 text-sm text-ink-2">
              {fmtNum1(b.energy_mwh)} MWh · up to {fmtNum1(b.max_mw)} MW · {hh(b.earliest_hour)}–{hh(b.deadline_hour)}
            </div>
            {b.notes && <p className="mt-1 text-xs leading-relaxed text-ink-2">{b.notes}</p>}
            {!b.feasible && (
              <p className="mt-1 text-xs font-medium text-warn">
                As extracted this bid is not feasible; you can fix it after adding it.
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Button onClick={() => { onAdd(b, file?.name ?? "upload"); setAdded(true); }} disabled={added || disabled} testId="extract-add" className="px-2.5 py-1 text-xs">
                {added ? "Added" : "Add as company"}
              </Button>
              <Source className="!mt-0">
                {result.path === "chalk_function" ? "Chalk Function extract-bid" : "Chalk Model Gateway (direct)"}
                {b.model ? ` · ${b.model}` : ""} · {(result.latency_ms / 1000).toFixed(1)} s
                {result.fallback_reason ? ` · fallback: ${result.fallback_reason}` : ""}
              </Source>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
