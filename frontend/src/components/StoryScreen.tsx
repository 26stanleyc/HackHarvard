"use client";

import adoptionMeta from "@/fixtures/adoption_chart_meta.json";
import { API_MODE } from "@/lib/api";
import { fmtInt, fmtSignedT, fmtT } from "@/lib/format";
import { STRATEGY_LABEL } from "@/lib/theme";
import type { AdoptionChartResponse, Calibration, Forecast, MetaResponse } from "@/lib/types";
import { AdoptionChart, adoptionReadout } from "./AdoptionChart";
import { Button, Source } from "./ui";

export function StoryScreen({
  meta,
  adoption,
  day,
  forecast,
  onNext,
}: {
  meta: MetaResponse;
  adoption: AdoptionChartResponse;
  day: string;
  forecast: Forecast | null;
  onNext: () => void;
}) {
  const r = adoptionReadout(adoption);
  const lower = (x: keyof typeof STRATEGY_LABEL) => STRATEGY_LABEL[x].toLowerCase();
  return (
    <div className="space-y-8">
      <header className="max-w-4xl pt-2">
        <div className="kicker mb-3">{meta.region} · day-ahead clearinghouse for flexible load</div>
        <h1 className="font-serif text-[26px] leading-[1.15] font-semibold tracking-tight text-ink sm:text-[36px] lg:text-[44px] lg:leading-[1.12]">
          When every flexible load chases the same clean hour, fossil plants serve the rush.
          A clearinghouse coordinates competing companies, and pays each only for the carbon
          coordination actually helped avoid.
        </h1>
      </header>

      <section className="card p-4 sm:p-6">
        <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
          <div>
            <div className="kicker">Adoption chart</div>
            <h3 className="mt-1 text-lg font-semibold text-ink">
              Total CO2 for the day as more of the region&apos;s load becomes flexible
            </h3>
          </div>
        </div>
        <AdoptionChart data={adoption} />
        <AdoptionCaption meta={meta} adoption={adoption} />
      </section>

      <section className="grid gap-4 md:grid-cols-3">
        <div className="card p-5 md:col-span-2">
          <div className="kicker mb-2">The herding gap</div>
          <p className="text-[15px] leading-relaxed text-ink-2">
            A carbon-aware tool tells each company which hours are cleanest. Each one, alone,
            would save real carbon by moving there. But when many large loads follow the same
            signal, the spare clean generation runs out and the next megawatt comes from a gas
            or coal plant. Add up what each company was promised and compare it with what they
            realize together: the shortfall is the <strong className="text-ink">herding gap</strong>.
            A coordinator that sees everyone&apos;s bids can spread the load and recover much of it.
          </p>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-2">
            The herding effect itself is prior work (Martin, Powell &amp; Rajagopal, Nature
            Communications, 2025, for EV charging). What is new here is coordination across
            independent, competing companies with different load types, and paying each one only
            for what coordination adds beyond what it could have done alone.
          </p>
        </div>
        <div className="card p-5">
          <div className="kicker mb-3">Reading the chart</div>
          <ul className="space-y-3 text-sm text-ink-2" data-testid="chart-reading">
            {r.flatUntil !== null && (
              <li>
                <span className="font-medium text-ink">Up to {r.flatUntil}% of peak: </span>
                default, marginal-solo and coordinated add no CO2 over the no-flex day (
                <span className="num">{fmtInt(r.baseline)}</span> t). The added load is absorbed by
                curtailed renewables, so there is nothing to coordinate.
              </li>
            )}
            <li>
              <span className="font-medium text-ink">Average signal: </span>
              {r.crossesAt === null ? (
                <>never emits more than default up to {r.maxX}% of peak.</>
              ) : (
                <>emits more than default from {r.crossesAt}% of peak onward.</>
              )}
            </li>
            {r.worst !== "default" && (
              <li>
                <span className="font-medium text-ink">From {r.worstFrom}% on: </span>
                {lower(r.worst)} is the highest-emitting strategy
                {r.worst === "independent_marginal" ? " (herding: everyone moves into the same hours)" : ""}; at{" "}
                {r.maxX}% it is <span className="num">{fmtSignedT(r.worstVsDefault)}</span> t vs default.
              </li>
            )}
            <li>
              <span className="font-medium text-ink">Coordination at {r.maxX}%: </span>
              {r.coVsIm < -r.tol ? (
                <>
                  recovers <span className="num">{fmtT(-r.coVsIm)}</span> t vs {lower("independent_marginal")}
                </>
              ) : r.coVsIm > r.tol ? (
                <>
                  emits <span className="num">{fmtT(r.coVsIm)}</span> t more than {lower("independent_marginal")}
                </>
              ) : (
                <>equal to {lower("independent_marginal")}</>
              )}
              {", "}
              {Math.abs(r.coVsDefault) <= r.tol ? (
                <>and equals default.</>
              ) : r.coVsDefault < 0 ? (
                <>
                  ending <span className="num">{fmtT(-r.coVsDefault)}</span> t below default.
                </>
              ) : (
                <>
                  but stays <span className="num">{fmtT(r.coVsDefault)}</span> t above default on actuals.
                </>
              )}
            </li>
            {r.lowest !== "default" && (
              <li>
                <span className="font-medium text-ink">
                  Lowest {r.lowestFrom === r.maxX ? `at ${r.maxX}%` : `at ${r.lowestFrom}–${r.maxX}%`}:{" "}
                </span>
                {lower(r.lowest)}.
              </li>
            )}
          </ul>
          <Source className="mt-3">computed in the browser from /api/adoption-chart means</Source>
        </div>
      </section>

      <CredibilityStrip meta={meta} day={day} forecast={forecast} />

      <div className="flex justify-end">
        <Button onClick={onNext} testId="to-bids">
          Build the bids <span aria-hidden>→</span>
        </Button>
      </div>
    </div>
  );
}

