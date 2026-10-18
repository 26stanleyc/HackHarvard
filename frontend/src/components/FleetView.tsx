"use client";

import { OrbitControls, useCursor } from "@react-three/drei";
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
  type RefObject,
} from "react";
import * as THREE from "three";
import { loadTypeLabel } from "@/lib/bids";
import { activeRuns, fmtInt, fmtT, fmtUsd, hh, slotLabel } from "@/lib/format";
import {
  SHAPLEY_MAX_BIDS_DEFAULT,
  type Bid,
  type ClearRequest,
  type ClearResponse,
  type MarginalResource,
  type SettlementBid,
} from "@/lib/types";
import {
  BUILD_S,
  BuildingMesh,
  CITY,
  DEMOLISH_S,
  CityGround,
  disposeLook,
  easeOutBack,
  glowTexture,
  GridTower,
  makeLook,
  newVis,
  PowerLine,
  PulseField,
  TOWER_R,
  TOWER_TOP,
  type Stream,
  type Vis,
} from "./city";
import { MarginalLine, type ChartCompany } from "./LoadChart";
import { Button, Segmented } from "./ui";

/**
 * 3D replay of the coordinated schedule: one building per company around the power grid in the
 * middle. In the hours the coordinator runs a company, the grid streams pulses down the line to
 * its building and the building glows green; the hour's grid numbers and each company's
 * settlement sit alongside. Every number comes from the clear response; the city is decoration.
 */

const ACCENT = "#0f5c45"; // --accent
const HOUR_MS = 1000; // playback time per hour of the day
const FLASH_DECAY = 3; // per second: a pulse's highlight fades over about a second
const PULSE_EVERY_S = 0.32; // a charging building gets a pulse this often
const GRID_ID = "__grid";
// up to this many buildings every one gets a name label; up to twice as many, only lit ones do;
// beyond that, only the hovered building's card shows (fewer fit on a phone)
const LABEL_ALL_MAX = { wide: 10, narrow: 5 };
const OFFSCREEN = { transform: "translate(-9999px, -9999px)" }; // until the first frame places it
const EPS_MW = 1e-9;
const CLEAN_T = 0.5; // t CO2 below which a difference counts as none (float noise)

/** The replay shows either the coordinated schedule or each company acting alone on today's
 * carbon-aware signal ("everyone chases the clean hour"), the herding baseline. */
type View = "coordinated" | "independent_average";
const VIEW_LABEL: Record<View, string> = { coordinated: "Coordinated", independent_average: "No coordination" };

