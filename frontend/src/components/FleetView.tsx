"use client";

import { Grid, Line, OrbitControls, useCursor } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, ToneMapping } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentRef,
  type RefObject,
} from "react";
import * as THREE from "three";
import { loadTypeLabel } from "@/lib/bids";
import { activeRuns, fmtInt, fmtUsd, hh, slotLabel } from "@/lib/format";
import {
  SHAPLEY_MAX_BIDS_DEFAULT,
  type Bid,
  type ClearRequest,
  type ClearResponse,
  type MarginalResource,
  type SettlementBid,
} from "@/lib/types";
import { MarginalLine, type ChartCompany } from "./LoadChart";
import { Button } from "./ui";

/**
 * 3D replay of the coordinated schedule: one building per company around the power grid in the
 * middle. In the hours the coordinator runs a company, the grid streams pulses down the line to
 * its building and the building glows green; the hour's grid numbers and each company's
 * settlement sit alongside. Every number comes from the clear response; the city is decoration.
 */

const BG = "#efe9de"; // warm paper ground, fades into the fog at the edges
const GROUND = "#e8e1d3";
const ACCENT = "#0f5c45"; // --accent
const GREEN = "#2fd27f"; // charging glow (added light, so it reads on the light ground)
const GOLD = "#e3b54f"; // the grid tower and idle power lines
const LINE_IDLE = "#cdb27a";
const RIPPLES = 3; // rings rippling out under a charging building
const RIPPLE_S = 1.8; // seconds for one ring to travel out and fade
const HOUR_MS = 1000; // playback time per hour of the day
const BUILD_S = 0.9; // seconds for a new building to rise
const FLASH_DECAY = 3; // per second: a pulse's highlight fades over about a second
const PULSE_EVERY_S = 0.32; // a charging building gets a pulse this often
const PULSE_SPEED = 16; // metres per second along the power line
const PULSE_TRAIL = 3; // glowing beads per pulse (head + trail)
const MAX_PULSES = 240;
const TOWER_R = 1.7; // pulses leave the grid tower at this radius
const GRID_ID = "__grid";
// up to this many buildings every one gets a name label; up to twice as many, only lit ones do;
// beyond that, only the hovered building's card shows (fewer fit on a phone)
const LABEL_ALL_MAX = { wide: 10, narrow: 5 };
const OFFSCREEN = { transform: "translate(-9999px, -9999px)" }; // until the first frame places it
const EPS_MW = 1e-9;

interface FleetRow {
  bid: Bid;
  index: number;
  color: string;
  mw: number[];
  now: number;
  settle: SettlementBid | undefined;
  /** added in the sidebar since the last run: shown as a building, not yet scheduled */
  pending: boolean;
}

/** A company added in the sidebar that the last run did not include. */
export interface PendingCompany {
  id: string;
  name: string;
  load_type: string;
  max_mw: number;
  color: string;
}

/** Per-building animation state shared between the buildings, the pulses and the labels. */
interface Fx {
  pos: THREE.Vector3; // current (eased) ground position
  top: number; // current roof height
  half: number; // half the footprint, where pulses land
  active: boolean;
  load: number; // 0..1 share of max MW this hour
  flash: number; // 0..1, set to 1 when a pulse lands
  glow: number; // eased 0..1 charging glow
}
type FxMap = Map<string, Fx>;

const shortName = (name: string) => name.split(" (")[0];

