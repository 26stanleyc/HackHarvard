"use client";

import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { fmtInt, hh, windowLabel } from "@/lib/format";
import type { Bid } from "@/lib/types";

const EPS = 1e-9;

/** Snap step: 5% of max power (at least 1 MW). */
function stepOf(bid: Bid): number {
  return Math.max(1, bid.max_mw / 20);
}

/**
 * Set hour h to `target` MW, snapped to the step, clamped to [0, max_mw] and to the
 * energy still unassigned, and forced to 0 outside the bid's window.
 */
export function setHour(bid: Bid, mw: number[], h: number, target: number): number[] {
  if (h < bid.earliest_hour || h >= bid.deadline_hour) return mw;
  const step = stepOf(bid);
  const others = mw.reduce((a, v, i) => (i === h ? a : a + v), 0);
  const room = Math.max(0, bid.energy_mwh - others);
  let v = Math.round(target / step) * step;
  v = Math.max(0, Math.min(bid.max_mw, v));
  if (v > room) v = room; // lets the last block land exactly on the energy target
  if (Math.abs(v - mw[h]) < EPS) return mw;
  const next = mw.slice();
  next[h] = v;
  return next;
}

/** Paint-by-drag grid for one company: 24 columns, drag up/down to set MW per hour. */
export function ScheduleGrid({
  bid,
  color,
  mw,
  onUpdate,
  error,
}: {
  bid: Bid;
  color: string;
  mw: number[];
  onUpdate: (f: (prev: number[]) => number[]) => void;
  error?: string | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);

  const total = mw.reduce((a, b) => a + b, 0);
  const remaining = bid.energy_mwh - total;
  const done = Math.abs(remaining) <= Math.max(1e-6, bid.energy_mwh * 1e-3);

  const paint = (e: PointerEvent<HTMLDivElement>) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const col = Math.floor(((e.clientX - r.left) / r.width) * 24);
    if (col < 0 || col > 23) return;
    const frac = Math.max(0, Math.min(1, 1 - (e.clientY - r.top) / r.height));
    const target = frac < 0.06 ? 0 : frac > 0.94 ? bid.max_mw : frac * bid.max_mw;
    onUpdate((prev) => setHour(bid, prev, col, target));
  };

  const onKey = (h: number) => (e: KeyboardEvent<HTMLDivElement>) => {
    const step = stepOf(bid);
    let delta: number | null = null;
    if (e.key === "ArrowUp" || e.key === "ArrowRight") delta = step;
    if (e.key === "ArrowDown" || e.key === "ArrowLeft") delta = -step;
    if (e.key === "Home") delta = -Infinity;
    if (e.key === "End") delta = Infinity;
    if (delta === null) return;
    e.preventDefault();
    const d = delta;
    onUpdate((prev) => setHour(bid, prev, h, Number.isFinite(d) ? prev[h] + d : d > 0 ? bid.max_mw : 0));
  };

  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-xs">
        <span className="flex min-w-0 items-center gap-1.5 font-medium text-ink">
          <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color }} aria-hidden />
          <span className="truncate">{bid.name}</span>
        </span>
        <span className="num text-muted">
          {windowLabel(bid.earliest_hour, bid.deadline_hour)} · max {fmtInt(bid.max_mw)} MW
        </span>
      </div>
      <div
        ref={ref}
        className="relative grid h-16 cursor-crosshair touch-none select-none gap-px rounded-sm bg-line/60 p-px sm:h-[72px]"
        style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}
        onPointerDown={(e) => {
          dragging.current = true;
          try {
            e.currentTarget.setPointerCapture(e.pointerId);
          } catch {
            // synthetic or already-released pointer; painting still works without capture
          }
          paint(e);
        }}
        onPointerMove={(e) => {
          if (dragging.current) paint(e);
        }}
        onPointerUp={(e) => {
          dragging.current = false;
          if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        }}
        onPointerCancel={() => {
          dragging.current = false;
        }}
        data-testid={`grid-${bid.id}`}
      >
        {mw.map((v, h) => {
          const inside = h >= bid.earliest_hour && h < bid.deadline_hour;
          const pct = bid.max_mw > 0 ? (v / bid.max_mw) * 100 : 0;
          return (
            <div
              key={h}
              role={inside ? "slider" : undefined}
              aria-label={inside ? `${bid.name}, ${hh(h)}` : undefined}
              aria-valuemin={inside ? 0 : undefined}
              aria-valuemax={inside ? bid.max_mw : undefined}
              aria-valuenow={inside ? v : undefined}
              aria-valuetext={inside ? `${fmtInt(v)} MW` : undefined}
              tabIndex={inside ? 0 : -1}
              onKeyDown={inside ? onKey(h) : undefined}
              className={`relative flex items-end rounded-[1px] ${inside ? "bg-surface" : "hatch bg-surface-2"}`}
              title={inside ? `${hh(h)} · ${fmtInt(v)} MW` : `${hh(h)} · outside window`}
            >
              {v > 0 && <div className="w-full rounded-t-[1px]" style={{ height: `${pct}%`, background: color }} />}
            </div>
          );
        })}
      </div>
      <div className="num mt-0.5 flex justify-between text-[10px] text-muted">
        <span>00</span>
        <span>06</span>
        <span>12</span>
        <span>18</span>
        <span>24</span>
      </div>
      <div className="mt-0.5 flex flex-wrap items-baseline justify-between gap-x-3 text-xs">
        <span className={done ? "text-accent" : remaining < 0 ? "text-danger" : "text-warn"} data-testid={`remaining-${bid.id}`}>
          {done ? (
            <>All {fmtInt(bid.energy_mwh)} MWh placed</>
          ) : (
            <>
              Remaining <span className="num font-medium">{fmtInt(remaining)}</span> of {fmtInt(bid.energy_mwh)} MWh
            </>
          )}
        </span>
        {error && <span className="text-danger" data-testid={`eval-error-${bid.id}`}>{error}</span>}
      </div>
    </div>
  );
}