interface FleetRow {
  bid: Bid;
  index: number;
  color: string;
  mw: number[];
  now: number;
  settle: SettlementBid | undefined;
  /** added in the sidebar since the last run: shown as a building, not yet scheduled */
  pending: boolean;
  /** removed in the sidebar since the last run: demolished, its lot left until the next run */
  removed: boolean;
  /** a pending company just removed: kept only while its demolition plays, outside the layout */
  ghost?: boolean;
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
  removed = [],
}: {
  request: ClearRequest;
  response: ClearResponse;
  bids: Bid[];
  companies: ChartCompany[];
  pending?: PendingCompany[];
  /** ids from the last run that have since been removed in the sidebar */
  removed?: string[];
}) {
  const [view, setView] = useState<View>("coordinated");
  const shown = response.strategies[view];
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
  const gone = new Set(removed);
  const cleared = bids.map<FleetRow>((bid, index) => {
    const out = gone.has(bid.id);
    const mw = out ? [] : (shown.schedule[bid.id] ?? []);
    return {
      bid,
      index,
      color: companies.find((c) => c.id === bid.id)?.color ?? "#74776f",
      mw,
      now: mw[hour] ?? 0,
      settle: per.get(bid.id),
      pending: false,
      removed: out,
    };
  });
  const clearedIds = new Set(bids.map((b) => b.id));
  const liveRows: FleetRow[] = [
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
        removed: false,
      })),
  ];

  // A new company removed before it ever ran vanishes from the rows; keep it as a ghost while
  // its demolition plays (state adjusted during render, the React pattern for derived state).
  const rowsKey = liveRows.map((r) => `${r.bid.id}${r.removed ? "x" : ""}`).join(",");
  const [seen, setSeen] = useState({ key: rowsKey, rows: liveRows });
  const [ghosts, setGhosts] = useState<FleetRow[]>([]);
  if (seen.key !== rowsKey) {
    const ids = new Set(liveRows.map((r) => r.bid.id));
    const vanished = seen.rows.filter((r) => !ids.has(r.bid.id) && !r.removed && !r.ghost);
    if (vanished.length || ghosts.some((g) => ids.has(g.bid.id))) {
      setGhosts((g) => [
        ...g.filter((x) => !ids.has(x.bid.id)),
        ...vanished.map((r) => ({ ...r, mw: [], now: 0, removed: true, ghost: true })),
      ]);
    }
    setSeen({ key: rowsKey, rows: liveRows });
  }
  useEffect(() => {
    if (!ghosts.length) return;
    const ids = new Set(ghosts.map((g) => g.bid.id));
    const t = setTimeout(() => setGhosts((g) => g.filter((x) => !ids.has(x.bid.id))), DEMOLISH_S * 1000 + 150);
    return () => clearTimeout(t);
  }, [ghosts]);
  const rows: FleetRow[] = [...liveRows, ...ghosts];
  const running = rows.filter((r) => r.now > EPS_MW);
  const runningPerHour = Array.from({ length: 24 }, (_, h) =>
    rows.filter((r) => (r.mw[h] ?? 0) > EPS_MW).length,
  );
  const totalPaid = response.settlement.per_bid.reduce((a, p) => a + p.payout_usd, 0);
  const labelAll = rows.length <= labelMax;
  const labelLit = running.length <= labelMax * 1.2; // big city: compact tags on lit buildings only
  const hoveredRow = rows.find((r) => r.bid.id === hovered);
  const pendingCount = liveRows.filter((r) => r.pending).length;
  const removedCount = cleared.filter((r) => r.removed).length;

  const meter = useMemo(() => meterSeries(response), [response]);

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
            green. The panel adds up where their power has come from so far: spare wind and solar, gas, or coal.
            Switch to &ldquo;No coordination&rdquo; to see every company chase the same clean hours on its own.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            label="Schedule shown"
            value={view}
            onChange={setView}
            options={[
              { value: "coordinated", label: VIEW_LABEL.coordinated },
              { value: "independent_average", label: VIEW_LABEL.independent_average },
            ]}
            testIdPrefix="fleet-view"
          />
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
            style={{ background: CITY.bg }}
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
                  show={!r.removed && hovered !== r.bid.id && (labelAll || r.pending || (labelLit && r.now > EPS_MW))}
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
            {meter && (
              <>
                <div className="pointer-events-none absolute left-3 top-3 hidden sm:block">
                  <Scoreboard meter={meter} view={view} hour={hour} />
                </div>
              </>
            )}
            <Legend pendingCount={pendingCount} removedCount={removedCount} />
          </div>
          <ScheduleSlider hour={hour} counts={runningPerHour} total={cleared.length} sliderRef={sliderRef} onScrub={scrub} />
          {meter && (
            <div className="mt-3 sm:hidden">
              <Scoreboard meter={meter} view={view} hour={hour} />
            </div>
          )}
        </div>

        <HourPanel
          hour={hour}
          running={running}
          total={cleared.length}
          marginal={shown.marginal_resource[hour]}
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
        estimated={response.settlement.method === "marginal"}
      />
    </section>
  );
}

function Legend({ pendingCount, removedCount }: { pendingCount: number; removedCount: number }) {
  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 px-3 pb-2.5 text-[10px] text-ink-2">
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: CITY.gold }} /> Power grid
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full bg-muted" /> Company (taller = bigger load)
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: CITY.green }} /> Drawing power this hour
      </span>
      {pendingCount > 0 && (
        <span className="flex items-center gap-1.5" data-testid="fleet-pending-note">
          <span className="h-1.5 w-1.5 rounded-full border border-dashed border-muted" /> {pendingCount} new, run to
          schedule
        </span>
      )}
      {removedCount > 0 && (
        <span className="flex items-center gap-1.5" data-testid="fleet-removed-note">
          <span className="h-1.5 w-1.5 rounded-sm bg-[#d2c9b8]" /> {removedCount} removed, run to apply
        </span>
      )}
    </div>
  );
}