export function FleetView({
  request,
  response,
  bids,
  companies,
  pending = [],
}: {
  request: ClearRequest;
  response: ClearResponse;
  bids: Bid[];
  companies: ChartCompany[];
  pending?: PendingCompany[];
}) {
  const coordinated = response.strategies.coordinated;
  const [hour, setHour] = useState(0);
  const [playing, setPlaying] = useState(
    () => !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  const [ended, setEnded] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const [labelMax] = useState(() =>
    window.matchMedia("(min-width: 640px)").matches ? LABEL_ALL_MAX.wide : LABEL_ALL_MAX.narrow,
  );
  // DOM labels over the canvas, moved every frame by <Projector> (drei <Html> makes one React
  // root per label, and React 19 errors when those roots unmount)
  const labelsRef = useRef(new Map<string, HTMLDivElement>());
  const tipRef = useRef<HTMLDivElement>(null);
  const fxRef = useRef<FxMap>(new Map());
  // Continuous time of day in hours (0..24). The buildings read it every frame and playback
  // writes the slider straight to the DOM, so React only re-renders when the whole hour changes.
  const timeRef = useRef(0);
  const sliderRef = useRef<HTMLInputElement>(null);

  const seek = useCallback((t: number) => {
    const c = Math.min(24, Math.max(0, t));
    timeRef.current = c;
    if (sliderRef.current) sliderRef.current.value = String(c);
    setHour(Math.min(23, Math.floor(c)));
  }, []);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const t = timeRef.current + (now - last) / HOUR_MS;
      last = now;
      seek(t);
      if (t >= 24) {
        setPlaying(false);
        setEnded(true);
      } else {
        raf = requestAnimationFrame(tick);
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, seek]);

  const scrub = (t: number) => {
    setPlaying(false);
    setEnded(false);
    seek(t);
  };

  const per = new Map(response.settlement.per_bid.map((p) => [p.bid_id, p]));
  const hasPayouts = response.settlement.per_bid.length > 0;
  const noPayoutNote =
    response.settlement.method === "skipped"
      ? `Shapley payouts are computed for up to ${SHAPLEY_MAX_BIDS_DEFAULT} companies; this clear has ${bids.length}.`
      : "No Shapley payout was computed for this clear.";
  const cleared = bids.map<FleetRow>((bid, index) => {
    const mw = coordinated.schedule[bid.id] ?? [];
    return {
      bid,
      index,
      color: companies.find((c) => c.id === bid.id)?.color ?? "#74776f",
      mw,
      now: mw[hour] ?? 0,
      settle: per.get(bid.id),
      pending: false,
    };
  });
  const clearedIds = new Set(bids.map((b) => b.id));
  const rows: FleetRow[] = [
    ...cleared,
    ...pending
      .filter((p) => !clearedIds.has(p.id))
      .map<FleetRow>((p, i) => ({
        bid: { id: p.id, name: p.name || "New company", load_type: p.load_type, energy_mwh: 0, earliest_hour: 0, deadline_hour: 24, max_mw: p.max_mw },
        index: cleared.length + i,
        color: p.color,
        mw: [],
        now: 0,
        settle: undefined,
        pending: true,
      })),
  ];
  const running = rows.filter((r) => r.now > EPS_MW);
  const runningPerHour = Array.from({ length: 24 }, (_, h) =>
    rows.filter((r) => (r.mw[h] ?? 0) > EPS_MW).length,
  );
  const totalPaid = response.settlement.per_bid.reduce((a, p) => a + p.payout_usd, 0);
  const labelAll = rows.length <= labelMax;
  const labelLit = running.length <= labelMax * 1.2; // big city: compact tags on lit buildings only
  const hoveredRow = rows.find((r) => r.bid.id === hovered);
  const pendingCount = rows.length - cleared.length;

  const play = () => {
    if (ended) seek(0);
    setEnded(false);
    setPlaying(!playing);
  };

  return (
    <section className="card overflow-hidden" data-testid="fleet-view">
      <div className="flex flex-wrap items-end justify-between gap-3 px-4 pt-4 sm:px-5 sm:pt-5">
        <div>
          <div className="kicker mb-1">Grid replay</div>
          <h3 className="text-lg font-semibold text-ink">Who runs when, and who gets paid</h3>
          <p className="mt-1 max-w-2xl text-xs text-muted">
            Each building is a company. When it&apos;s scheduled to run, the grid sends it power and it glows
            green. Drag the timeline to scrub the day; hover a building for details.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="num text-sm font-medium text-ink" data-testid="fleet-hour">
            {slotLabel(hour)}
          </span>
          <Button variant="secondary" onClick={play} testId="fleet-play">
            {playing ? "Pause" : ended ? "Replay day" : "Play"}
          </Button>
        </div>
      </div>

      <div className="grid gap-4 p-4 sm:p-5">
        <div className="min-w-0">
          <div
            className="relative isolate h-[420px] overflow-hidden rounded-lg border border-line sm:h-[520px]"
            style={{ background: BG }}
            role="img"
            aria-label={`3D city of ${rows.length} companies around the grid; ${running.length} drawing power at ${hh(hour)}`}
          >
            <Canvas
              shadows="soft"
              dpr={[1, rows.length > 16 ? 1.5 : 2]} // fewer pixels for big cities
              camera={{ fov: 30, position: [4, 20, 26] }}
              onPointerMissed={() => setHovered(null)} // tap empty space to close a card
              fallback={<p className="p-4 text-sm text-muted">The 3D view needs WebGL, which this browser does not provide.</p>}
            >
              <Suspense fallback={null}>
                <CityScene
                  rows={rows}
                  timeRef={timeRef}
                  fxRef={fxRef}
                  hovered={hovered}
                  setHovered={setHovered}
                  labelsRef={labelsRef}
                  tipRef={tipRef}
                />
              </Suspense>
            </Canvas>
            <div className="pointer-events-none absolute inset-0">
              <GridTag
                running={running.length}
                mw={running.reduce((a, r) => a + r.now, 0)}
                elRef={(el) => {
                  if (el) labelsRef.current.set(GRID_ID, el);
                  else labelsRef.current.delete(GRID_ID);
                }}
              />
              {rows.map((r) => (
                <NameTag
                  key={r.bid.id}
                  row={r}
                  share={r.settle && totalPaid > 0 ? r.settle.payout_usd / totalPaid : null}
                  compact={!labelAll}
                  show={hovered !== r.bid.id && (labelAll || r.pending || (labelLit && r.now > EPS_MW))}
                  elRef={(el) => {
                    if (el) labelsRef.current.set(r.bid.id, el);
                    else labelsRef.current.delete(r.bid.id);
                  }}
                />
              ))}
              <div ref={tipRef} className="absolute left-0 top-0" style={OFFSCREEN}>
                {hoveredRow && <BuildingCard row={hoveredRow} noPayoutNote={noPayoutNote} />}
              </div>
            </div>
            <Legend pendingCount={pendingCount} />
          </div>
          <ScheduleSlider hour={hour} counts={runningPerHour} total={cleared.length} sliderRef={sliderRef} onScrub={scrub} />
        </div>

        <HourPanel
          hour={hour}
          running={running}
          total={cleared.length}
          marginal={coordinated.marginal_resource[hour]}
        />
      </div>

      <CompanyTable
        rows={rows}
        hovered={hovered}
        setHovered={setHovered}
        hasPayouts={hasPayouts}
        noPayoutNote={noPayoutNote}
        totalPaid={totalPaid}
        carbonPrice={request.carbon_price}
        estimated={response.settlement.method === "monte_carlo"}
      />
    </section>
  );
}

function Legend({ pendingCount }: { pendingCount: number }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 px-3 pb-2.5 text-[10px] text-ink-2">
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: GOLD }} /> Power grid
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-muted" /> Company (taller = bigger load)
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: GREEN }} /> Drawing power this hour
      </span>
      {pendingCount > 0 && (
        <span className="flex items-center gap-1.5" data-testid="fleet-pending-note">
          <span className="h-1.5 w-1.5 rounded-full border border-dashed border-muted" /> {pendingCount} new, run to
          schedule
        </span>
      )}
    </div>
  );
}

// --- layout and looks ------------------------------------------------------------

