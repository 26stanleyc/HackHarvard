"use client";

import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, LabelList, Tooltip, XAxis, YAxis } from "recharts";
import { API_MODE } from "@/lib/api";
import { loadTypeLabel } from "@/lib/bids";
import { activeRuns, fmtInt, fmtSignedT, fmtT, fmtUsd, hh, windowLabel } from "@/lib/format";
import { STRATEGY_COLORS, STRATEGY_LABEL, STRATEGY_SHORT } from "@/lib/theme";
import { STRATEGY_NAMES, type Bid, type ClearRequest, type ClearResponse, type HerdingMetric } from "@/lib/types";
import type { ChartCompany } from "./LoadChart";
import { forecastSourceText } from "./StoryScreen";
import { SectionTitle, Source, Stat } from "./ui";

export function ResultsView({
  request,
  response,
  bids,
  companies,
  leftOut,
  setLeftOut,
}: {
  request: ClearRequest;
  response: ClearResponse;
  bids: Bid[];
  companies: ChartCompany[];
  leftOut: string | null;
  setLeftOut: (id: string | null) => void;
}) {
  return (
    <div className="space-y-8">
      <SectionTitle kicker="Step 4 · Results" title="What coordination saved, and who gets paid for it">
        Every number here comes from one clear: strategies scored on the actual day, the herding
        metric, and an exact Shapley settlement.
      </SectionTitle>
      <div className="grid gap-4 lg:grid-cols-2">
        <StrategyComparison response={response} />
        <HerdingCallout response={response} />
      </div>
      <Receipts
        request={request}
        response={response}
        bids={bids}
        companies={companies}
        leftOut={leftOut}
        setLeftOut={setLeftOut}
      />
    </div>
  );
}

function StrategyComparison({ response }: { response: ClearResponse }) {
  const def = response.strategies.default.total_co2_t;
  const rows = STRATEGY_NAMES.map((s) => ({
    s,
    name: STRATEGY_SHORT[s],
    avoided: def - response.strategies[s].total_co2_t,
    total: response.strategies[s].total_co2_t,
  }));
  const totals = rows.map((r) => r.total);
  const spreadPct = def !== 0 ? ((Math.max(...totals) - Math.min(...totals)) / def) * 100 : 0;
  return (
    <section className="card min-w-0 p-4 sm:p-5" data-testid="strategy-comparison">
      <div className="kicker mb-1">Strategy comparison</div>
      <h3 className="text-lg font-semibold text-ink">CO2 avoided vs default, by strategy</h3>
      <p className="mt-1 text-xs text-muted">
        Bars show tons avoided relative to Act 1. The four full-day totals (table) differ by
        only {spreadPct.toFixed(2)}% because the companies are small next to the whole grid, so
        the differences are the story.
      </p>
      <BarChart
        responsive
        layout="vertical"
        style={{ width: "100%", height: 200 }}
        data={rows}
        margin={{ top: 12, right: 64, bottom: 4, left: 0 }}
        barCategoryGap={10}
      >
        <CartesianGrid stroke="#e1dfd6" horizontal={false} />
        <XAxis
          type="number"
          tickFormatter={(v: number) => fmtInt(v)}
          tick={{ fontSize: 10, fill: "#74776f" }}
          tickLine={false}
          axisLine={{ stroke: "#c9c6ba" }}
          domain={[(min: number) => Math.min(0, min), "auto"]}
        />
        <YAxis
          type="category"
          dataKey="name"
          width={104}
          tick={{ fontSize: 11, fill: "#464b48" }}
          tickLine={false}
          axisLine={false}
        />
        <Tooltip
          cursor={{ fill: "rgba(21,25,26,0.05)" }}
          content={({ active, payload }) => {
            const p = payload?.[0]?.payload as (typeof rows)[number] | undefined;
            if (!active || !p) return null;
            return (
              <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-md">
                <div className="font-medium text-ink">{STRATEGY_LABEL[p.s]}</div>
                <div className="num text-ink-2">total {fmtInt(p.total)} t</div>
                <div className="num text-ink-2">{fmtSignedT(p.avoided)} t avoided vs default</div>
              </div>
            );
          }}
        />
        <Bar dataKey="avoided" radius={[0, 4, 4, 0]} isAnimationActive={false}>
          {rows.map((r) => (
            <Cell key={r.s} fill={STRATEGY_COLORS[r.s]} />
          ))}
          <LabelList
            dataKey="avoided"
            position="right"
            formatter={(v: unknown) => `${fmtT(Number(v))} t`}
            style={{ fontSize: 11, fill: "#15191a", fontFamily: "var(--font-mono)" }}
          />
        </Bar>
      </BarChart>
      <table className="mt-2 w-full text-xs">
        <thead>
          <tr className="text-left text-muted">
            <th className="py-1 font-medium">Strategy</th>
            <th className="py-1 text-right font-medium">Total (t)</th>
            <th className="py-1 pl-2 text-right font-medium">vs default</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.s} className="border-t border-line">
              <td className="py-1 text-ink-2">{STRATEGY_LABEL[r.s]}</td>
              <td className="num py-1 text-right text-ink">{fmtInt(r.total)}</td>
              <td className="num py-1 text-right text-ink">{r.s === "default" ? "—" : fmtSignedT(-r.avoided)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Source className="mt-2">strategies.*.total_co2_t · scored on actual EIA-930 day · forecast: {forecastSourceText(response.forecast)}</Source>
    </section>
  );
}

