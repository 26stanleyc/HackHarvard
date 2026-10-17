"use client";

import { useMemo } from "react";
import { Bar, BarChart, CartesianGrid, Tooltip, XAxis, YAxis } from "recharts";
import { fmtInt, slotLabel } from "@/lib/format";
import { marginalFill, marginalLabel } from "@/lib/theme";
import type { MarginalResource } from "@/lib/types";

export interface ChartCompany {
  id: string;
  name: string;
  color: string;
}

function toHour(idx: unknown): number | null {
  const n = typeof idx === "number" ? idx : typeof idx === "string" ? Number(idx) : NaN;
  return Number.isInteger(n) && n >= 0 && n < 24 ? n : null;
}

/** 24-hour stacked load chart: per-company MW stacked by hour. */
export function LoadChart({
  companies,
  schedule,
  marginal,
  hourlyCo2,
  yMax,
  animate,
  onSelectHour,
  selectedHour,
  marginalCaption,
}: {
  companies: ChartCompany[];
  schedule: Record<string, number[]>;
  marginal: MarginalResource[] | null;
  hourlyCo2?: number[] | null;
  yMax: number;
  animate: boolean;
  selectedHour: number | null;
  onSelectHour: (h: number | null) => void;
  marginalCaption: string;
}) {
  const rows = useMemo(
    () =>
      Array.from({ length: 24 }, (_, h) => {
        const row: Record<string, number> = { hour: h };
        let total = 0;
        for (const c of companies) {
          const v = schedule[c.id]?.[h] ?? 0;
          row[c.id] = v;
          total += v;
        }
        row.total = total;
        return row;
      }),
    [companies, schedule],
  );

  return (
    <div>
      <div className="relative">
        <div className="mb-1 pl-1 font-mono text-[10px] text-muted">MW, company load (stacked)</div>
        <BarChart
          responsive
          style={{ width: "100%", height: 300 }}
          data={rows}
          margin={{ top: 8, right: 4, bottom: 0, left: 0 }}
          barCategoryGap={2}
          onClick={(state) => {
            const n = toHour(state?.activeTooltipIndex);
            if (n !== null) onSelectHour(n);
          }}
          onMouseMove={(state) => {
            const n = toHour(state?.activeTooltipIndex);
            if (n !== null && n !== selectedHour) onSelectHour(n);
          }}
        >
          <CartesianGrid stroke="#e1dfd6" vertical={false} />
          <XAxis
            dataKey="hour"
            tickFormatter={(h: number) => String(h).padStart(2, "0")}
            interval={1}
            tick={{ fontSize: 10, fill: "#74776f" }}
            tickLine={false}
            axisLine={{ stroke: "#c9c6ba" }}
          />
          <YAxis
            width={48}
            domain={[0, yMax]}
            allowDataOverflow
            tickFormatter={(v: number) => fmtInt(v)}
            tick={{ fontSize: 10, fill: "#74776f" }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip
            cursor={{ fill: "rgba(21,25,26,0.05)" }}
            content={({ active, label }) => {
              if (!active || label === undefined) return null;
              const h = Number(label);
              const row = rows[h];
              if (!row) return null;
              return (
                <HourTooltip
                  hour={h}
                  companies={companies}
                  row={row}
                  marginal={marginal?.[h] ?? null}
                  co2={hourlyCo2?.[h]}
                />
              );
            }}
          />
          {companies.map((c) => (
            <Bar
              key={c.id}
              dataKey={c.id}
              name={c.name}
              stackId="load"
              fill={c.color}
              stroke="#fdfdfb"
              strokeWidth={1}
              isAnimationActive={animate}
              animationDuration={350}
            />
          ))}
        </BarChart>
      </div>
      {marginal && (
        <MarginalStrip
          marginal={marginal}
          selectedHour={selectedHour}
          onSelectHour={onSelectHour}
          caption={marginalCaption}
        />
      )}
    </div>
  );
}

function HourTooltip({
  hour,
  companies,
  row,
  marginal,
  co2,
}: {
  hour: number;
  companies: ChartCompany[];
  row: Record<string, number>;
  marginal: MarginalResource | null;
  co2?: number;
}) {
  return (
    <div className="max-w-[280px] rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-md">
      <div className="num mb-1 font-medium text-ink">{slotLabel(hour)}</div>
      {marginal && <MarginalLine m={marginal} />}
      <div className="mt-1.5 space-y-0.5 border-t border-line pt-1.5">
        {companies.map((c) =>
          (row[c.id] ?? 0) > 0 ? (
            <div key={c.id} className="flex items-center justify-between gap-3">
              <span className="flex min-w-0 items-center gap-1.5 text-ink-2">
                <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: c.color }} />
                <span className="truncate">{c.name}</span>
              </span>
              <span className="num whitespace-nowrap text-ink">{fmtInt(row[c.id])} MW</span>
            </div>
          ) : null,
        )}
        <div className="flex justify-between gap-3 text-muted">
          <span>Total companies</span>
          <span className="num">{fmtInt(row.total ?? 0)} MW</span>
        </div>
        {co2 !== undefined && (
          <div className="flex justify-between gap-3 text-muted">
            <span>Grid CO2 this hour</span>
            <span className="num">{fmtInt(co2)} t</span>
          </div>
        )}
      </div>
    </div>
  );
}