// --- layout ------------------------------------------------------------------

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
  const streams = useRef<Stream[]>([]);
  const level = useRef(0);
  const landed = useCallback(
    (id: string) => {
      const f = fxRef.current.get(id);
      if (f) f.flash = 1;
    },
    [fxRef],
  );

  // ghosts keep their spot while they come down; everyone else re-flows around them
  const placed = rows.filter((r) => !r.ghost);
  const { spots, radius } = layout(placed.length);
  const spotOf = new Map(placed.map((r, i) => [r.bid.id, spots[i]]));
  const road = radius + 2.6;
  const maxMw = Math.max(1, ...rows.map((r) => (Number.isFinite(r.bid.max_mw) ? r.bid.max_mw : 0)));

  return (
    <>
      <Frame radius={road + 1.2} />
      <CityGround road={road} shadowR={road + 2} />
      <GridHub fxRef={fxRef} count={rows.length} levelRef={level} streamsRef={streams} />
      <GridTower levelRef={level} />

      {rows.map((r, i) => (
        <Building
          key={r.bid.id}
          id={r.bid.id}
          color={r.color}
          height={2 + 3.6 * Math.sqrt(Math.max(0.05, (Number.isFinite(r.bid.max_mw) ? r.bid.max_mw : maxMw / 2) / maxMw))}
          compact={rows.length > 16}
          position={spotOf.get(r.bid.id) ?? [0, 0, 0]}
          mw={r.mw}
          maxMw={r.bid.max_mw}
          pending={r.pending}
          removed={r.removed}
          delay={reduced ? -1 : i * 0.05}
          timeRef={timeRef}
          fxRef={fxRef}
          glowMap={glowMap}
          hovered={hovered === r.bid.id}
          onOver={() => !r.removed && setHovered(r.bid.id)}
          onOut={() => setHovered(null)}
        />
      ))}

      <PulseField streams={streams} onLand={landed} />
      <Projector ids={rows.map((r) => r.bid.id)} hovered={hovered} fxRef={fxRef} labelsRef={labelsRef} tipRef={tipRef} />

      {/* only the lamp and the brightest windows glow, softly: it is a daylight scene */}
      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur luminanceThreshold={1} intensity={0.5} radius={0.6} />
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

/**
 * The grid's side of the replay: how bright the tower is (share of companies drawing power), the
 * rings on its plaza, and one pulse stream to every building that is drawing power this hour.
 */
function GridHub({
  fxRef,
  count,
  levelRef,
  streamsRef,
}: {
  fxRef: RefObject<FxMap>;
  count: number;
  levelRef: RefObject<number>;
  streamsRef: RefObject<Stream[]>;
}) {
  const waves = useRef<THREE.Group>(null);
  const clean = useMemo(() => new THREE.Color(CITY.clean), []);

  useEffect(() => {
    const fx = fxRef.current;
    fx.set(GRID_ID, {
      pos: new THREE.Vector3(0, 0, 0),
      top: TOWER_TOP + 1.4,
      half: TOWER_R,
      active: false,
      load: 0,
      flash: 0,
      glow: 0,
    });
    return () => void fx.delete(GRID_ID);
  }, [fxRef]);

  useFrame((state, dt) => {
    const out: Stream[] = [];
    let on = 0;
    fxRef.current.forEach((f, id) => {
      if (id === GRID_ID || !f.active) return;
      on++;
      const dir = new THREE.Vector3(f.pos.x, 0, f.pos.z).normalize();
      out.push({
        id,
        from: dir.clone().multiplyScalar(TOWER_R).setY(0.35),
        to: f.pos.clone().addScaledVector(dir, -(f.half + 0.1)).setY(0.3),
        every: PULSE_EVERY_S * (1.15 - 0.3 * f.load),
        color: clean,
      });
    });
    streamsRef.current = out;
    const k = 1 - Math.exp(-dt * 5);
    levelRef.current += ((count ? on / count : 0) - levelRef.current) * k;
    const wg = waves.current;
    if (wg) {
      wg.visible = levelRef.current > 0.01;
      wg.children.forEach((ring, i) => {
        const phase = (state.clock.elapsedTime / 2.2 + i / 3) % 1;
        const r = 1 + phase * 1.6;
        ring.scale.set(r, r, 1);
        ((ring as THREE.Mesh).material as THREE.MeshBasicMaterial).opacity = (1 - phase) ** 1.5 * Math.min(0.8, levelRef.current * 2);
      });
    }
  });

  return (
    <group>
      <mesh rotation-x={-Math.PI / 2} position-y={0.012}>
        <ringGeometry args={[2.75, 2.86, 96]} />
        <meshBasicMaterial color={CITY.gold} transparent opacity={0.6} />
      </mesh>
      <group ref={waves} rotation-x={-Math.PI / 2} position-y={0.02} scale={2.8}>
        {[0, 1, 2].map((i) => (
          <mesh key={i}>
            <ringGeometry args={[0.97, 1, 96]} />
            <meshBasicMaterial color={CITY.gold} transparent opacity={0} depthWrite={false} side={THREE.DoubleSide} />
          </mesh>
        ))}
      </group>
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
  removed,
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
  /** removed in the sidebar: plays its demolition, then leaves an empty lot */
  removed: boolean;
  /** seconds before it starts rising; negative = already built (reduced motion) */
  delay: number;
  timeRef: RefObject<number>;
  fxRef: RefObject<FxMap>;
  glowMap: THREE.Texture;
  hovered: boolean;
  onOver: () => void;
  onOut: () => void;
}) {
  const look = useMemo(() => makeLook(id, color, height, compact ? 0.85 : 1), [id, color, height, compact]);
  useEffect(() => () => disposeLook(look), [look]);
  const root = useRef<THREE.Group>(null);
  const vis = useRef<Vis>(newVis());
  const born = useRef<number | null>(null);
  const razed = useRef<number | null>(null);
  const half = Math.max(look.w, look.d) / 2;
  const [x, , z] = position;
  const line = useMemo(() => {
    const dir = new THREE.Vector3(x, 0, z).normalize();
    const len = Math.hypot(x, z);
    return {
      from: dir.clone().multiplyScalar(TOWER_R + 0.9),
      to: dir.clone().multiplyScalar(Math.max(TOWER_R + 1, len - half - 0.3)),
    };
  }, [x, z, half]);

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
      // the stagger only applies to the first build of the whole city, not to later additions
      born.current = delay < 0 ? -1e9 : t + (t < 1.5 ? delay : 0);
    }
    const k = 1 - Math.exp(-dt * 7); // eased, frame-rate independent

    // demolition starts the frame `removed` turns on, and is undone if the company comes back
    if (removed && razed.current === null) razed.current = t;
    if (!removed) razed.current = null;
    const d = razed.current === null ? 0 : Math.min(1, (t - razed.current) / DEMOLISH_S);

    // glide to a new spot when companies are added or removed (a falling building stays put)
    if (!removed) {
      g.position.x += (position[0] - g.position.x) * k;
      g.position.z += (position[2] - g.position.z) * k;
    }
    fx.pos.copy(g.position);
    fx.half = half;

    const p = born.current === null ? 0 : Math.min(1, Math.max(0, (t - born.current) / BUILD_S));
    const grown = p >= 1 ? 1 : Math.max(0.001, easeOutBack(p));
    if (p >= 1 && !removed && fx.top < look.h * 0.99 && fx.flash < 0.5) fx.flash = 0.7; // a sparkle as it tops out
    fx.top = look.h * grown * (1 - d);

    // read the clock here rather than through props: no React render per frame
    const now = mw[Math.min(23, Math.floor(timeRef.current))] ?? 0;
    const on = !pending && !removed && now > EPS_MW;
    fx.active = on && p >= 1;
    fx.load = on ? Math.min(1, maxMw > 0 ? now / maxMw : 1) : 0;
    fx.glow += ((fx.active ? 0.6 + 0.4 * fx.load : 0) - fx.glow) * k;
    fx.flash = Math.max(0, fx.flash - dt * FLASH_DECAY * (fx.flash > 0.35 ? 1 : 0.6));

    const v = vis.current;
    v.p = p;
    v.d = d;
    v.glow = fx.glow;
    v.flash = fx.flash;
    v.hovered = hovered;
  });

  return (
    <>
      <PowerLine
        from={line.from}
        to={line.to}
        faint={pending}
        idle={pending ? "#d8d1c2" : CITY.lineIdle}
        level={() => {
          const f = fxRef.current.get(id);
          return f ? f.glow + f.flash * 0.5 : 0;
        }}
      />
      <group ref={root} position={position}>
        <BuildingMesh
          look={look}
          glowMap={glowMap}
          vis={vis}
          pending={pending}
          phase={position[0] * 0.07}
          onOver={onOver}
          onOut={onOut}
        />
      </group>
    </>
  );
}