function HerdingRow({ label, m }: { label: string; m: HerdingMetric }) {
  const pct = m.predicted_t !== 0 ? (m.gap_t / m.predicted_t) * 100 : 0;
  const realizedShare = m.predicted_t > 0 ? Math.max(0, Math.min(1, m.realized_t / m.predicted_t)) : 0;
  return (
    <div className="space-y-2">
      <div className="text-sm font-medium text-ink">{label}</div>
      <div className="grid grid-cols-3 gap-2">
        <Stat label="Predicted" value={fmtT(m.predicted_t)} sub="t saved, sum of solo" />
        <Stat label="Realized" value={fmtT(m.realized_t)} sub="t saved, together" />
        <Stat label="Gap" value={fmtT(m.gap_t)} sub={`t · ${pct.toFixed(0)}% of predicted`} tone={m.gap_t > 0 ? "bad" : "default"} />
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-danger-soft" aria-hidden>
        <div className="h-full rounded-full bg-ink-2" style={{ width: `${realizedShare * 100}%` }} />
      </div>
    </div>
  );
}

function HerdingCallout({ response }: { response: ClearResponse }) {
  const h = response.herding;
  return (
    <section className="card min-w-0 space-y-5 p-4 sm:p-5" data-testid="herding-callout">
      <div>
        <div className="kicker mb-1">Herding gap</div>
        <h3 className="text-lg font-semibold text-ink">Solo promises don&apos;t add up</h3>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">
          &quot;Predicted&quot; adds up what each company would save versus default if it alone shifted
          while the others stayed put. &quot;Realized&quot; is what they save when they all shift at
          once. The difference is the herding gap: they crowd into the same hours and the extra
          load lands on fossil plants.
        </p>
      </div>
      <HerdingRow label={STRATEGY_LABEL.independent_average} m={h.independent_average} />
      <HerdingRow label={STRATEGY_LABEL.independent_marginal} m={h.independent_marginal} />
      <Source>herding.* from POST /api/clear · same simulator throughout · bar = realized as a share of predicted</Source>
    </section>
  );
}