export function MarginalLine({ m }: { m: MarginalResource }) {
  return (
    <div className="text-ink-2">
      <div className="text-[10px] uppercase tracking-wider text-muted">Plant on the margin</div>
      {m.kind === "curtailed_renewables" ? (
        <div className="font-medium text-accent">Curtailed renewables (0 t/MWh){m.fuel ? ` · ${m.fuel}` : ""}</div>
      ) : (
        <div>
          <span className="font-medium text-ink">{m.plant_name ?? "Unnamed plant"}</span>
          <span className="text-muted"> · {m.fuel ?? "fossil"} · </span>
          <span className="num">{m.co2_t_per_mwh.toFixed(2)} t/MWh</span>
        </div>
      )}
    </div>
  );
}

/**
 * Plant-on-the-margin strip: one cell per hour, shaded by the marginal CO2 rate
 * (one hue, light to dark), green-tinted for curtailed renewables. Tap or hover a
 * cell for the plant.
 */
export function MarginalStrip({
  marginal,
  selectedHour,
  onSelectHour,
  caption,
  inset = true,
}: {
  marginal: MarginalResource[];
  selectedHour: number | null;
  onSelectHour: (h: number | null) => void;
  caption: string;
  /** indent to line up with a chart's y-axis */
  inset?: boolean;
}) {
  const sel = selectedHour !== null ? marginal[selectedHour] : null;
  return (
    <div className={`mt-2 ${inset ? "pl-[48px] pr-1" : ""}`}>
      <div className="mb-1 flex items-baseline justify-between gap-2 text-[10px] uppercase tracking-wider text-muted">
        <span>Plant on the margin, by hour</span>
        <span className="hidden normal-case tracking-normal sm:inline">tap or hover an hour</span>
      </div>
      <div
        className="grid gap-px"
        style={{ gridTemplateColumns: "repeat(24, minmax(0, 1fr))" }}
        role="listbox"
        aria-label="Marginal resource by hour"
      >
        {marginal.map((m, h) => (
          <button
            key={h}
            type="button"
            role="option"
            aria-selected={selectedHour === h}
            title={`${slotLabel(h)} · ${marginalLabel(m)}`}
            data-testid={`marginal-${h}`}
            onClick={() => onSelectHour(selectedHour === h ? null : h)}
            onMouseEnter={() => onSelectHour(h)}
            className={`h-5 rounded-[2px] ${selectedHour === h ? "ring-2 ring-ink ring-offset-1 ring-offset-surface" : ""}`}
            style={{ background: marginalFill(m) }}
          >
            <span className="sr-only">
              {slotLabel(h)}: {marginalLabel(m)}
            </span>
          </button>
        ))}
      </div>
      <div className="mt-2 min-h-[44px] rounded-md border border-line bg-surface-2/60 px-3 py-2 text-xs" aria-live="polite" data-testid="marginal-detail">
        {sel && selectedHour !== null ? (
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="num font-medium text-ink">{slotLabel(selectedHour)}</span>
            <MarginalLine m={sel} />
          </div>
        ) : (
          <span className="text-muted">
            Select an hour to see the real plant your extra megawatt would turn on.
          </span>
        )}
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-muted">
        <span className="flex items-center gap-1">
          <span className="h-2.5 w-3 rounded-[2px]" style={{ background: "#d8eadf" }} /> curtailed renewables
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2.5 w-3 rounded-[2px]" style={{ background: "#cfc9b8" }} /> lower CO2 rate
        </span>
        <span className="flex items-center gap-1">
          <span className="h-2.5 w-3 rounded-[2px]" style={{ background: "#3b3528" }} /> higher CO2 rate
        </span>
        <span className="font-mono">{caption}</span>
      </div>
    </div>
  );
}
