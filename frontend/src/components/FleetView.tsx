"use client";

import {
  Environment,
  Lightformer,
  MeshReflectorMaterial,
  OrbitControls,
  useCursor,
  useGLTF,
} from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, ToneMapping } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
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
 * 3D fleet replay of the coordinated schedule: one car per company, lit in the hours the
 * coordinator runs it, with the hour's grid numbers and each company's settlement alongside.
 * Every number comes from the clear response; the cars are decoration over it.
 */

const MODEL_URL = "/models/car.glb"; // built by scripts/prepare_car.py from Khronos' Car Concept
const LITE_ABOVE = 12; // above this many cars, use the lighter model to keep playback smooth
const REFLECT_MAX = 12; // reflective floor up to this many cars (it renders the scene twice)
const PAINT = "#0c0d0f"; // one shared glossy black paint for every car
const BG = "#08090b"; // near-black studio ("neo black" look)
const ACCENT = "#0f5c45"; // --accent
const GLOW = "#36d793"; // bright emerald pad under a running car (added light, so it reads on grey)
const RIPPLES = 3; // rings rippling out under a charging car
const RIPPLE_S = 1.8; // seconds for one ring to travel out and fade
const LAMP_ON = 7; // emissive intensity of lit lamps: well above the bloom threshold
const HOUR_MS = 1000; // playback time per hour of the day
const SPACING_X = 3.4; // metres between cars in a row (the car is 2.7 m wide at the mirrors)
const SPACING_Z = 6.6; // metres between rows (4.6 m long)
// up to this many cars every car gets a name label; up to twice as many, only lit cars do;
// beyond that, only the hovered car's card shows (fewer fit on a phone)
const LABEL_ALL_MAX = { wide: 8, narrow: 4 };
const LABEL_Y = 1.9; // metres above the ground where labels and the hover card attach
const OFFSCREEN = { transform: "translate(-9999px, -9999px)" }; // until the first frame places it
const EPS_MW = 1e-9;

interface FleetRow {
  bid: Bid;
  index: number;
  color: string;
  mw: number[];
  now: number;
  settle: SettlementBid | undefined;
}

const shortName = (name: string) => name.split(" (")[0];