function Receipts({
  request,
  response,
  bids,
  companies,
  leftOut,
  setLeftOut,
}: {
  request: ClearRequest;
  response: ClearResponse;
  bids: Bid[];
  companies: ChartCompany[];
  leftOut: string | null;
  setLeftOut: (id: string | null) => void;
}) {
  const st = response.settlement;
  const [note, setNote] = useState<string | null>(null);
  const loo = leftOut ? st.leave_one_out[leftOut] : undefined;
  const totalAvoided = loo ? loo.total_avoided_t : st.total_avoided_t;
  const per = new Map<string, { shapley_t: number; payout_usd: number }>();
  for (const p of loo ? loo.per_bid : st.per_bid) per.set(p.bid_id, p);
  const full = new Map(st.per_bid.map((p) => [p.bid_id, p]));
  const totalPaid = Array.from(per.values()).reduce((a, p) => a + p.payout_usd, 0);
  const nameOf = (id: string) => bids.find((b) => b.id === id)?.name ?? id;

  const toggle = (id: string) => {
    if (leftOut === id) {
      setLeftOut(null);
      setNote(null);
      return;
    }
    if (!st.leave_one_out[id]) {
      setNote(`No leave-one-out data was returned for ${nameOf(id)}.`);
      return;
    }
    if (leftOut) {
      setNote(
        `Leave-one-out data covers one company at a time, so ${nameOf(leftOut)} rejoined when ${nameOf(id)} left.`,
      );
    } else {
      setNote(null);
    }
    setLeftOut(id);
  };

  return (
    <section data-testid="receipts">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="kicker mb-1">Settlement</div>
          <h3 className="font-serif text-2xl font-semibold text-ink">Avoided-carbon receipts</h3>
          <p className="mt-2 inline-block rounded border border-line-strong bg-surface-2 px-2 py-1 text-xs font-medium text-ink-2" data-testid="operator-label">
            Operator / auditor view — in production each company sees only its own receipt
          </p>
          <p className="mt-2 max-w-2xl text-sm text-ink-2">
            Payouts are for what coordination adds beyond each company&apos;s best solo schedule.
            Toggle a company out of the coalition to see everyone else&apos;s share without it.
          </p>
        </div>
      </div>

      <div className="card mb-4 grid grid-cols-2 gap-4 p-4 sm:grid-cols-4" data-testid="settlement-summary">
        <Stat
          label={leftOut ? `Avoided without ${nameOf(leftOut)}` : "Avoided by coordination"}
          value={fmtT(totalAvoided)}
          sub={leftOut ? `t · full coalition ${fmtT(st.total_avoided_t)} t` : "t vs baseline"}
          tone={totalAvoided >= 0 ? "good" : "bad"}
          testId="total-avoided"
        />
        <Stat label="Paid out" value={fmtUsd(totalPaid)} sub={`of ${fmtUsd(st.pool_usd)} pool`} testId="total-paid" />
        <Stat label="Carbon price" value={`${fmtUsd(request.carbon_price)}/t`} sub={st.prorated ? "prorated to fit the pool" : "not prorated"} />
        <Stat
          label="Shapley method"
          value={st.method === "exact" ? "Exact" : "Monte Carlo"}
          sub={`${fmtInt(st.coalitions_evaluated)} coalitions evaluated`}
        />
      </div>

      {note && (
        <p className="mb-3 rounded-md border border-warn/30 bg-warn-soft px-3 py-2 text-xs text-warn" role="status">
          {note}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {bids.map((b) => {
          const idx = companies.findIndex((c) => c.id === b.id);
          const color = companies[idx]?.color ?? "#2a78d6";
          const out = leftOut === b.id;
          const p = per.get(b.id);
          const f = full.get(b.id);
          const sched = response.strategies.coordinated.schedule[b.id] ?? [];
          const runs = activeRuns(sched);
          return (
            <article
              key={b.id}
              className={`card flex min-w-0 flex-col p-4 ${out ? "opacity-70" : ""}`}
              data-testid={`receipt-${b.id}`}
            >
              <header className="mb-3 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color }} aria-hidden />
                    <span className="truncate font-medium text-ink">{b.name}</span>
                  </div>
                  <div className="mt-0.5 text-xs text-muted">
                    {loadTypeLabel(b.load_type)} · <span className="num">{b.id}</span>
                  </div>
                </div>
                <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-xs text-ink-2">
                  <input
                    type="checkbox"
                    role="switch"
                    checked={!out}
                    onChange={() => toggle(b.id)}
                    data-testid={`leave-toggle-${b.id}`}
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                  {out ? "Left" : "In coalition"}
                </label>
              </header>

              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
                <dt className="text-muted">Bid</dt>
                <dd className="num text-right text-ink-2">
                  <span className="whitespace-nowrap">
                    {fmtInt(b.energy_mwh)} MWh · ≤{fmtInt(b.max_mw)} MW
                  </span>{" "}
                  <span className="whitespace-nowrap">· {windowLabel(b.earliest_hour, b.deadline_hour)}</span>
                </dd>
                <dt className="text-muted">Assigned hours</dt>
                <dd className="num text-right text-ink-2">
                  {out
                    ? "own solo schedule"
                    : runs.length
                      ? runs.map((r, i) => (
                          <span key={r.start} className="whitespace-nowrap">
                            {hh(r.start)}–{hh(r.end)}
                            {i < runs.length - 1 ? ", " : ""}
                          </span>
                        ))
                      : "none"}
                </dd>
              </dl>

              <div className="my-3 border-t border-dashed border-line-strong" />

              {out ? (
                <div className="flex-1 text-xs leading-relaxed text-ink-2" data-testid={`receipt-left-${b.id}`}>
                  Left the coalition. It runs its own best solo schedule (independent marginal), so
                  it adds nothing beyond the baseline and is paid <span className="num font-medium">$0</span>.
                  The other receipts now show the game without it.
                </div>
              ) : (
                <div className="flex-1 space-y-2">
                  <div className="grid grid-cols-2 gap-3">
                    <Stat
                      label="Shapley share"
                      value={p ? fmtT(p.shapley_t) : "—"}
                      sub={
                        leftOut && p && f
                          ? `t · ${fmtSignedT(p.shapley_t - f.shapley_t)} vs full coalition`
                          : "t CO2 avoided"
                      }
                      tone={p && p.shapley_t < 0 ? "bad" : "default"}
                      testId={`shapley-${b.id}`}
                    />
                    <Stat
                      label="Payout"
                      value={p ? fmtUsd(p.payout_usd) : "—"}
                      sub={p && p.shapley_t < 0 ? "negative share" : `at ${fmtUsd(request.carbon_price)}/t`}
                      tone={p && p.payout_usd > 0 ? "good" : "muted"}
                      testId={`payout-${b.id}`}
                    />
                  </div>
                  {p && p.shapley_t < 0 && (
                    <p className="text-xs leading-relaxed text-ink-2">
                      Negative share: on the actual day this company&apos;s participation raised the
                      coalition&apos;s emissions slightly versus its solo schedule. Nobody is charged, so
                      it is paid $0.
                    </p>
                  )}
                  <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-xs">
                    <dt className="text-muted">vs default</dt>
                    <dd className="num text-right text-ink-2">
                      {f ? `${fmtT(f.vs_default_t)} t avoided` : "—"}
                    </dd>
                    <dt className="text-muted">vs independent average</dt>
                    <dd className="num text-right text-ink-2">
                      {f ? `${fmtT(f.vs_independent_average_t)} t avoided` : "—"}
                    </dd>
                  </dl>
                  {leftOut && (
                    <p className="text-[11px] text-muted">
                      Assigned hours, vs default and vs independent average are full-coalition figures.
                    </p>
                  )}
                </div>
              )}

              <footer className="mt-3 border-t border-line pt-2 font-mono text-[10px] leading-relaxed text-muted">
                baseline: {st.baseline} (what each company could do alone) · method: {st.method} ·{" "}
                {fmtUsd(request.carbon_price)}/t · pool {fmtUsd(st.pool_usd)} · prorated: {st.prorated ? "yes" : "no"}
              </footer>
            </article>
          );
        })}
      </div>
      <Source className="mt-3">
        settlement.per_bid{leftOut ? ` → settlement.leave_one_out.${leftOut} (no new request)` : ""} · settled after
        the fact on actual grid conditions · Shapley shares sum to the coalition&apos;s avoided CO2
        {API_MODE === "fixture" ? " · fixture values" : ""}
      </Source>
    </section>
  );
}