/** One ring of buildings around the grid up to 10 companies, two rings beyond that. */
function layout(n: number): { spots: [number, number, number][]; radius: number } {
  const ring = (count: number, r: number, offset: number) =>
    Array.from({ length: count }, (_, i): [number, number, number] => {
      const a = -0.75 * Math.PI + offset + (i / count) * Math.PI * 2; // first one back-left
      return [Math.cos(a) * r, 0, Math.sin(a) * r];
    });
  if (n <= 10) {
    const r = n <= 4 ? 6.5 : 6.5 + (n - 4) * 0.45;
    return { spots: ring(n, r, 0), radius: r };
  }
  const inner = Math.round(n * 0.4);
  const outer = n - inner;
  const r2 = outer > 14 ? 12.5 : 11.5;
  return {
    spots: [...ring(inner, 7, 0), ...ring(outer, r2, Math.PI / outer)],
    radius: r2,
  };
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Small seeded generator (mulberry32): the same company always gets the same building. */
function rng(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Roof = "plant" | "antenna" | "tank" | "flat";

interface Tier {
  w: number;
  d: number;
  h: number;
  y: number; // bottom of the tier
  map: THREE.CanvasTexture;
  emissive: THREE.CanvasTexture;
}

interface Look {
  w: number;
  d: number;
  h: number;
  rot: number;
  tiers: Tier[];
  roof: Roof;
  facade: string;
  trim: string;
}

/** A pastel take on the company color with a seeded twist: proportions, setback, roof, windows. */
function makeLook(id: string, color: string, height: number, compact: boolean): Look {
  const r = rng(hash(id));
  const base = new THREE.Color(color);
  const hsl = { h: 0, s: 0, l: 0 };
  base.getHSL(hsl);
  // greens would read as "charging", so green companies get teal-blue buildings instead
  const hue = hsl.h > 0.2 && hsl.h < 0.47 ? 0.52 + (hsl.h - 0.2) * 0.22 : hsl.h;
  const facade = new THREE.Color().setHSL(
    (hue + (r() - 0.5) * 0.04 + 1) % 1,
    Math.min(0.75, hsl.s * (0.55 + r() * 0.25)),
    Math.min(0.82, Math.max(0.55, 0.68 + (r() - 0.5) * 0.12)),
  );
  const trim = facade.clone().offsetHSL(0, -0.05, -0.12);
  const scale = compact ? 0.85 : 1;
  const w = (1.45 + r() * 0.6) * scale;
  const d = (1.45 + r() * 0.6) * scale;
  const setback = height > 3.2 && r() < 0.55;
  const lowH = setback ? height * (0.58 + r() * 0.12) : height;
  const facadeHex = `#${facade.getHexString()}`;
  const tiers: Tier[] = [{ w, d, h: lowH, y: 0, ...facadeTextures(facadeHex, w, lowH, r) }];
  if (setback) {
    const k = 0.62 + r() * 0.12;
    const h2 = height - lowH;
    tiers.push({ w: w * k, d: d * k, h: h2, y: lowH, ...facadeTextures(facadeHex, w * k, h2, r) });
  }
  const roofs: Roof[] = ["plant", "antenna", "tank", "flat"];
  return {
    w,
    d,
    h: height,
    rot: (r() - 0.5) * 0.16,
    tiers,
    roof: roofs[Math.floor(r() * roofs.length)],
    facade: facadeHex,
    trim: `#${trim.getHexString()}`,
  };
}

/** Window grid for one face: a color map and a matching emissive mask (lit = white). */
function facadeTextures(facade: string, w: number, h: number, r: () => number) {
  const cols = Math.max(3, Math.round(w / 0.32));
  const floors = Math.max(3, Math.round(h / 0.36));
  const cw = 10;
  const ch = 12;
  const mk = () => {
    const c = document.createElement("canvas");
    c.width = cols * cw;
    c.height = floors * ch;
    return c;
  };
  const colorC = mk();
  const maskC = mk();
  const ctx = colorC.getContext("2d")!;
  const mask = maskC.getContext("2d")!;
  const f = new THREE.Color(facade);
  const dark = `#${f.clone().multiplyScalar(0.62).getHexString()}`;
  const mid = `#${f.clone().multiplyScalar(0.8).getHexString()}`;
  ctx.fillStyle = facade;
  ctx.fillRect(0, 0, colorC.width, colorC.height);
  mask.fillStyle = "#000";
  mask.fillRect(0, 0, maskC.width, maskC.height);
  const litShare = 0.25 + r() * 0.25;
  for (let y = 0; y < floors; y++) {
    for (let x = 0; x < cols; x++) {
      const v = r();
      ctx.fillStyle = v < litShare ? "#fff1bf" : v < 0.75 ? dark : mid;
      ctx.fillRect(x * cw + 2, y * ch + 3, cw - 4, ch - 5);
      const g = Math.round(150 + r() * 105);
      mask.fillStyle = `rgb(${g},${g},${g})`;
      mask.fillRect(x * cw + 2, y * ch + 3, cw - 4, ch - 5);
    }
  }
  const map = new THREE.CanvasTexture(colorC);
  map.colorSpace = THREE.SRGBColorSpace;
  map.anisotropy = 4;
  const emissive = new THREE.CanvasTexture(maskC);
  return { map, emissive };
}

/** Soft radial glow drawn once to a canvas; stretched under each lit building. */
function glowTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const ctx = c.getContext("2d")!;
  const g = ctx.createRadialGradient(64, 64, 4, 64, 64, 64);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.55, "rgba(255,255,255,0.45)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
}

const easeOutBack = (p: number) => {
  const c = 1.4;
  return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2;
};

// --- 3D scene ----------------------------------------------------------------

function CityScene({
  rows,
  timeRef,
  fxRef,
  hovered,
  setHovered,
  labelsRef,
  tipRef,
}: {
  rows: FleetRow[];
  timeRef: RefObject<number>;
  fxRef: RefObject<FxMap>;
  hovered: string | null;
  setHovered: (id: string | null) => void;
  labelsRef: RefObject<Map<string, HTMLDivElement>>;
  tipRef: RefObject<HTMLDivElement | null>;
}) {
  const glowMap = useMemo(() => glowTexture(), []);
  useEffect(() => () => glowMap.dispose(), [glowMap]);
  useCursor(hovered !== null);
  const [reduced] = useState(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches);

  const { spots, radius } = layout(rows.length);
  const road = radius + 2.6;
  const maxMw = Math.max(1, ...rows.map((r) => (Number.isFinite(r.bid.max_mw) ? r.bid.max_mw : 0)));
  const shadowR = road + 2;

  return (
    <>
      <Frame radius={road + 1.2} />
      <color attach="background" args={[BG]} />
      <fog attach="fog" args={[BG, 70 + road * 2, 140 + road * 3]} />
      <hemisphereLight args={["#fff8ec", "#d8ccb6", 1.9]} />
      <ambientLight intensity={0.7} />
      {/* warm low sun from the back left: long soft shadows across the ground, like the reference */}
      <directionalLight
        position={[-14, 18, -6]}
        intensity={2}
        color="#fff3df"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
        shadow-camera-left={-shadowR}
        shadow-camera-right={shadowR}
        shadow-camera-top={shadowR}
        shadow-camera-bottom={-shadowR}
        shadow-camera-near={1}
        shadow-camera-far={80}
      />

      <mesh rotation-x={-Math.PI / 2} position-y={-0.01} receiveShadow>
        <planeGeometry args={[400, 400]} />
        <meshStandardMaterial color={GROUND} roughness={1} />
      </mesh>
      <Grid
        position-y={0.002}
        args={[200, 200]}
        cellSize={1}
        cellThickness={0.6}
        cellColor="#ddd3c0"
        sectionSize={4}
        sectionThickness={1}
        sectionColor="#d3c6ad"
        fadeDistance={road * 2.6}
        fadeStrength={1.5}
        infiniteGrid
      />
      <RingRoad radius={road} />

      <GridTower fxRef={fxRef} count={rows.length} />

      {rows.map((r, i) => (
        <Building
          key={r.bid.id}
          id={r.bid.id}
          color={r.color}
          height={2 + 3.6 * Math.sqrt(Math.max(0.05, (Number.isFinite(r.bid.max_mw) ? r.bid.max_mw : maxMw / 2) / maxMw))}
          compact={rows.length > 16}
          position={spots[i]}
          mw={r.mw}
          maxMw={r.bid.max_mw}
          pending={r.pending}
          delay={reduced ? -1 : i * 0.05}
          timeRef={timeRef}
          fxRef={fxRef}
          glowMap={glowMap}
          hovered={hovered === r.bid.id}
          onOver={() => setHovered(r.bid.id)}
          onOut={() => setHovered(null)}
        />
      ))}

      <Pulses fxRef={fxRef} />
      <Projector ids={rows.map((r) => r.bid.id)} hovered={hovered} fxRef={fxRef} labelsRef={labelsRef} tipRef={tipRef} />

      {/* windows and pulses above full brightness glow */}
      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur luminanceThreshold={1} intensity={0.85} radius={0.65} />
        <ToneMapping mode={ToneMappingMode.NEUTRAL} />
      </EffectComposer>

      <OrbitControls
        makeDefault
        enablePan={false}
        enableZoom={false}
        minPolarAngle={0.35}
        maxPolarAngle={1.15}
        target={[0, 1.2, 0]}
      />
    </>
  );
}

/** Points the camera at the city from a distance that fits the ring road at this canvas size. */
function Frame({ radius }: { radius: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const aspect = useThree((s) => s.size.width / Math.max(1, s.size.height));
  useEffect(() => {
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const fitWidth = (radius * 1.1) / (aspect * tanHalf);
    const fitDepth = (radius * 0.52 + 1.6) / tanHalf; // the ring is foreshortened from above
    const d = Math.max(fitWidth, fitDepth, 12);
    // high three-quarter view, close to the isometric look of an architecture model
    camera.position.copy(new THREE.Vector3(0.14, 0.78, 1).normalize().multiplyScalar(d)).add(new THREE.Vector3(0, 1.2, 0));
    camera.lookAt(0, 1.2, 0);
  }, [camera, aspect, radius]);
  return null;
}

/** Moves the DOM name labels and the hover card to their buildings' roofs each frame. */
function Projector({
  ids,
  hovered,
  fxRef,
  labelsRef,
  tipRef,
}: {
  ids: string[];
  hovered: string | null;
  fxRef: RefObject<FxMap>;
  labelsRef: RefObject<Map<string, HTMLDivElement>>;
  tipRef: RefObject<HTMLDivElement | null>;
}) {
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(({ camera, size }) => {
    const toScreen = (fx: Fx, lift: number) => {
      v.set(fx.pos.x, fx.top + lift, fx.pos.z).project(camera);
      return [((v.x + 1) / 2) * size.width, ((1 - v.y) / 2) * size.height];
    };
    for (const id of [GRID_ID, ...ids]) {
      const el = labelsRef.current.get(id);
      const fx = fxRef.current.get(id);
      if (!el || !fx) continue;
      const [sx, sy] = toScreen(fx, 0.5);
      el.style.transform = `translate(${sx}px, ${sy}px)`;
    }
    const tip = tipRef.current;
    const fx = hovered ? fxRef.current.get(hovered) : undefined;
    if (tip && fx) {
      const [sx, sy] = toScreen(fx, 0.6);
      const left = Math.min(Math.max(8, sx - tip.offsetWidth / 2), size.width - tip.offsetWidth - 8);
      const top = Math.max(8, sy - tip.offsetHeight - 10);
      tip.style.transform = `translate(${left}px, ${top}px)`;
    }
  });
  return null;
}

function RingRoad({ radius }: { radius: number }) {
  const dash = useMemo(
    () =>
      Array.from({ length: 129 }, (_, i) => {
        const a = (i / 128) * Math.PI * 2;
        return new THREE.Vector3(Math.cos(a) * radius, 0.02, Math.sin(a) * radius);
      }),
    [radius],
  );
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position-y={0.008} receiveShadow>
        <ringGeometry args={[radius - 0.6, radius + 0.6, 128]} />
        <meshStandardMaterial color="#ddd2bc" roughness={1} />
      </mesh>
      <Line points={dash} color="#f7f2e8" lineWidth={1.5} dashed dashSize={0.5} gapSize={0.5} />
    </group>
  );
}

/** The power grid: a stepped golden tower with a beacon that brightens as more companies draw power. */
function GridTower({ fxRef, count }: { fxRef: RefObject<FxMap>; count: number }) {
  const textures = useMemo(() => {
    const r = rng(7);
    return [
      facadeTextures("#e9c46e", 2.6, 4.6, r),
      facadeTextures("#ecca78", 2.0, 2.2, r),
      facadeTextures("#efd084", 1.4, 1.3, r),
    ];
  }, []);
  useEffect(() => () => textures.forEach((t) => (t.map.dispose(), t.emissive.dispose())), [textures]);
  const beacon = useRef<THREE.MeshBasicMaterial>(null);
  const halo = useRef<THREE.Mesh>(null);
  const waves = useRef<THREE.Group>(null);
  const sides = useRef<THREE.MeshStandardMaterial[]>([]);
  const level = useRef(0);
  const tiers: [number, number, number][] = [
    [2.6, 4.6, 0.3],
    [2.0, 2.2, 4.9],
    [1.4, 1.3, 7.1],
  ]; // [width, height, bottom]
  const top = 8.4;

  useEffect(() => {
    const fx = fxRef.current;
    fx.set(GRID_ID, {
      pos: new THREE.Vector3(0, 0, 0),
      top: top + 1.6,
      half: TOWER_R,
      active: false,
      load: 0,
      flash: 0,
      glow: 0,
    });
    return () => void fx.delete(GRID_ID);
  }, [fxRef]);

  useFrame((state, dt) => {
    let on = 0;
    fxRef.current.forEach((f, id) => {
      if (id !== GRID_ID && f.active) on++;
    });
    const target = count ? on / count : 0;
    const k = 1 - Math.exp(-dt * 5);
    level.current += (target - level.current) * k;
    const t = state.clock.elapsedTime;
    const throb = 0.5 + 0.5 * Math.sin(t * 4);
    if (beacon.current) beacon.current.color.set(GOLD).multiplyScalar(1.2 + level.current * 3 + throb * level.current * 1.5);
    if (halo.current) {
      const s = 1 + throb * 0.25 * (0.3 + level.current);
      halo.current.scale.setScalar(s);
      ((halo.current.material as THREE.MeshBasicMaterial).opacity = 0.35 + 0.4 * level.current);
    }
    sides.current.forEach((m) => (m.emissiveIntensity = 0.15 + level.current * 1.4));
    const wg = waves.current;
    if (wg) {
      wg.visible = level.current > 0.01;
      wg.children.forEach((ring, i) => {
        const phase = (t / 2.2 + i / 3) % 1;
        const r = 1 + phase * 1.6;
        ring.scale.set(r, r, 1);
        ((ring as THREE.Mesh).material as THREE.MeshBasicMaterial).opacity = (1 - phase) ** 1.5 * Math.min(1, level.current * 2);
      });
    }
  });

  return (
    <group>
      {/* plaza and the gold ring around it */}
      <mesh position-y={0.15} receiveShadow castShadow>
        <boxGeometry args={[3.6, 0.3, 3.6]} />
        <meshStandardMaterial color="#efe3c4" roughness={0.9} />
      </mesh>
      <mesh rotation-x={-Math.PI / 2} position-y={0.012}>
        <ringGeometry args={[2.75, 2.88, 96]} />
        <meshBasicMaterial color={GOLD} transparent opacity={0.75} />
      </mesh>
      <group ref={waves} rotation-x={-Math.PI / 2} position-y={0.02} scale={2.8}>
        {[0, 1, 2].map((i) => (
          <mesh key={i}>
            <ringGeometry args={[0.97, 1, 96]} />
            <meshBasicMaterial color={GOLD} transparent opacity={0} depthWrite={false} side={THREE.DoubleSide} />
          </mesh>
        ))}
      </group>
      {tiers.map(([w, h, y], i) => (
        <mesh key={i} position-y={y + h / 2} castShadow receiveShadow>
          <boxGeometry args={[w, h, w]} />
          {[0, 1, 2, 3, 4, 5].map((f) =>
            f === 2 || f === 3 ? (
              <meshStandardMaterial key={f} attach={`material-${f}`} color="#d9ae4f" roughness={0.8} />
            ) : (
              <meshStandardMaterial
                key={f}
                attach={`material-${f}`}
                ref={(m) => {
                  if (m && !sides.current.includes(m)) sides.current.push(m);
                }}
                map={textures[i].map}
                emissiveMap={textures[i].emissive}
                emissive={GOLD}
                emissiveIntensity={0.15}
                roughness={0.75}
              />
            ),
          )}
        </mesh>
      ))}
      {/* mast and beacon */}
      <mesh position-y={top + 0.6} castShadow>
        <cylinderGeometry args={[0.05, 0.07, 1.2, 8]} />
        <meshStandardMaterial color="#b98e3a" />
      </mesh>
      <mesh position-y={top + 1.3}>
        <sphereGeometry args={[0.24, 24, 16]} />
        <meshBasicMaterial ref={beacon} color={GOLD} toneMapped={false} />
      </mesh>
      <mesh ref={halo} position-y={top + 1.3}>
        <torusGeometry args={[0.55, 0.035, 8, 48]} />
        <meshBasicMaterial color={GOLD} transparent opacity={0.4} toneMapped={false} />
      </mesh>
    </group>
  );
}

function Building({
  id,
  color,
  height,
  compact,
  position,
  mw,
  maxMw,
  pending,
  delay,
  timeRef,
  fxRef,
  glowMap,
  hovered,
  onOver,
  onOut,
}: {
  id: string;
  color: string;
  height: number;
  compact: boolean;
  position: [number, number, number];
  /** coordinated MW per hour */
  mw: number[];
  maxMw: number;
  pending: boolean;
  /** seconds before it starts rising; negative = already built (reduced motion) */
  delay: number;
  timeRef: RefObject<number>;
  fxRef: RefObject<FxMap>;
  glowMap: THREE.Texture;
  hovered: boolean;
  onOver: () => void;
  onOut: () => void;
}) {
  const look = useMemo(() => makeLook(id, color, height, compact), [id, color, height, compact]);
  const sideMats = useMemo(
    () =>
      look.tiers.map(
        (t) =>
          new THREE.MeshStandardMaterial({
            map: t.map,
            emissiveMap: t.emissive,
            emissive: new THREE.Color(GREEN),
            emissiveIntensity: 0,
            roughness: 0.8,
          }),
      ),
    [look],
  );
  const roofMat = useMemo(() => new THREE.MeshStandardMaterial({ color: look.trim, roughness: 0.9 }), [look]);
  useEffect(
    () => () => {
      sideMats.forEach((m) => m.dispose());
      roofMat.dispose();
      look.tiers.forEach((t) => (t.map.dispose(), t.emissive.dispose()));
    },
    [sideMats, roofMat, look],
  );

  const root = useRef<THREE.Group>(null);
  const body = useRef<THREE.Group>(null);
  const rise = useRef<THREE.Group>(null);
  const aura = useRef<THREE.MeshBasicMaterial>(null);
  const glow = useRef<THREE.MeshBasicMaterial>(null);
  const ripples = useRef<THREE.Group>(null);
  const dust = useRef<THREE.Mesh>(null);
  const born = useRef<number | null>(null);
  const tint = useMemo(() => new THREE.Color(), []);
  const white = useMemo(() => new THREE.Color("#ffffff"), []);
  const mint = useMemo(() => new THREE.Color("#bff5d6"), []);
  const half = Math.max(look.w, look.d) / 2;

  // the shared state the pulses and labels read; created once, moved every frame
  useEffect(() => {
    const fx = fxRef.current;
    if (!fx.has(id)) {
      fx.set(id, {
        pos: new THREE.Vector3(...position),
        top: 0,
        half,
        active: false,
        load: 0,
        flash: 0,
        glow: 0,
      });
    }
    return () => void fx.delete(id);
    // position changes are eased in useFrame, not by re-creating the entry
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fxRef, id]);

  useFrame((state, dt) => {
    const fx = fxRef.current.get(id);
    const g = root.current;
    if (!fx || !g) return;
    const t = state.clock.elapsedTime;
    // a company added from the sidebar waits for its editor dialog to close, so the rise is seen
    if (born.current === null && !document.querySelector('[aria-modal="true"]')) {
      born.current = delay < 0 ? -1e9 : t + delay;
    }
    const k = 1 - Math.exp(-dt * 7); // eased, frame-rate independent

    // glide to a new spot when companies are added or removed
    g.position.x += (position[0] - g.position.x) * k;
    g.position.z += (position[2] - g.position.z) * k;
    fx.pos.copy(g.position);
    fx.half = half;

    // construction: rise from the ground with a little overshoot, kicking up a ring of dust
    const p = born.current === null ? 0 : Math.min(1, Math.max(0, (t - born.current) / BUILD_S));
    const grown = p >= 1 ? 1 : Math.max(0.001, easeOutBack(p));
    if (rise.current) {
      rise.current.scale.set(0.85 + 0.15 * Math.min(1, p * 1.4), grown, 0.85 + 0.15 * Math.min(1, p * 1.4));
    }
    if (dust.current) {
      dust.current.visible = p > 0 && p < 1;
      const s = 1 + p * 1.6;
      dust.current.scale.set(s, s, 1);
      (dust.current.material as THREE.MeshBasicMaterial).opacity = (1 - p) * 0.55;
    }
    if (p >= 1 && fx.top < look.h * 0.99 && fx.flash < 0.5) fx.flash = 0.7; // a sparkle as it tops out
    fx.top = look.h * grown;

    // read the clock here rather than through props: no React render per frame
    const now = mw[Math.min(23, Math.floor(timeRef.current))] ?? 0;
    const on = !pending && now > EPS_MW;
    fx.active = on && p >= 1;
    fx.load = on ? Math.min(1, maxMw > 0 ? now / maxMw : 1) : 0;
    fx.glow += ((fx.active ? 0.6 + 0.4 * fx.load : 0) - fx.glow) * k;
    fx.flash = Math.max(0, fx.flash - dt * FLASH_DECAY * (fx.flash > 0.35 ? 1 : 0.6));
    const lit = Math.min(1.3, fx.glow + fx.flash * 0.8);

    sideMats.forEach((m) => {
      m.emissiveIntensity = fx.glow * 1.7 + fx.flash * 2.6;
      m.color.copy(tint.copy(white).lerp(mint, Math.min(1, lit) * 0.3));
    });
    if (aura.current) aura.current.opacity = Math.min(0.3, lit * 0.16);
    if (glow.current) glow.current.opacity = Math.min(1, lit * 0.8);
    const b = body.current;
    if (b) {
      const s = hovered ? 1.06 : 1 + fx.flash * 0.04;
      b.scale.setScalar(b.scale.x + (s - b.scale.x) * k);
    }
    // charging ripple: rings expand from the building across the ground and fade, staggered
    const rg = ripples.current;
    if (rg) {
      rg.visible = fx.glow > 0.02;
      rg.children.forEach((ring, i) => {
        const phase = (t / RIPPLE_S + i / RIPPLES + position[0] * 0.07) % 1;
        const r = 1 + phase * 1.1;
        ring.scale.set(r, r, 1);
        ((ring as THREE.Mesh).material as THREE.MeshBasicMaterial).opacity = (1 - phase) ** 1.6 * fx.glow;
      });
    }
  });

  return (
    <>
      <PowerLine id={id} to={position} half={half} pending={pending} fxRef={fxRef} />
      <group ref={root} position={position}>
        <group ref={ripples} rotation-x={-Math.PI / 2} position-y={0.03} scale={half * 1.25} visible={false}>
          {Array.from({ length: RIPPLES }, (_, i) => (
            <mesh key={i}>
              <ringGeometry args={[0.95, 1, 64]} />
              <meshBasicMaterial
                color={GREEN}
                transparent
                opacity={0}
                depthWrite={false}
                toneMapped={false}
                side={THREE.DoubleSide}
              />
            </mesh>
          ))}
        </group>
        <mesh rotation-x={-Math.PI / 2} position-y={0.025}>
          <planeGeometry args={[half * 4.4, half * 4.4]} />
          <meshBasicMaterial
            ref={glow}
            map={glowMap}
            color={GREEN}
            transparent
            opacity={0}
            depthWrite={false}
            toneMapped={false}
          />
        </mesh>
        <mesh ref={dust} rotation-x={-Math.PI / 2} position-y={0.04} scale={half} visible={false}>
          <ringGeometry args={[0.9, 1.15, 48]} />
          <meshBasicMaterial color="#c9b996" transparent opacity={0} depthWrite={false} />
        </mesh>
        <group
          ref={body}
          rotation-y={look.rot}
          onPointerOver={(e) => {
            e.stopPropagation();
            onOver();
          }}
          onPointerOut={onOut}
          onClick={(e) => {
            e.stopPropagation();
            onOver();
          }}
        >
          {/* plinth */}
          <mesh position-y={0.06} receiveShadow castShadow>
            <boxGeometry args={[look.w + 0.35, 0.12, look.d + 0.35]} />
            <meshStandardMaterial color={pending ? "#e4dccb" : "#f1ebdf"} roughness={1} />
          </mesh>
          {pending && (
            <mesh rotation-x={-Math.PI / 2} position-y={0.13}>
              <ringGeometry args={[half + 0.3, half + 0.38, 4, 1, Math.PI / 4]} />
              <meshBasicMaterial color="#b3a380" />
            </mesh>
          )}
          <group ref={rise} position-y={0.12}>
            {look.tiers.map((tier, i) => (
              <mesh
                key={i}
                position-y={tier.y + tier.h / 2}
                castShadow
                receiveShadow
                material={[sideMats[i], sideMats[i], roofMat, roofMat, sideMats[i], sideMats[i]]}
              >
                <boxGeometry args={[tier.w, tier.h, tier.d]} />
              </mesh>
            ))}
            <RoofDetail look={look} />
            {/* soft green shell: the building itself glows while it charges */}
            <mesh position-y={look.h / 2}>
              <boxGeometry args={[look.w + 0.22, look.h + 0.2, look.d + 0.22]} />
              <meshBasicMaterial
                ref={aura}
                color={GREEN}
                transparent
                opacity={0}
                depthWrite={false}
                blending={THREE.AdditiveBlending}
                side={THREE.BackSide}
                toneMapped={false}
              />
            </mesh>
          </group>
        </group>
      </group>
    </>
  );
}

function RoofDetail({ look }: { look: Look }) {
  const top = look.tiers[look.tiers.length - 1];
  const y = top.y + top.h;
  if (look.roof === "plant")
    return (
      <mesh position={[top.w * 0.12, y + 0.22, -top.d * 0.1]} castShadow>
        <boxGeometry args={[top.w * 0.5, 0.44, top.d * 0.45]} />
        <meshStandardMaterial color="#e9e6df" roughness={0.9} />
      </mesh>
    );
  if (look.roof === "antenna")
    return (
      <group position={[-top.w * 0.18, y, top.d * 0.15]}>
        <mesh position-y={0.55} castShadow>
          <cylinderGeometry args={[0.03, 0.045, 1.1, 6]} />
          <meshStandardMaterial color="#6f6a60" />
        </mesh>
        <mesh position-y={1.12}>
          <sphereGeometry args={[0.06, 10, 8]} />
          <meshBasicMaterial color="#ff5a4a" toneMapped={false} />
        </mesh>
      </group>
    );
  if (look.roof === "tank")
    return (
      <mesh position={[top.w * 0.2, y + 0.28, top.d * 0.18]} castShadow>
        <cylinderGeometry args={[0.24, 0.24, 0.56, 14]} />
        <meshStandardMaterial color={look.trim} roughness={0.8} />
      </mesh>
    );
  return (
    <mesh position-y={y + 0.05}>
      <boxGeometry args={[top.w * 0.92, 0.1, top.d * 0.92]} />
      <meshStandardMaterial color={look.trim} roughness={0.9} />
    </mesh>
  );
}

/** Dashed power line on the ground from the grid to one building; it turns green while energized. */
function PowerLine({
  id,
  to,
  half,
  pending,
  fxRef,
}: {
  id: string;
  to: [number, number, number];
  half: number;
  pending: boolean;
  fxRef: RefObject<FxMap>;
}) {
  const ref = useRef<ComponentRef<typeof Line>>(null);
  const idle = useMemo(() => new THREE.Color(pending ? "#cfc6b3" : LINE_IDLE), [pending]);
  const live = useMemo(() => new THREE.Color(GREEN), []);
  const points = useMemo(() => {
    const dir = new THREE.Vector3(to[0], 0, to[2]).normalize();
    const len = Math.hypot(to[0], to[2]);
    return [dir.clone().multiplyScalar(TOWER_R + 0.9), dir.clone().multiplyScalar(Math.max(TOWER_R + 1, len - half - 0.3))].map(
      (p) => p.setY(0.03),
    );
  }, [to, half]);
  useFrame(() => {
    const m = ref.current?.material;
    const fx = fxRef.current.get(id);
    if (!m || !fx) return;
    m.color.copy(idle).lerp(live, Math.min(1, fx.glow + fx.flash * 0.5));
    m.opacity = pending ? 0.6 : 0.75 + 0.25 * fx.glow;
  });
  return (
    <Line
      ref={ref}
      points={points}
      color={idle}
      lineWidth={pending ? 1.5 : 2.4}
      dashed
      dashSize={pending ? 0.18 : 0.45}
      gapSize={pending ? 0.3 : 0.28}
      transparent
    />
  );
}

interface Pulse {
  id: string;
  born: number;
  dur: number;
}

/**
 * Electrons from the grid to every building that is drawing power: one as soon as the building
 * switches on (so scrubbing the timeline fires them), then a steady stream while it charges.
 * Each pulse that lands lights its building up for about a second.
 */
function Pulses({ fxRef }: { fxRef: RefObject<FxMap> }) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const pulses = useRef<Pulse[]>([]);
  const next = useRef(new Map<string, number>()); // id -> time the next pulse leaves
  const tmp = useMemo(() => new THREE.Object3D(), []);
  const color = useMemo(() => new THREE.Color("#b8ffd6").multiplyScalar(5), []);
  const a = useMemo(() => new THREE.Vector3(), []);
  const b = useMemo(() => new THREE.Vector3(), []);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const fx = fxRef.current;
    fx.forEach((f, id) => {
      if (id === GRID_ID) return;
      if (!f.active) {
        next.current.delete(id);
        return;
      }
      const due = next.current.get(id);
      if (due === undefined || t >= due) {
        if (pulses.current.length < MAX_PULSES) {
          const len = Math.max(1, f.pos.length() - f.half - TOWER_R);
          pulses.current.push({ id, born: t, dur: len / PULSE_SPEED });
        }
        next.current.set(id, t + PULSE_EVERY_S * (1.15 - 0.3 * f.load));
      }
    });

    const m = mesh.current;
    if (!m) return;
    let n = 0;
    pulses.current = pulses.current.filter((p) => {
      const f = fx.get(p.id);
      if (!f) return false;
      const u = (t - p.born) / p.dur;
      if (u >= 1) {
        f.flash = 1; // landed
        return false;
      }
      const dir = b.set(f.pos.x, 0, f.pos.z).normalize();
      const len = f.pos.length();
      for (let j = 0; j < PULSE_TRAIL; j++) {
        const uj = u - j * 0.045;
        if (uj < 0) continue;
        const r = TOWER_R + (len - f.half - TOWER_R) * uj;
        a.copy(dir).multiplyScalar(r);
        a.y = 0.35 + Math.sin(Math.PI * uj) * (0.5 + len * 0.04); // a low hop along the line
        tmp.position.copy(a);
        tmp.scale.setScalar(j === 0 ? 1 : 0.7 - j * 0.18);
        tmp.updateMatrix();
        m.setMatrixAt(n++, tmp.matrix);
      }
      return true;
    });
    m.count = n;
    m.instanceMatrix.needsUpdate = true;
  });

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, MAX_PULSES * PULSE_TRAIL]} frustumCulled={false}>
      <sphereGeometry args={[0.2, 12, 8]} />
      <meshBasicMaterial color={color} toneMapped={false} />
    </instancedMesh>
  );
}

