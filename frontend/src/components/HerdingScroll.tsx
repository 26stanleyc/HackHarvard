"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import herdingRequest from "@/fixtures/herding_scroll_request.json";
import herdingResponse from "@/fixtures/herding_scroll_response.json";
import { API_MODE, postClear } from "@/lib/api";
import { fmtShareT, fmtT, fmtUsd } from "@/lib/format";
import type { ClearRequest, ClearResponse } from "@/lib/types";
import { Source } from "./ui";

/**
 * Scroll-scrubbed explainer: the page's scroll position drives a Blender render from herded to
 * coordinated. Two renders are wired up; VARIANT picks one:
 *   herding: demo-visuals/herding/build_herding.py (HERDING_CAPTIONS=0), cream bar-chart scene
 *   hub:     demo-visuals/grid-hub/build_grid_hub.py with its captions hidden, dark glowing scene
 * Both animations are illustrative; the numbers are a real /api/clear run of the four companies
 * they show (fixtures/herding_scroll_request.json), falling back to the recorded response.
 */

const FRAMES = 480;
const FPS = 24;

// Same colours and short names as the company blocks in the herding render.
const COMPANY = [
  { label: "EV fleet", color: "#2f5d8a" },
  { label: "Data center", color: "#7a5aa6" },
  { label: "Office pre-cooling", color: "#c98a1b" },
  { label: "Cold storage", color: "#8c6d4f" },
];

const REQUEST = herdingRequest as unknown as ClearRequest;
const RECORDED = herdingResponse as unknown as ClearResponse;

type Stat = "gap" | "avoided" | "payouts" | null;

interface Act {
  from: number;
  kicker: string;
  title: string;
  body: string;
  stat: Stat;
}

interface Variant {
  src: string;
  poster: string;
  dark: boolean;
  /** frame ranges follow the keyframes in the variant's build script */
  acts: Act[];
  herdedFrame: number;
  coordinatedFrame: number;
}

const SETTLEMENT_BODY = `Payouts are each company's Shapley share of the avoided carbon, at $${REQUEST.carbon_price}/t.`;

const VARIANTS: Record<"herding" | "hub", Variant> = {
  herding: {
    src: "/media/herding-scrub.mp4",
    poster: "/media/herding-scrub-poster.jpg",
    dark: false,
    herdedFrame: 250,
    coordinatedFrame: 470,
    acts: [
      {
        from: 0,
        kicker: "The setup",
        title: "Four companies, one day of flexible load",
        body: "Each company has load it can run in any hour of its window. The green bars are the spare clean energy in each hour.",
        stat: null,
      },
      {
        from: 88,
        kicker: "1 · Herding",
        title: "Every carbon-aware tool picks the same hours",
        body: "Each company's tool reads the same forecast, so they all send their load to the same \u201ccleanest\u201d hours.",
        stat: null,
      },
      {
        from: 166,
        kicker: "2 · The rush",
        title: "The spare clean energy runs out",
        body: "Load above the green bar is served by gas and coal plants, so the savings each company was promised don't all show up.",
        stat: "gap",
      },
      {
        from: 288,
        kicker: "3 · Coordinated",
        title: "The clearinghouse schedules everyone together",
        body: "Seeing all the bids at once, it spreads the load across the clean hours instead of piling it into one.",
        stat: "avoided",
      },
      { from: 394, kicker: "4 · Settlement", title: "Each company is paid for what coordination added", body: SETTLEMENT_BODY, stat: "payouts" },
    ],
  },
  hub: {
    src: "/media/grid-hub-scrub.mp4",
    poster: "/media/grid-hub-scrub-poster.jpg",
    dark: true,
    herdedFrame: 215,
    coordinatedFrame: 380,
    acts: [
      {
        from: 0,
        kicker: "The setup",
        title: "Four companies, one grid",
        body: "Solar and wind fill the grid's clean-energy core. Each company has flexible load it can run in any hour of its window.",
        stat: null,
      },
      {
        from: 78,
        kicker: "1 · Herding",
        title: "Every carbon-aware tool picks the same hour",
        body: "Each company's tool reads the same forecast, so all four draw power at once in the \u201ccleanest\u201d hour.",
        stat: null,
      },
      {
        from: 160,
        kicker: "2 · The rush",
        title: "The clean-energy core runs dry",
        body: "Gas and coal plants cover the rest, so the savings each company was promised don't all show up.",
        stat: "gap",
      },
      {
        from: 243,
        kicker: "3 · Coordinated",
        title: "The clearinghouse schedules everyone together",
        body: "The companies take turns across the clean hours, so the core never empties and the fossil plant stays dark.",
        stat: "avoided",
      },
      { from: 412, kicker: "4 · Settlement", title: "Each company is paid for what coordination added", body: SETTLEMENT_BODY, stat: "payouts" },
    ],
  },
};