// --- labels over the scene ----------------------------------------------------

function GridTag({ elRef }: { elRef: (el: HTMLDivElement | null) => void }) {
  return (
    <div ref={elRef} className="absolute left-0 top-0" style={OFFSCREEN}>
      <div className="-translate-x-1/2 -translate-y-full whitespace-nowrap text-center leading-tight" data-testid="fleet-grid-tag">
        <div className="text-[13px] font-semibold text-ink [text-shadow:0_1px_2px_#f1eee6]">Power grid</div>
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
          on ? "bg-white/85 shadow-sm ring-1 ring-[#2fd27f]/60" : "bg-[#f1eee6]/70"
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

// --- scoreboard over the scene --------------------------------------------------------

type Fuel = "clean" | "gas" | "coal" | "oil";
const FUELS: Fuel[] = ["clean", "gas", "coal", "oil"];
const FUEL_LABEL: Record<Fuel, string> = { clean: "Wind & solar", gas: "Gas", coal: "Coal", oil: "Oil" };
const FUEL_COLOR: Record<Fuel, string> = { clean: "#2fb36f", gas: "#e0a43a", coal: "#d9573f", oil: "#8a4b6e" };

/** Per-hour series behind the scoreboard, for both views. Null for older fixtures. */
interface Meter {
  co2: Record<View, number[]>; // t CO2 the user companies' load causes
  fuel: Record<View, Record<Fuel, number[]>>; // MWh of their load served by each source
}

function meterSeries(response: ClearResponse): Meter | null {
  const views: View[] = ["coordinated", "independent_average"];
  if (views.some((v) => !response.strategies[v].companies_co2_t || !response.strategies[v].companies_fuel_mwh)) return null;
  const series = <T,>(f: (v: View) => T) => ({ coordinated: f("coordinated"), independent_average: f("independent_average") });
  return {
    co2: series((v) => (response.strategies[v].companies_co2_t ?? []).map((t) => Math.max(0, t))), // float noise below zero
    fuel: series((v) => response.strategies[v].companies_fuel_mwh as Record<Fuel, number[]>),
  };
}

/** Where the companies' power has come from so far, both schedules, and the CO2 that saved. */
function Scoreboard({ meter, view, hour }: { meter: Meter; view: View; hour: number }) {
  const order: View[] = ["independent_average", "coordinated"];
  const upTo = (v: View, h: number) =>
    Object.fromEntries(FUELS.map((f) => [f, meter.fuel[v][f].slice(0, h + 1).reduce((a, x) => a + x, 0)])) as Record<Fuel, number>;
  const co2Upto = (v: View, h: number) => meter.co2[v].slice(0, h + 1).reduce((a, x) => a + x, 0);
  const so = co2Upto("independent_average", hour);
  const co = co2Upto("coordinated", hour);
  const saved = so - co;
  const verdict =
    saved > CLEAN_T
      ? { big: `${fmtT(saved)} t CO₂ saved`, small: `${Math.round((saved / so) * 100)}% less than no coordination, so far`, cls: "text-accent" }
      : saved < -CLEAN_T
        ? { big: `${fmtT(-saved)} t behind`, small: "so far today; watch the rest of the day", cls: "text-ink-2" }
        : { big: so > CLEAN_T ? "Even so far" : "No CO₂ yet", small: "", cls: "text-ink-2" };
  const day = upTo("coordinated", 23);
  const dayTotal = FUELS.reduce((a, f) => a + day[f], 0);
  const footer =
    dayTotal > 0 && day.clean >= 0.99 * dayTotal
      ? "Enough spare wind & solar today: everyone runs clean."
      : "Not enough spare wind & solar today for everyone, so some gas or coal is unavoidable.";
  const used = new Set<Fuel>(FUELS.filter((f) => f !== "oil" || order.some((v) => upTo(v, 23).oil > 0)));

  return (
    <div className="w-full rounded-xl bg-white/90 p-3 shadow-md ring-1 ring-line backdrop-blur-sm sm:w-[250px]" data-testid="fleet-scoreboard">
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted sm:text-[11px]">Where their power came from</div>
      <div className="mt-2 grid gap-2">
        {order.map((v) => {
          const mwh = upTo(v, hour);
          const total = FUELS.reduce((a, f) => a + mwh[f], 0);
          return (
            <div key={v} className={v === view ? "" : "opacity-75"}>
              <div className={`text-[11px] ${v === view ? "font-semibold text-ink" : "text-ink-2"}`}>{VIEW_LABEL[v]}</div>
              <div className="mt-0.5 flex h-4 overflow-hidden rounded bg-surface-2">
                {total > EPS_MW ? (
                  FUELS.map((f) => {
                    const share = mwh[f] / total;
                    return (
                      <div
                        key={f}
                        className="num flex items-center justify-center overflow-hidden text-[9.5px] font-semibold"
                        style={{
                          width: `${share * 100}%`,
                          background: FUEL_COLOR[f],
                          color: f === "gas" ? "#15191a" : "#fff", // dark on amber, white on the rest
                          transition: "width 600ms ease-out",
                        }}
                      >
                        {share >= 0.14 ? `${Math.round(share * 100)}%` : ""}
                      </div>
                    );
                  })
                ) : (
                  <span className="px-1.5 text-[9.5px] leading-4 text-muted">no load yet</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-1.5 flex flex-wrap gap-x-2.5 gap-y-0.5 text-[9.5px] text-ink-2">
        {FUELS.filter((f) => used.has(f)).map((f) => (
          <span key={f} className="flex items-center gap-1">
            <span className="h-1.5 w-2.5 rounded-sm" style={{ background: FUEL_COLOR[f] }} /> {FUEL_LABEL[f]}
          </span>
        ))}
      </div>
      <div className={`mt-2.5 text-lg font-semibold leading-tight ${verdict.cls}`} data-testid="fleet-scoreboard-verdict">
        {verdict.big}
      </div>
      {verdict.small && <div className="text-[11px] text-ink-2">{verdict.small}</div>}
      <div className="mt-2 border-t border-dashed border-line-strong pt-1.5 text-[10.5px] leading-snug text-muted">{footer}</div>
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
            ? `${fmtUsd(totalPaid)} paid · ${fmtUsd(carbonPrice)}/t${estimated ? " · by marginal contribution" : ""}`
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
            {rows.filter((r) => !r.ghost).map((r) => {
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
                      <span className={r.removed ? "text-muted line-through" : "text-ink"}>{r.bid.name}</span>
                    </span>
                  </td>
                  <td className={`num py-1.5 pr-2 text-right ${on ? "text-accent" : "text-muted"}`}>
                    {r.removed ? "removed · run to apply" : r.pending ? "run to schedule" : on ? `${fmtInt(r.now)} MW` : "idle"}
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