// --- labels over the scene ----------------------------------------------------

function GridTag({ running, mw, elRef }: { running: number; mw: number; elRef: (el: HTMLDivElement | null) => void }) {
  return (
    <div ref={elRef} className="absolute left-0 top-0" style={OFFSCREEN}>
      <div className="-translate-x-1/2 -translate-y-full whitespace-nowrap text-center leading-tight" data-testid="fleet-grid-tag">
        <div className="text-[13px] font-semibold text-ink [text-shadow:0_1px_2px_#efe9de]">Power grid</div>
        <div className="num text-[10px] text-ink-2 [text-shadow:0_1px_2px_#efe9de]">
          {running ? `${running} drawing · ${fmtInt(mw)} MW` : "no flexible load this hour"}
        </div>
      </div>
    </div>
  );
}

function NameTag({
  row,
  share,
  compact,
  show,
  elRef,
}: {
  row: FleetRow;
  share: number | null;
  compact: boolean;
  show: boolean;
  elRef: (el: HTMLDivElement | null) => void;
}) {
  const on = row.now > EPS_MW;
  if (compact)
    return (
      <div ref={elRef} className="absolute left-0 top-0" style={OFFSCREEN}>
        <div
          className={`${show ? "flex" : "hidden"} -translate-x-1/2 -translate-y-full items-center gap-1 whitespace-nowrap rounded-full px-1.5 py-px text-[9.5px] font-medium shadow-sm ${
            on ? "bg-white/90 text-ink ring-1 ring-[#2fd27f]/60" : "bg-white/75 text-ink-2"
          }`}
        >
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: row.color }} />
          {shortName(row.bid.name)}
          {row.pending ? <span className="text-muted">· new</span> : on && <span className="num text-[#0f8a55]">· {fmtInt(row.now)} MW</span>}
        </div>
      </div>
    );
  return (
    <div ref={elRef} className="absolute left-0 top-0" style={OFFSCREEN}>
      <div
        className={`${show ? "block" : "hidden"} -translate-x-1/2 -translate-y-full whitespace-nowrap rounded-md px-1.5 py-0.5 text-center leading-tight transition-colors ${
          on ? "bg-white/85 shadow-sm ring-1 ring-[#2fd27f]/60" : "bg-[#efe9de]/70"
        }`}
      >
        <div className="flex items-center justify-center gap-1 text-[11px] font-semibold text-ink">
          <span className="h-1.5 w-1.5 rounded-full" style={{ background: row.color }} />
          {shortName(row.bid.name)}
        </div>
        <div className="num text-[9.5px] text-muted">
          {loadTypeLabel(row.bid.load_type)}
          {Number.isFinite(row.bid.max_mw) && row.bid.max_mw > 0 ? ` · ${fmtInt(row.bid.max_mw)} MW` : ""}
        </div>
        {row.pending ? (
          <div className="text-[9.5px] font-medium text-ink-2">new · run to schedule</div>
        ) : on ? (
          <div className="num text-[9.5px] font-medium text-[#0f8a55]">charging · {fmtInt(row.now)} MW</div>
        ) : (
          share != null && <div className="num text-[9.5px] font-medium text-ink-2">{(share * 100).toFixed(1)}% of payouts</div>
        )}
      </div>
    </div>
  );
}