function calib(v: number | null, kind: "pct" | "pp" | "rate") {
  if (v === null) return <span className="text-muted italic">pending</span>;
  const text =
    kind === "pct" ? `${(v * 100).toFixed(1)}%` : kind === "pp" ? `${(v * 100).toFixed(1)} pp` : `${v.toFixed(3)} t/MWh`;
  return <span className="num">{text}</span>;
}

export function fmtLatency(ms: number): string {
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 10) return `${ms.toFixed(1)} ms`;
  return `${fmtInt(ms)} ms`;
}

export function forecastSourceText(f: Forecast | null): string {
  if (!f) return "reported with each clear";
  const src = f.source === "chalk" ? "Chalk model serving" : "local model (Chalk credentials pending)";
  return `${src} · ${fmtLatency(f.latency_ms)}`;
}

interface AdoptionMeta {
  day: string;
  day_label: string;
  actual_peak_mw: number;
  baseline_no_flex_co2_t: number;
  k_draws: number;
  n_bids: number;
  seeds: number[];
  background: string;
}

/**
 * How the adoption chart was computed. The day comes from /api/meta; the population and draw
 * counts come from the chart's build metadata (backend/data/processed/adoption_chart_meta.json,
 * copied at build time because the API contract does not carry it), cross-checked against the
 * chart itself.
 */
