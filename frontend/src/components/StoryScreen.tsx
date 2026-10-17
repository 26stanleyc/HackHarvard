"use client";

import { API_MODE } from "@/lib/api";
import { fmtInt, fmtPct, fmtSignedT } from "@/lib/format";
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
  return (
    <div className="space-y-8">
      <header className="max-w-4xl pt-2">
        <div className="kicker mb-3">{meta.region} · day-ahead clearinghouse for flexible load</div>
        <h1 className="font-serif text-[26px] leading-[1.15] font-semibold tracking-tight text-ink sm:text-[36px] lg:text-[44px] lg:leading-[1.12]">
          When every flexible load chases the same clean hour, fossil plants serve the rush.
          Coordinating competing companies recovers the savings, and pays each for the carbon
          it actually helped avoid.
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
        <Source className="mt-2">
          GET /api/adoption-chart · precomputed offline from a synthetic bid population (fixed
          seed, averaged over draws; bands show draw-to-draw variability) · scored on actual
          EIA-930 days{API_MODE === "fixture" ? " · fixture values, illustrative only" : ""}
        </Source>
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
          <div className="kicker mb-3">What this chart shows</div>
          <ul className="space-y-3 text-sm text-ink-2">
            <li>
              <span className="font-medium text-ink">Average signal vs default: </span>
              {r.crossesAt === null ? (
                <>never above default up to {r.maxX}% of peak in this data.</>
              ) : (
                <>
                  emits <em>more</em> than doing nothing from {r.crossesAt}% of peak onward.
                </>
              )}
            </li>
            <li>
              <span className="font-medium text-ink">At {r.maxX}% of peak: </span>
              average signal <span className="num">{fmtSignedT(r.iaVsDefault)}</span> t, coordinated{" "}
              <span className="num">{fmtSignedT(r.coVsDefault)}</span> t vs default.
            </li>
            <li>
              <span className="font-medium text-ink">Value of coordination: </span>
              coordinated vs the best solo strategy at {r.maxX}%:{" "}
              <span className="num">{fmtSignedT(r.coVsIm)}</span> t.
            </li>
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

function calib(v: number | null, kind: "pct" | "rate") {
  if (v === null) return <span className="text-muted italic">pending</span>;
  return <span className="num">{kind === "pct" ? fmtPct(v) : `${v.toFixed(2)} t/MWh`}</span>;
}

export function forecastSourceText(f: Forecast | null): string {
  if (!f) return "reported with each clear";
  const src = f.source === "chalk" ? "Chalk model serving" : "local model (Chalk credentials pending)";
  return `${src} · ${fmtInt(f.latency_ms)} ms`;
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
            <dt>Hourly fossil CO2 error</dt>
            <dd className="text-right">{calib(c.fossil_co2_error, "pct")}</dd>
            <dt>Coal/gas split error</dt>
            <dd className="text-right">{calib(c.coal_gas_split_error, "pct")}</dd>
            <dt>Marginal factor, simulated</dt>
            <dd className="text-right">{calib(c.mef_simulated, "rate")}</dd>
            <dt>Marginal factor, observed</dt>
            <dd className="text-right">{calib(c.mef_observed, "rate")}</dd>
          </dl>
        </div>
        <div className="p-4">
          <div className="kicker mb-1.5">Forecast</div>
          <div className="text-sm text-ink">{forecastSourceText(forecast)}</div>
          <div className="mt-0.5 text-xs text-muted">
            next-24h demand, wind, solar · gradient boosting on EIA-930 history
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
        <Source className="mt-2">GET /api/meta{API_MODE === "fixture" ? " · fixture values, not real calibration results" : ""}</Source>
      </div>
    </section>
  );
}