function BuildingCard({ row, noPayoutNote }: { row: FleetRow; noPayoutNote: string }) {
  const { bid, settle } = row;
  const runs = activeRuns(row.mw);
  return (
    <div
      className="w-64 rounded-lg border border-line-strong bg-surface p-3 text-xs shadow-lg"
      data-testid={`fleet-card-${bid.id}`}
    >
      <div className="flex items-center gap-1.5">
        <span className="h-2 w-2 rounded-full" style={{ background: row.color }} />
        <span className="font-semibold text-ink">{bid.name}</span>
      </div>
      <div className="mt-0.5 text-muted">{loadTypeLabel(bid.load_type)}</div>
      {row.pending ? (
        <p className="mt-1 text-ink-2">Added since the last run. Run again to schedule it and compute its payout.</p>
      ) : (
        <>
          <p className="mt-1 text-ink-2">
            {row.now > EPS_MW ? (
              <span className="font-medium text-accent">Drawing {fmtInt(row.now)} MW from the grid now.</span>
            ) : (
              <span className="text-muted">Idle this hour.</span>
            )}{" "}
            Scheduled{" "}
            <span className="num">
              {runs.length ? runs.map((r) => `${hh(r.start)}–${hh(r.end)}`).join(", ") : "no hours"}
            </span>
            .
          </p>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 border-t border-dashed border-line-strong pt-2">
            <dt className="text-muted">Payout</dt>
            <dd className="num text-right font-medium text-accent">{settle ? fmtUsd(settle.payout_usd) : "—"}</dd>
          </dl>
          {!settle && <p className="mt-1.5 text-[11px] text-muted">{noPayoutNote}</p>}
        </>
      )}
    </div>
  );
}