export function FleetView({
  request,
  response,
  bids,
  companies,
}: {
  request: ClearRequest;
  response: ClearResponse;
  bids: Bid[];
  companies: ChartCompany[];
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
  // Continuous time of day in hours (0..24). The cars read it every frame and playback writes
  // the slider straight to the DOM, so React only re-renders when the whole hour changes.
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
  const rows: FleetRow[] = bids.map((bid, index) => {
    const mw = coordinated.schedule[bid.id] ?? [];
    return {
      bid,
      index,
      color: companies.find((c) => c.id === bid.id)?.color ?? "#74776f",
      mw,
      now: mw[hour] ?? 0,
      settle: per.get(bid.id),
    };
  });
  const running = rows.filter((r) => r.now > EPS_MW);
  const runningPerHour = Array.from({ length: 24 }, (_, h) =>
    rows.filter((r) => (r.mw[h] ?? 0) > EPS_MW).length,
  );
  const totalPaid = response.settlement.per_bid.reduce((a, p) => a + p.payout_usd, 0);
  const labelAll = rows.length <= labelMax;
  const labelLit = rows.length <= labelMax * 2;
  const hoveredRow = rows.find((r) => r.bid.id === hovered);

  const play = () => {
    if (ended) seek(0);
    setEnded(false);
    setPlaying(!playing);
  };

  return (
    <section className="card overflow-hidden" data-testid="fleet-view">
      <div className="flex flex-wrap items-end justify-between gap-3 px-4 pt-4 sm:px-5 sm:pt-5">
        <div>
          <div className="kicker mb-1">Fleet replay</div>
          <h3 className="text-lg font-semibold text-ink">Who runs when, and who gets paid</h3>
          <p className="mt-1 max-w-2xl text-xs text-muted">
            Each car is a company. It lights up when it&apos;s scheduled to run. Hover for details.
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
            className="relative isolate h-[380px] overflow-hidden rounded-lg border border-black bg-[#08090b] sm:h-[440px]"
            role="img"
            aria-label={`3D fleet of ${rows.length} companies; ${running.length} running at ${hh(hour)}`}
          >
            <Canvas
              shadows="soft"
              dpr={[1, rows.length > LITE_ABOVE ? 1.5 : 2]} // fewer pixels for big fleets
              camera={{ fov: 35, position: [10, 12, 18] }}
              onPointerMissed={() => setHovered(null)} // tap empty space to close a card
              fallback={<p className="p-4 text-sm text-muted">The 3D fleet needs WebGL, which this browser does not provide.</p>}
            >
              <Suspense fallback={null}>
                <FleetScene
                  rows={rows}
                  timeRef={timeRef}
                  hovered={hovered}
                  setHovered={setHovered}
                  labelsRef={labelsRef}
                  tipRef={tipRef}
                />
              </Suspense>
            </Canvas>
            <div className="pointer-events-none absolute inset-0">
              {rows.map((r) => (
                <NameTag
                  key={r.bid.id}
                  row={r}
                  show={hovered !== r.bid.id && (labelAll || (labelLit && r.now > EPS_MW))}
                  elRef={(el) => {
                    if (el) labelsRef.current.set(r.bid.id, el);
                    else labelsRef.current.delete(r.bid.id);
                  }}
                />
              ))}
              <div ref={tipRef} className="absolute left-0 top-0" style={OFFSCREEN}>
                {hoveredRow && <CarCard row={hoveredRow} noPayoutNote={noPayoutNote} />}
              </div>
            </div>
          </div>
          <ScheduleSlider hour={hour} counts={runningPerHour} total={rows.length} sliderRef={sliderRef} onScrub={scrub} />
          <p className="mt-2 text-[10px] text-muted">
            Car model:{" "}
            <a className="underline" href="https://github.com/KhronosGroup/glTF-Sample-Assets/tree/main/Models/CarConcept" target="_blank" rel="noreferrer">
              &ldquo;Car Concept&rdquo;
            </a>{" "}
            by Eric Chadwick, Darmstadt Graphics Group,{" "}
            <a className="underline" href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noreferrer">
              CC BY 4.0
            </a>
            ; logos removed, simplified and repainted.
          </p>
        </div>

        <HourPanel
          hour={hour}
          running={running}
          total={rows.length}
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
      />
    </section>
  );
}

// --- 3D scene ----------------------------------------------------------------

function layout(n: number): [number, number, number][] {
  const cols = Math.max(1, n <= 5 ? n : Math.ceil(Math.sqrt(n * 2)));
  const rows = Math.ceil(n / cols);
  return Array.from({ length: n }, (_, i) => {
    const r = Math.floor(i / cols);
    const inRow = r === rows - 1 ? n - r * cols : cols; // centre a short last row
    return [((i % cols) - (inRow - 1) / 2) * SPACING_X, 0, (r - (rows - 1) / 2) * SPACING_Z];
  });
}

function FleetScene({
  rows,
  timeRef,
  hovered,
  setHovered,
  labelsRef,
  tipRef,
}: {
  rows: FleetRow[];
  timeRef: RefObject<number>;
  hovered: string | null;
  setHovered: (id: string | null) => void;
  labelsRef: RefObject<Map<string, HTMLDivElement>>;
  tipRef: RefObject<HTMLDivElement | null>;
}) {
  const { scene } = useGLTF(MODEL_URL);
  const paint = useMemo(
    () =>
      new THREE.MeshPhysicalMaterial({
        color: PAINT,
        roughness: 0.32,
        metalness: 0.45,
        clearcoat: 1,
        clearcoatRoughness: 0.04,
      }),
    [],
  );
  const glowMap = useMemo(() => glowTexture(), []);
  useEffect(() => () => { paint.dispose(); glowMap.dispose(); }, [paint, glowMap]);
  useCursor(hovered !== null);

  const spots = layout(rows.length);
  const xs = spots.map((p) => p[0]);
  const zs = spots.map((p) => p[2]);
  const width = Math.max(...xs, 0) - Math.min(...xs, 0) + SPACING_X;
  const depth = Math.max(...zs, 0) - Math.min(...zs, 0) + SPACING_Z;

  return (
    <>
      <Frame width={width} depth={depth} />
      <color attach="background" args={[BG]} />
      <fog attach="fog" args={[BG, 30 + depth, 70 + depth * 2]} />
      {/* soft studio reflections for the paint and glass, built locally (no HDR download) */}
      {/* long strip lights, as in a car studio: they draw the highlight lines along the body */}
      <Environment resolution={256} frames={1} environmentIntensity={1}>
        <color attach="background" args={["#3a3b3d"]} />
        {[-4, 0, 4].map((z) => (
          <Lightformer key={z} intensity={0.9} position={[0, 7, z]} rotation-x={Math.PI / 2} scale={[24, 0.6, 1]} />
        ))}
        {/* key side: a tall bright softbox on the left draws the highlight down every flank */}
        <Lightformer intensity={4} position={[-12, 2.5, 0]} rotation-y={Math.PI / 2} scale={[30, 3, 1]} />
        <Lightformer intensity={0.4} position={[12, 2.5, 0]} rotation-y={-Math.PI / 2} scale={[24, 1, 1]} />
        <Lightformer intensity={0.3} position={[0, 1.5, 14]} scale={[24, 1, 1]} />
      </Environment>
      <hemisphereLight args={["#cfd6e0", "#0a0b0d", 0.15]} />
      {/* low side light from the left: lights the flanks and throws long shadows across the floor */}
      <directionalLight
        position={[-16, 7, 5]}
        intensity={3.2}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
        shadow-normalBias={0.03}
        shadow-camera-left={-(Math.max(width, depth) / 2 + 8)}
        shadow-camera-right={Math.max(width, depth) / 2 + 8}
        shadow-camera-top={Math.max(width, depth) / 2 + 8}
        shadow-camera-bottom={-(Math.max(width, depth) / 2 + 8)}
        shadow-camera-near={1}
        shadow-camera-far={60}
      />

      <mesh rotation-x={-Math.PI / 2} position-y={-0.001} receiveShadow>
        <planeGeometry args={[400, 400]} />
        {rows.length <= REFLECT_MAX ? (
          // softly blurred showroom reflections of the cars
          <MeshReflectorMaterial
            color="#121418"
            roughness={0.75}
            metalness={0.4}
            blur={[400, 120]}
            mixBlur={1}
            mixStrength={1.2}
            resolution={512}
            depthScale={1}
            minDepthThreshold={0.6}
            maxDepthThreshold={1.2}
          />
        ) : (
          <meshStandardMaterial color="#121418" roughness={0.8} metalness={0.3} />
        )}
      </mesh>
      {spots.map((p, i) => (
        <ParkingLines key={i} x={p[0]} z={p[2]} />
      ))}

      {rows.map((r, i) => {
        const template = scene.getObjectByName(rows.length > LITE_ABOVE ? "car_lite" : "car");
        if (!template) return null;
        const isHovered = hovered === r.bid.id;
        return (
          <Car
            key={r.bid.id}
            template={template}
            paint={paint}
            glowMap={glowMap}
            position={spots[i]}
            mw={r.mw}
            maxMw={r.bid.max_mw}
            timeRef={timeRef}
            hovered={isHovered}
            onOver={() => setHovered(r.bid.id)}
            onOut={() => setHovered(null)}
          />
        );
      })}

      <Projector ids={rows.map((r) => r.bid.id)} spots={spots} hovered={hovered} labelsRef={labelsRef} tipRef={tipRef} />

      {/* headlights and taillights above full brightness glow; film-like tone mapping */}
      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur luminanceThreshold={1} intensity={0.9} radius={0.6} />
        <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
      </EffectComposer>

      <OrbitControls
        makeDefault
        enablePan={false}
        enableZoom={false}
        minPolarAngle={0.35}
        maxPolarAngle={1.2}
        target={[0, 0, 0]}
      />
    </>
  );
}

/** Points the camera at the lot from a distance that fits every car at this canvas size. */
function Frame({ width, depth }: { width: number; depth: number }) {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const aspect = useThree((s) => s.size.width / Math.max(1, s.size.height));
  useEffect(() => {
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const fitWidth = (width * 0.62) / (aspect * tanHalf); // the angled lot fits with a margin
    const fitDepth = (depth * 0.45) / tanHalf; // rows recede, so depth needs less room
    const d = Math.max(fitWidth, fitDepth, 10);
    // a low three-quarter view, like a car photo
    camera.position.copy(new THREE.Vector3(0.55, 0.48, 0.8).normalize().multiplyScalar(d));
    camera.lookAt(0, 0, 0);
  }, [camera, aspect, width, depth]);
  return null;
}

/** Moves the DOM name labels and the hover card to their cars' screen positions each frame. */
function Projector({
  ids,
  spots,
  hovered,
  labelsRef,
  tipRef,
}: {
  ids: string[];
  spots: [number, number, number][];
  hovered: string | null;
  labelsRef: RefObject<Map<string, HTMLDivElement>>;
  tipRef: RefObject<HTMLDivElement | null>;
}) {
  useFrame(({ camera, size }) => {
    const toScreen = (x: number, y: number, z: number) => {
      const v = new THREE.Vector3(x, y, z).project(camera);
      return [((v.x + 1) / 2) * size.width, ((1 - v.y) / 2) * size.height];
    };
    ids.forEach((id, i) => {
      const el = labelsRef.current.get(id);
      if (!el) return;
      const [sx, sy] = toScreen(spots[i][0], LABEL_Y, spots[i][2]);
      el.style.transform = `translate(${sx}px, ${sy}px)`;
    });
    const tip = tipRef.current;
    const i = hovered ? ids.indexOf(hovered) : -1;
    if (tip && i >= 0) {
      const [sx, sy] = toScreen(spots[i][0], LABEL_Y + 0.2, spots[i][2]);
      const left = Math.min(Math.max(8, sx - tip.offsetWidth / 2), size.width - tip.offsetWidth - 8);
      const top = Math.max(8, sy - tip.offsetHeight - 10);
      tip.style.transform = `translate(${left}px, ${top}px)`;
    }
  });
  return null;
}

function ParkingLines({ x, z }: { x: number; z: number }) {
  const half = SPACING_X / 2;
  return (
    <group position={[x, 0.004, z]}>
      {[-half, half].map((dx) => (
        <mesh key={dx} rotation-x={-Math.PI / 2} position-x={dx}>
          <planeGeometry args={[0.07, 5]} />
          <meshBasicMaterial color="#3a3e45" />
        </mesh>
      ))}
    </group>
  );
}

/** Soft radial glow drawn once to a canvas; stretched under each lit car. */
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

function Car({
  template,
  paint,
  glowMap,
  position,
  mw,
  maxMw,
  timeRef,
  hovered,
  onOver,
  onOut,
}: {
  template: THREE.Object3D;
  paint: THREE.Material;
  glowMap: THREE.Texture;
  position: [number, number, number];
  /** coordinated MW per hour */
  mw: number[];
  maxMw: number;
  timeRef: RefObject<number>;
  hovered: boolean;
  onOver: () => void;
  onOut: () => void;
}) {
  const body = useRef<THREE.Group>(null);
  const glow = useRef<THREE.MeshBasicMaterial>(null);
  const ripples = useRef<THREE.Group>(null);
  const level = useRef(0); // eased 0..1: how strongly the ripples show
  const { obj, lamps } = useMemo(() => {
    const obj = template.clone(true);
    const lamps: THREE.MeshStandardMaterial[] = [];
    obj.traverse((c) => {
      const m = c as THREE.Mesh;
      if (!m.isMesh) return;
      m.castShadow = true;
      m.receiveShadow = true;
      const mat = m.material as THREE.MeshStandardMaterial;
      if (mat.name === "Body") {
        m.material = paint;
      } else if (mat.name === "Headlight" || mat.name === "Taillight") {
        const own = mat.clone(); // per car, so each car's lamps switch on alone
        own.emissive = new THREE.Color(mat.name === "Headlight" ? "#e6f4ff" : "#ff2a1a");
        own.emissiveIntensity = 0;
        own.userData.lamp = true; // found again by useFrame through the body ref
        m.material = own;
        lamps.push(own);
      }
    });
    obj.position.set(0, 0, 0);
    obj.rotation.set(0, -Math.PI / 2, 0); // model faces +X; face the camera (+Z)
    return { obj, lamps };
  }, [template, paint]);
  useEffect(() => () => lamps.forEach((l) => l.dispose()), [lamps]);

  useFrame((state, dt) => {
    // read the clock here rather than through props: no React render per frame
    const now = mw[Math.min(23, Math.floor(timeRef.current))] ?? 0;
    const on = now > EPS_MW;
    const load = on ? Math.min(1, maxMw > 0 ? now / maxMw : 1) : 0;
    const k = 1 - Math.exp(-dt * 7); // eased, frame-rate independent
    const g = body.current;
    if (g) {
      const lift = on ? 0.1 + Math.sin(state.clock.elapsedTime * 3 + position[0]) * 0.025 : 0;
      g.position.y += (lift - g.position.y) * k;
      const s = hovered ? 1.07 : 1;
      g.scale.setScalar(g.scale.x + (s - g.scale.x) * k);
      g.traverse((c) => {
        const m = (c as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
        if (m?.userData.lamp) m.emissiveIntensity += ((on ? LAMP_ON : 0) - m.emissiveIntensity) * k;
      });
    }
    if (glow.current) glow.current.opacity += ((on ? 0.45 + 0.45 * load : 0) - glow.current.opacity) * k;
    // charging ripple: rings expand from the car across the floor and fade, staggered in time
    level.current += ((on ? 0.5 + 0.5 * load : 0) - level.current) * k;
    const rg = ripples.current;
    if (rg) {
      rg.visible = level.current > 0.01;
      rg.children.forEach((ring, i) => {
        const phase = (state.clock.elapsedTime / RIPPLE_S + i / RIPPLES + position[0] * 0.07) % 1;
        const r = 1 + phase * 1.15; // stays mostly inside its own parking space
        ring.scale.set(r, r, 1);
        ((ring as THREE.Mesh).material as THREE.MeshBasicMaterial).opacity = (1 - phase) ** 1.6 * level.current;
      });
    }
  });

  return (
    <group position={position}>
      <group ref={ripples} rotation-x={-Math.PI / 2} position-y={0.02} scale={[1.5, 2.55, 1]} visible={false}>
        {Array.from({ length: RIPPLES }, (_, i) => (
          // a car-shaped (elliptical) ring hugging the footprint, 4.6 m long x 2.7 m wide
          <mesh key={i}>
            <ringGeometry args={[0.96, 1, 64]} />
            <meshBasicMaterial
              color={GLOW}
              transparent
              opacity={0}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
              toneMapped={false}
              side={THREE.DoubleSide}
            />
          </mesh>
        ))}
      </group>
      <mesh rotation-x={-Math.PI / 2} position-y={0.012}>
        <planeGeometry args={[3.2, 5.6]} />
        <meshBasicMaterial
          ref={glow}
          map={glowMap}
          color={GLOW}
          transparent
          opacity={0}
          depthWrite={false}
          blending={THREE.AdditiveBlending}
          toneMapped={false}
        />
      </mesh>
      <group
        ref={body}
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
        <primitive object={obj} />
      </group>
    </group>
  );
}

useGLTF.preload(MODEL_URL);

// --- labels over the scene ----------------------------------------------------

function NameTag({
  row,
  show,
  elRef,
}: {
  row: FleetRow;
  show: boolean;
  elRef: (el: HTMLDivElement | null) => void;
}) {
  const on = row.now > EPS_MW;
  return (
    <div ref={elRef} className="absolute left-0 top-0" style={OFFSCREEN}>
      <div
        className={`${show ? "flex" : "hidden"} -translate-x-1/2 -translate-y-1/2 items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[10px] font-medium shadow-sm ${
          on ? "border-[#36d793]/50 bg-black/70 text-white" : "border-white/10 bg-black/50 text-white/55"
        }`}
      >
        <span className="h-1.5 w-1.5 rounded-full" style={{ background: row.color }} />
        {shortName(row.bid.name)}
        {on && <span className="num text-[#36d793]">· {fmtInt(row.now)} MW</span>}
      </div>
    </div>
  );
}

function CarCard({ row, noPayoutNote }: { row: FleetRow; noPayoutNote: string }) {
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
      <p className="mt-1 text-ink-2">
        {row.now > EPS_MW ? (
          <span className="font-medium text-accent">Running now at {fmtInt(row.now)} MW.</span>
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
}: {
  rows: FleetRow[];
  hovered: string | null;
  setHovered: (id: string | null) => void;
  hasPayouts: boolean;
  noPayoutNote: string;
  totalPaid: number;
  carbonPrice: number;
}) {
  return (
    <div className="border-t border-line px-4 pb-4 pt-3 sm:px-5 sm:pb-5">
      <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-[11px] font-medium uppercase tracking-wider text-muted">
          Payouts for the day
        </div>
        <div className="num text-xs text-muted">
          {hasPayouts ? `${fmtUsd(totalPaid)} paid · ${fmtUsd(carbonPrice)}/t` : noPayoutNote}
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
                    {on ? `${fmtInt(r.now)} MW` : "idle"}
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