const VARIANT: keyof typeof VARIANTS = "hub";
const V = VARIANTS[VARIANT];
const ACTS = V.acts;

function actAt(frame: number): number {
  let i = 0;
  while (i + 1 < ACTS.length && frame >= ACTS[i + 1].from) i++;
  return i;
}

export function HerdingScroll() {
  const sectionRef = useRef<HTMLElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const barRef = useRef<HTMLDivElement>(null);
  const [act, setAct] = useState(0);
  const [data, setData] = useState<{ res: ClearResponse; source: "live" | "recorded" } | null>(() =>
    API_MODE === "fixture" ? { res: RECORDED, source: "recorded" } : null,
  );

  useEffect(() => {
    if (API_MODE === "fixture") return;
    let cancelled = false;
    postClear(REQUEST)
      .then((res) => !cancelled && setData({ res, source: "live" }))
      .catch(() => !cancelled && setData({ res: RECORDED, source: "recorded" }));
    return () => {
      cancelled = true;
    };
  }, []);

  // Scroll progress through the section -> video time, eased so trackpad steps stay smooth.
  useEffect(() => {
    let raf = 0;
    let shown = 0;
    let lastAct = -1;
    const tick = () => {
      const section = sectionRef.current;
      const video = videoRef.current;
      if (section && video) {
        const rect = section.getBoundingClientRect();
        const span = rect.height - window.innerHeight;
        const p = span > 0 ? Math.min(1, Math.max(0, -rect.top / span)) : 0;
        const target = p * (FRAMES - 1);
        shown += (target - shown) * 0.18;
        if (Math.abs(target - shown) < 0.05) shown = target;
        const t = shown / FPS;
        if (video.readyState >= 1 && !video.seeking && Math.abs(video.currentTime - t) > 0.5 / FPS) {
          video.currentTime = t;
        }
        if (barRef.current) barRef.current.style.transform = `scaleX(${shown / (FRAMES - 1)})`;
        const a = actAt(shown);
        if (a !== lastAct) {
          lastAct = a;
          setAct(a);
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  // Pin below the app's sticky header, which can wrap to two rows on narrow screens.
  const [headerH, setHeaderH] = useState(100);
  useEffect(() => {
    const header = document.querySelector("header");
    if (!header) return;
    const ro = new ResizeObserver(() => setHeaderH(Math.ceil(header.getBoundingClientRect().height)));
    ro.observe(header);
    return () => ro.disconnect();
  }, []);

  const jumpTo = useCallback((frame: number) => {
    const section = sectionRef.current;
    if (!section) return;
    const top = section.getBoundingClientRect().top + window.scrollY;
    const span = section.offsetHeight - window.innerHeight;
    window.scrollTo({ top: top + (frame / (FRAMES - 1)) * span, behavior: "smooth" });
  }, []);

  const a = ACTS[act];
  // The dark variant puts its captions in a band under the video, so the video gets less height.
  const width = { maxWidth: `calc((100svh - ${headerH + (V.dark ? 305 : 170)}px) * 16 / 9)` };
  const tone = V.dark ? DARK : LIGHT;
  const coordinated = act >= 3;
  const herded = act >= 1 && act < 3;

  return (
    <section
      ref={sectionRef}
      aria-label="Scroll-driven explainer of the herding effect"
      className="relative h-[560vh]"
      data-testid="herding-scroll"
    >
      <div
        className="sticky flex flex-col justify-center gap-3 py-3"
        style={{ top: headerH, height: `calc(100svh - ${headerH}px)` }}
      >
        <div
          className="mx-auto flex w-full flex-wrap items-end justify-between gap-2"
          style={width}
        >
          <div>
            <div className="kicker">The herding effect · scroll to play</div>
            <h2 className="mt-1 font-serif text-lg font-semibold tracking-tight text-ink lg:text-2xl">
              Same companies, same day: herded vs coordinated
            </h2>
          </div>
          <div role="group" aria-label="Jump to" className="flex rounded-full border border-line bg-surface p-0.5 text-[13px]">
            <button
              type="button"
              onClick={() => jumpTo(V.herdedFrame)}
              aria-pressed={herded}
              className={`rounded-full px-3 py-1 font-medium transition-colors ${herded ? "bg-danger text-white" : "text-ink-2 hover:text-ink"}`}
              data-testid="jump-herded"
            >
              Before: herded
            </button>
            <button
              type="button"
              onClick={() => jumpTo(V.coordinatedFrame)}
              aria-pressed={coordinated}
              className={`rounded-full px-3 py-1 font-medium transition-colors ${coordinated ? "bg-accent text-white" : "text-ink-2 hover:text-ink"}`}
              data-testid="jump-coordinated"
            >
              After: coordinated
            </button>
          </div>
        </div>

        <div
          className={`relative mx-auto w-full shrink-0 overflow-hidden rounded-[10px] border ${V.dark ? "border-white/10 md:flex md:flex-col-reverse" : "border-line"}`}
          style={{ ...width, background: V.dark ? "#06080c" : "#f6f5f0" }}
        >
          <div
            key={act}
            className={`herding-fade relative z-10 p-4 ${V.dark ? "md:grid md:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)] md:items-center md:gap-6 md:px-5 md:py-3 md:[&>*:last-child]:justify-self-end" : "md:absolute md:top-[4.5%] md:left-[3.5%] md:max-w-[50%] md:p-0"}`}
            aria-live="polite"
            data-testid="herding-caption"
          >
            <div>
              <div className={`kicker mb-1.5 ${tone.kicker}`}>{a.kicker}</div>
              <h3 className={`font-serif text-lg leading-snug font-semibold ${V.dark ? "" : "lg:text-[22px]"} ${tone.title}`}>{a.title}</h3>
              <p className={`mt-1 text-sm leading-relaxed ${V.dark ? "" : "lg:text-[15px]"} ${tone.body}`}>{a.body}</p>
            </div>
            {a.stat && <StatBlock stat={a.stat} data={data?.res ?? null} tone={tone} />}
          </div>
          <div className="relative">
            <video
              ref={videoRef}
              src={V.src}
              poster={V.poster}
              muted
              playsInline
              preload="auto"
              aria-hidden
              className="block aspect-video w-full"
            />
            <div className={`absolute inset-x-0 bottom-0 h-1 ${V.dark ? "bg-white/10" : "bg-line/60"}`} aria-hidden>
              <div ref={barRef} className={`h-full origin-left ${V.dark ? "bg-white/60" : "bg-ink/70"}`} style={{ transform: "scaleX(0)" }} />
            </div>
          </div>
        </div>

        <div className="mx-auto w-full" style={width}>
          <Source>
            animation illustrative · numbers {data?.source === "live" ? "live" : "recorded"} from /api/clear: ERCOT{" "}
            {REQUEST.day}, these 4 companies, {REQUEST.background_pct}% background · scenario chosen to show the
            effect; other days are mixed
          </Source>
        </div>
      </div>
    </section>
  );
}

const LIGHT = {
  kicker: "",
  title: "text-ink",
  body: "text-ink-2",
  box: "border-line bg-surface/90",
  strong: "text-ink",
  soft: "text-ink-2",
  muted: "text-muted",
  good: "text-accent",
  bad: "text-danger",
  skeleton: "bg-surface-2",
};

const DARK: typeof LIGHT = {
  kicker: "!text-[#8fa3b0]",
  title: "text-[#eef2f4]",
  body: "text-[#b9c4cc]",
  box: "border-white/10 bg-white/[0.04]",
  strong: "text-[#eef2f4]",
  soft: "text-[#b9c4cc]",
  muted: "text-[#8fa3b0]",
  good: "text-[#3fd4a0]",
  bad: "text-[#ff8a6a]",
  skeleton: "bg-white/10",
};

function StatBlock({
  stat,
  data,
  tone,
}: {
  stat: Exclude<Stat, null>;
  data: ClearResponse | null;
  tone: typeof LIGHT;
}) {
  if (!data) return <div className={`mt-3 h-14 w-64 animate-pulse rounded-lg ${tone.skeleton}`} />;
  const box = `mt-3 inline-block rounded-lg border px-3.5 py-2.5 backdrop-blur-sm ${tone.box}`;

  if (stat === "gap") {
    const h = data.herding.independent_marginal;
    if (!(h.predicted_t > 0 && h.gap_t > 0)) return null;
    return (
      <div className={box} data-testid="stat-gap">
        <div className="flex items-baseline gap-4">
          <div>
            <div className={`text-[11px] ${tone.muted}`}>promised (sum of solo savings)</div>
            <div className={`num text-lg ${tone.strong}`}>{fmtT(h.predicted_t)} t</div>
          </div>
          <div aria-hidden className={tone.muted}>→</div>
          <div>
            <div className={`text-[11px] ${tone.muted}`}>delivered together</div>
            <div className={`num text-lg ${tone.strong}`}>{fmtT(h.realized_t)} t</div>
          </div>
        </div>
        <div className={`mt-1 text-sm font-medium ${tone.bad}`}>
          {Math.round((h.gap_t / h.predicted_t) * 100)}% short: a herding gap of {fmtT(h.gap_t)} t
        </div>
      </div>
    );
  }

  if (stat === "avoided") {
    const s = data.strategies;
    const vsDefault = s.default.total_co2_t - s.coordinated.total_co2_t;
    return (
      <div className={box} data-testid="stat-avoided">
        <div className={`num text-2xl ${tone.good}`}>{fmtT(data.settlement.total_avoided_t)} t</div>
        <div className={`text-[13px] ${tone.soft}`}>
          CO2 avoided vs every company acting alone
          {vsDefault > 0 ? (
            <>
              {" "}
              · <span className="num">{fmtT(vsDefault)} t</span> below the default schedule
            </>
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className={box} data-testid="stat-payouts">
      <table className="text-[13px]">
        <tbody>
          {data.settlement.per_bid.map((b) => {
            const i = REQUEST.bids.findIndex((x) => x.id === b.bid_id);
            const c = COMPANY[i] ?? { label: b.bid_id, color: "#74776f" };
            return (
              <tr key={b.bid_id}>
                <td className="py-0.5 pr-3">
                  <span className="mr-2 inline-block h-2.5 w-2.5 rounded-sm align-middle" style={{ background: c.color }} />
                  <span className={tone.soft}>{c.label}</span>
                </td>
                <td className={`num py-0.5 pr-3 text-right ${tone.muted}`}>{fmtShareT(b.shapley_t)} t</td>
                <td className={`num py-0.5 text-right ${tone.strong}`}>{fmtUsd(b.payout_usd)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