// --- panels around the scene ---------------------------------------------------

const SLIDER_CLASS = [
  "absolute inset-0 h-full w-full cursor-pointer appearance-none bg-transparent focus:outline-none",
  "[&::-webkit-slider-runnable-track]:h-full [&::-webkit-slider-runnable-track]:bg-transparent",
  "[&::-webkit-slider-thumb]:-mt-1 [&::-webkit-slider-thumb]:h-11 [&::-webkit-slider-thumb]:w-2.5",
  "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full",
  "[&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-white [&::-webkit-slider-thumb]:bg-ink",
  "[&::-webkit-slider-thumb]:shadow-md focus-visible:[&::-webkit-slider-thumb]:ring-2",
  "focus-visible:[&::-webkit-slider-thumb]:ring-accent",
  "[&::-moz-range-track]:bg-transparent [&::-moz-range-thumb]:h-11 [&::-moz-range-thumb]:w-2.5",
  "[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2 [&::-moz-range-thumb]:border-white",
  "[&::-moz-range-thumb]:bg-ink",
].join(" ");

/**
 * The day as a slider: the track is the 24-hour activity strip (darker = more companies
 * running), and the thumb is the playhead. Drag or click to scrub; arrow keys step an hour.
 */
function ScheduleSlider({
  hour,
  counts,
  total,
  sliderRef,
  onScrub,
}: {
  hour: number;
  counts: number[];
  total: number;
  sliderRef: RefObject<HTMLInputElement | null>;
  onScrub: (t: number) => void;
}) {
  return (
    <div className="mt-3" data-testid="fleet-slider">
      <div className="relative h-9">
        <div className="pointer-events-none absolute inset-0 grid grid-cols-24 gap-px overflow-hidden rounded-md">
          {counts.map((n, h) => (
            <div
              key={h}
              className={h === hour ? "brightness-110" : ""}
              style={{ background: n ? ACCENT : "var(--line)", opacity: n ? 0.25 + 0.75 * (n / Math.max(1, total)) : 1 }}
            />
          ))}
        </div>
        <input
          ref={sliderRef}
          type="range"
          min={0}
          max={24}
          step="any"
          defaultValue={0}
          aria-label="Time of day"
          aria-valuetext={`${slotLabel(hour)}, ${counts[hour]} of ${total} companies running`}
          onChange={(e) => onScrub(Number(e.currentTarget.value))}
          onKeyDown={(e) => {
            const d = { ArrowRight: 1, ArrowUp: 1, ArrowLeft: -1, ArrowDown: -1 }[e.key];
            if (!d) return;
            e.preventDefault();
            onScrub(Math.min(23, Math.max(0, hour + d)) + 0.5); // middle of the next hour
          }}
          className={SLIDER_CLASS}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted">
        <span>00:00</span>
        <span>06:00</span>
        <span>12:00</span>
        <span>18:00</span>
        <span>24:00</span>
      </div>
    </div>
  );
}