function AdoptionCaption({ meta, adoption }: { meta: MetaResponse; adoption: AdoptionChartResponse }) {
  const am = adoptionMeta as AdoptionMeta;
  const defLabel = meta.days.find((d) => d.date === meta.default_day)?.label ?? meta.default_day;
  const base = adoption.strategies.default.mean_co2_t[0];
  const consistent = am.day === meta.default_day && Math.abs(base - am.baseline_no_flex_co2_t) < 0.5;
  const seeds = am.seeds.length > 1 ? `seeds ${am.seeds[0]}–${am.seeds[am.seeds.length - 1]}` : `seed ${am.seeds[0]}`;
  return (
    <div className="mt-3 space-y-1.5" data-testid="adoption-caption">
      <p className="text-xs leading-relaxed text-ink-2">
        Computed on the default day, <span className="font-medium text-ink">{defLabel}</span>{" "}
        (<span className="num">{meta.default_day}</span>), whatever day you pick later. Population:{" "}
        <span className="num">{am.n_bids}</span> synthetic flexible bids per draw,{" "}
        <span className="num">{am.k_draws}</span> draws ({seeds}); lines are the mean and bands the min/max over
        draws. Every strategy is planned on the forecast and scored on the actual day
        {am.background === "none" ? ", with no background load" : ""}. x = the bids&apos; combined max power as a
        share of that day&apos;s actual peak (<span className="num">{fmtInt(am.actual_peak_mw)}</span> MW).
      </p>
      {!consistent && (
        <p className="text-xs text-warn">
          The chart&apos;s build metadata ({am.day}) does not match this chart or the default day; the population
          description may be stale.
        </p>
      )}
      <Source>
        GET /api/adoption-chart · population and draws from adoption_chart_meta.json (build time)
        {API_MODE === "fixture" ? " · fixture: recorded response" : ""}
      </Source>
    </div>
  );
}

export function CredibilityStrip({
  meta,
  day,
  forecast,
}: {
  meta: MetaResponse;
  day: string;
  forecast: Forecast | null;
}) {
  const d = meta.days.find((x) => x.date === day);
  const c: Calibration = meta.calibration;
  return (
    <section className="card overflow-hidden" aria-label="Credibility strip" data-testid="credibility">
      <div className="grid divide-y divide-line sm:grid-cols-2 sm:divide-x sm:divide-y-0 lg:grid-cols-4">
        <div className="p-4">
          <div className="kicker mb-1.5">Region</div>
          <div className="font-medium text-ink">{meta.region}</div>
          <div className="mt-0.5 text-xs text-muted">single balancing area, merit-order dispatch</div>
        </div>
        <div className="p-4">
          <div className="kicker mb-1.5">Real day</div>
          <div className="font-medium text-ink">{d ? d.label : day}</div>
          <div className="num mt-0.5 text-xs text-muted">{day} · held out from calibration</div>
        </div>
        <div className="p-4">
          <div className="kicker mb-1.5">Calibration (held-out days)</div>
          <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-xs text-ink-2">
            <dt>Hourly fossil CO2 error (MAPE)</dt>
            <dd className="text-right">{calib(c.fossil_co2_error, "pct")}</dd>
            <dt>Coal/gas split error (MAE)</dt>
            <dd className="text-right">{calib(c.coal_gas_split_error, "pp")}</dd>
            <dt>Marginal factor, simulated</dt>
            <dd className="text-right">{calib(c.mef_simulated, "rate")}</dd>
            <dt>Marginal factor, observed</dt>
            <dd className="text-right">{calib(c.mef_observed, "rate")}</dd>
          </dl>
        </div>
        <div className="p-4">
          <div className="kicker mb-1.5">Forecast</div>
          <div className="text-sm text-ink" data-testid="forecast-source">{forecastSourceText(forecast)}</div>
          <div className="mt-0.5 text-xs text-muted">
            next-24h demand, wind, solar · gradient boosting on EIA-930 history
            {forecast ? " · latency = this request's forecast call" : ""}
          </div>
        </div>
      </div>
      <div className="border-t border-line bg-surface-2/60 px-4 py-3">
        <div className="kicker mb-1">Data sources</div>
        <ul className="grid gap-x-6 gap-y-0.5 text-xs text-ink-2 md:grid-cols-2">
          {meta.data_sources.map((s) => (
            <li key={s} className="break-words">
              {s}
            </li>
          ))}
        </ul>
        <Source className="mt-2">GET /api/meta{API_MODE === "fixture" ? " · fixture: recorded response" : ""}</Source>
      </div>
    </section>
  );
}
