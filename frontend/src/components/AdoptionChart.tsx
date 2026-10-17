"use client";

import { useMemo, useState } from "react";
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmtInt, fmtSignedT } from "@/lib/format";
import { STRATEGY_COLORS, STRATEGY_LABEL } from "@/lib/theme";
import { STRATEGY_NAMES, type AdoptionChartResponse, type StrategyName } from "@/lib/types";
import { Segmented } from "./ui";

type View = "total" | "delta";

type Row = { x: number } & Record<string, number | [number, number]>;

/** Total CO2 vs flexible load (% of peak), four strategies with variability bands. */
export function AdoptionChart({ data }: { data: AdoptionChartResponse }) {
  const [view, setView] = useState<View>("total");

  const rows = useMemo<Row[]>(() => {
    const def = data.strategies.default.mean_co2_t;
    return data.x_pct_of_peak.map((x, i) => {
      const base = view === "delta" ? def[i] : 0;
      const row: Row = { x };
      for (const s of STRATEGY_NAMES) {
        const ser = data.strategies[s];
        row[s] = ser.mean_co2_t[i] - base;
        row[`${s}_band`] = [ser.band_low[i] - base, ser.band_high[i] - base];
      }
      return row;
    });
  }, [data, view]);

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <Legend />
        <Segmented
          label="Y axis"
          value={view}
          onChange={setView}
          options={[
            { value: "total", label: "Total CO2" },
            { value: "delta", label: "Change vs default" },
          ]}
          testIdPrefix="adoption-view"
        />
      </div>
      <ComposedChart
        responsive
        style={{ width: "100%", height: 320 }}
        data={rows}
        margin={{ top: 8, right: 12, bottom: 22, left: 4 }}
      >
        <CartesianGrid stroke="#e1dfd6" strokeDasharray="0" vertical={false} />
        <XAxis
          dataKey="x"
          type="number"
          domain={[data.x_pct_of_peak[0] ?? 0, data.x_pct_of_peak[data.x_pct_of_peak.length - 1] ?? 30]}
          ticks={data.x_pct_of_peak.filter((_, i) => i % 2 === 0)}
          tickFormatter={(v: number) => `${v}%`}
          tick={{ fontSize: 11, fill: "#74776f" }}
          tickLine={false}
          axisLine={{ stroke: "#c9c6ba" }}
          label={{
            value: "Flexible load, % of regional peak",
            position: "insideBottom",
            offset: -14,
            fontSize: 11,
            fill: "#74776f",
          }}
        />
        <YAxis
          width={62}
          domain={["auto", "auto"]}
          tickFormatter={(v: number) =>
            view === "delta" ? fmtSignedT(v) : `${fmtInt(v / 1000)}k`
          }
          tick={{ fontSize: 11, fill: "#74776f" }}
          tickLine={false}
          axisLine={false}
          label={{
            value: view === "delta" ? "t CO2 vs default" : "t CO2 / day",
            angle: -90,
            position: "insideLeft",
            offset: 10,
            fontSize: 11,
            fill: "#74776f",
            style: { textAnchor: "middle" },
          }}
        />
        {STRATEGY_NAMES.map((s) => (
          <Area
            key={`${s}_band`}
            dataKey={`${s}_band`}
            stroke="none"
            fill={STRATEGY_COLORS[s]}
            fillOpacity={0.12}
            isAnimationActive={false}
            activeDot={false}
            legendType="none"
            tooltipType="none"
          />
        ))}
        {STRATEGY_NAMES.map((s) => (
          <Line
            key={s}
            dataKey={s}
            name={STRATEGY_LABEL[s]}
            stroke={STRATEGY_COLORS[s]}
            strokeWidth={2}
            strokeDasharray={s === "default" ? "5 4" : undefined}
            dot={false}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "#fdfdfb" }}
            isAnimationActive={false}
          />
        ))}
        <Tooltip
          cursor={{ stroke: "#c9c6ba", strokeWidth: 1 }}
          content={({ active, label }) => {
            if (!active || label === undefined) return null;
            const row = rows.find((r) => r.x === Number(label));
            if (!row) return null;
            return (
              <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-md">
                <div className="mb-1 font-medium text-ink">{label}% of peak flexible</div>
                {STRATEGY_NAMES.map((s) => {
                  const band = row[`${s}_band`] as [number, number];
                  const v = row[s] as number;
                  return (
                    <div key={s} className="flex items-center justify-between gap-4">
                      <span className="flex items-center gap-1.5 text-ink-2">
                        <Swatch s={s} />
                        {STRATEGY_LABEL[s]}
                      </span>
                      <span className="num text-ink">
                        {view === "delta" ? fmtSignedT(v) : fmtInt(v)} t
                        <span className="ml-1 text-muted">
                          ({view === "delta" ? fmtSignedT(band[0]) : fmtInt(band[0])}–
                          {view === "delta" ? fmtSignedT(band[1]) : fmtInt(band[1])})
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          }}
        />
      </ComposedChart>
    </div>
  );
}

function Swatch({ s }: { s: StrategyName }) {
  return (
    <svg width="16" height="8" aria-hidden className="shrink-0">
      <line
        x1="0"
        y1="4"
        x2="16"
        y2="4"
        stroke={STRATEGY_COLORS[s]}
        strokeWidth="2"
        strokeDasharray={s === "default" ? "4 3" : undefined}
      />
    </svg>
  );
}

function Legend() {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {STRATEGY_NAMES.map((s) => (
        <li key={s} className="flex items-center gap-1.5">
          <Swatch s={s} />
          {STRATEGY_LABEL[s]}
        </li>
      ))}
    </ul>
  );
}

/**
 * Data-driven reading of the chart (no hard-coded results): the first share at
 * which the average-signal strategy emits more than default, and the spread at
 * the largest share shown.
 */
export function adoptionReadout(data: AdoptionChartResponse) {
  const xs = data.x_pct_of_peak;
  const d = data.strategies.default.mean_co2_t;
  const ia = data.strategies.independent_average.mean_co2_t;
  const im = data.strategies.independent_marginal.mean_co2_t;
  const co = data.strategies.coordinated.mean_co2_t;
  let crossIdx = -1;
  for (let i = 0; i < xs.length; i++) {
    if (xs[i] > 0 && ia[i] > d[i]) {
      crossIdx = i;
      break;
    }
  }
  const last = xs.length - 1;
  return {
    crossesAt: crossIdx >= 0 ? xs[crossIdx] : null,
    maxX: xs[last],
    iaVsDefault: ia[last] - d[last],
    coVsDefault: co[last] - d[last],
    coVsIm: co[last] - im[last],
  };
}