function HourPanel({
  hour,
  running,
  total,
  marginal,
}: {
  hour: number;
  running: FleetRow[];
  total: number;
  marginal: MarginalResource | undefined;
}) {
  const mw = running.reduce((a, r) => a + r.now, 0);
  return (
    <aside className="grid gap-3 text-sm sm:grid-cols-2" data-testid="fleet-hour-panel">
      <div className="rounded-lg border border-line bg-surface-2 p-3">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted">{slotLabel(hour)}</div>
        <div className="num mt-1 text-2xl font-medium text-ink">
          {running.length} <span className="text-base text-muted">of {total} running</span>
        </div>
        <div className="num mt-0.5 text-xs text-muted">{fmtInt(mw)} MW of flexible load this hour</div>
      </div>
      {marginal && (
        <div className="rounded-lg border border-line p-3 text-xs">
          <MarginalLine m={marginal} />
        </div>
      )}
    </aside>
  );
}

function CompanyTable({
  rows,
  hovered,
  setHovered,
  hasPayouts,
  noPayoutNote,
  totalPaid,
  carbonPrice,
  estimated,
}: {
  rows: FleetRow[];
  hovered: string | null;
  setHovered: (id: string | null) => void;
  hasPayouts: boolean;
  noPayoutNote: string;
  totalPaid: number;
  carbonPrice: number;
  estimated: boolean;
}) {
  return (
    <div className="border-t border-line px-4 pb-4 pt-3 sm:px-5 sm:pb-5">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted">
          Payouts for the day
        </div>
        <div className="num text-xs text-muted">
          {hasPayouts
            ? `${fmtUsd(totalPaid)} paid · ${fmtUsd(carbonPrice)}/t${estimated ? " · estimated (sampled Shapley)" : ""}`
            : noPayoutNote}
        </div>
      </div>
      <div className="max-h-72 overflow-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface text-left text-[10px] uppercase tracking-wider text-muted">
            <tr>
              <th className="py-1 pr-2 font-medium">Company</th>
              <th className="py-1 pr-2 text-right font-medium">This hour</th>
              <th className="py-1 text-right font-medium">Payout</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const on = r.now > EPS_MW;
              return (
                <tr
                  key={r.bid.id}
                  onMouseEnter={() => setHovered(r.bid.id)}
                  onMouseLeave={() => setHovered(null)}
                  className={`border-t border-line ${on ? "bg-accent-soft" : ""} ${hovered === r.bid.id ? "outline outline-1 outline-accent" : ""}`}
                  data-testid={`fleet-row-${r.bid.id}`}
                >
                  <td className="py-1.5 pr-2">
                    <span className="flex items-center gap-1.5">
                      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: r.color }} />
                      <span className="text-ink">{r.bid.name}</span>
                    </span>
                  </td>
                  <td className={`num py-1.5 pr-2 text-right ${on ? "text-accent" : "text-muted"}`}>
                    {r.pending ? "run to schedule" : on ? `${fmtInt(r.now)} MW` : "idle"}
                  </td>
                  <td className="num py-1.5 text-right font-medium text-ink">{r.settle ? fmtUsd(r.settle.payout_usd) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
