"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, ToneMapping } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import { Suspense, useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import * as THREE from "three";
import { fmtUsd } from "@/lib/format";
import {
  BuildingMesh,
  CITY,
  CityGround,
  disposeLook,
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

/**
 * The story page's herding explainer as a live scene in the same light city style as the
 * results replay: four companies around the grid tower, wind and solar on one side, a gas plant
 * on the other. The page's scroll position (frame 0..479, written to `frameRef`) drives who
 * draws power and from where; pulses, turbines and smoke move in real time. Illustrative: the
 * acts follow the captions in HerdingScroll, the payouts are a real /api/clear run.
 */

export interface StoryCompany {
  id: string;
  label: string;
  color: string;
  maxMw: number;
}

export interface StoryPayout {
  usd: number;
  share: number; // of all payouts, 0..1
}

// act boundaries, matching the captions in HerdingScroll
const HERD_ON = 95; // everyone draws in the same hour
const RUSH_DRY = 185; // the clean core is empty
const COORD_ON = 262; // the clearinghouse takes over: companies take turns
const COORD_OFF = 400;
const PAY_ON = 420;

const RING_R = 6.8;
const ROAD = RING_R + 2.6;
const SOURCE_X = 12.2;
const GAUGE_SEGS = 96;

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ramp = (f: number, a: number, b: number) => clamp01((f - a) / (b - a));

/** Clean energy left in the grid's core, 0..1. */
function coreAt(f: number): number {
  if (f < HERD_ON) return 1;
  if (f < RUSH_DRY) return 1 - ramp(f, HERD_ON, RUSH_DRY);
  if (f < 243) return 0;
  if (f < 290) return 0.7 * ramp(f, 243, 290);
  if (f < 412) return 0.7 + 0.08 * Math.sin((f - 290) / 12);
  return 0.78;
}

/** How much of the load gas and coal are covering, 0..1. */
const fossilAt = (f: number) => (f < 243 ? ramp(f, 178, 192) : 1 - ramp(f, 243, 256));

function activeAt(i: number, f: number): boolean {
  if (f >= HERD_ON && f < 243) return true;
  if (f >= COORD_ON && f < COORD_OFF) return Math.floor(((f - COORD_ON) / (COORD_OFF - COORD_ON)) * 4) === i;
  return false;
}

/** Corner spots, like the reference: back-left, back-right, front-left, front-right. */
const SPOTS: [number, number, number][] = [-0.75, -0.25, 0.75, 0.25].map((a) => [
  Math.cos(a * Math.PI) * RING_R,
  0,
  Math.sin(a * Math.PI) * RING_R,
]);

export default function GridStory({
  frameRef,
  companies,
  payouts,
}: {
  frameRef: RefObject<number>;
  companies: StoryCompany[];
  payouts: Record<string, StoryPayout> | null;
}) {
  const labels = useRef(new Map<string, HTMLDivElement>());
  const status = useRef(new Map<string, HTMLSpanElement>());
  const payoutsRef = useRef(payouts);
  useEffect(() => {
    payoutsRef.current = payouts;
  }, [payouts]);

  return (
    <div className="absolute inset-0" style={{ background: CITY.bg }}>
      <Canvas
        shadows="soft"
        dpr={[1, 2]}
        camera={{ fov: 30, position: [0, 20, 28] }}
        fallback={<p className="p-4 text-sm text-muted">The animation needs WebGL, which this browser does not provide.</p>}
      >
        <Suspense fallback={null}>
          <StoryScene frameRef={frameRef} companies={companies} payoutsRef={payoutsRef} labels={labels} status={status} />
        </Suspense>
      </Canvas>
      <div className="pointer-events-none absolute inset-0 text-center leading-tight" aria-hidden>
        <Tag id="grid" map={labels}>
          <div className="text-[13px] font-semibold text-ink">Power grid</div>
          <Status id="grid" map={status} />
        </Tag>
        <Tag id="clean" map={labels}>
          <div className="text-[11px] font-semibold text-ink-2">Wind + solar</div>
        </Tag>
        <Tag id="gas" map={labels}>
          <div className="text-[11px] font-semibold text-ink-2">Gas &amp; coal</div>
        </Tag>
        {companies.map((c) => (
          <Tag key={c.id} id={c.id} map={labels}>
            <div className="rounded-md bg-[#f1eee6]/75 px-1.5 py-0.5">
              <div className="flex items-center justify-center gap-1 text-[12px] font-semibold text-ink">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: c.color }} />
                {c.label}
              </div>
              <div className="num text-[10px] text-muted">{c.maxMw} MW flexible</div>
              <Status id={c.id} map={status} />
            </div>
          </Tag>
        ))}
      </div>
    </div>
  );
}

/** A label over the scene; the frame loop moves it by writing its transform. */
function Tag({ id, map, children }: { id: string; map: RefObject<Map<string, HTMLDivElement>>; children: ReactNode }) {
  return (
    <div
      ref={(el) => {
        if (el) map.current.set(id, el);
        else map.current.delete(id);
      }}
      className="absolute left-0 top-0 [text-shadow:0_1px_2px_#f1eee6]" style={{ transform: "translate(-9999px, -9999px)" }}>
      <div className="-translate-x-1/2 -translate-y-full whitespace-nowrap">{children}</div>
    </div>
  );
}

/** A label's status line; the frame loop writes its text. */
function Status({ id, map }: { id: string; map: RefObject<Map<string, HTMLSpanElement>> }) {
  return (
    <span
      ref={(el) => {
        if (el) map.current.set(id, el);
        else map.current.delete(id);
      }}
      className="num block text-[10px] font-medium text-ink-2"
    />
  );
}

function StoryScene({
  frameRef,
  companies,
  payoutsRef,
  labels,
  status,
}: {
  frameRef: RefObject<number>;
  companies: StoryCompany[];
  payoutsRef: RefObject<Record<string, StoryPayout> | null>;
  labels: RefObject<Map<string, HTMLDivElement>>;
  status: RefObject<Map<string, HTMLSpanElement>>;
}) {
  const glowMap = useMemo(() => glowTexture(), []);
  useEffect(() => () => glowMap.dispose(), [glowMap]);
  const maxMw = Math.max(1, ...companies.map((c) => c.maxMw));
  const looks = useMemo(
    () => companies.map((c) => makeLook(`story-${c.id}`, c.color, 2 + 3.6 * Math.sqrt(c.maxMw / maxMw))),
    [companies, maxMw],
  );
  useEffect(() => () => looks.forEach(disposeLook), [looks]);
  const visRefs = useMemo(() => companies.map((): { current: Vis } => ({ current: newVis() })), [companies]);

  const towerLevel = useRef(0);
  const core = useRef(1);
  const fossil = useRef(0);
  const streams = useRef<Stream[]>([]);
  const flash = useRef(new Map<string, number>());
  const lastStatus = useRef(new Map<string, string>());
  const colors = useMemo(
    () => ({
      clean: new THREE.Color(CITY.clean),
      fossil: new THREE.Color(CITY.fossil),
      pay: new THREE.Color(CITY.pay),
      green: new THREE.Color(CITY.green),
      coral: new THREE.Color("#ef8a63"),
      gold: new THREE.Color(CITY.gold),
    }),
    [],
  );
  const v = useMemo(() => new THREE.Vector3(), []);

  const ends = useMemo(
    () =>
      SPOTS.map((s, i) => {
        const dir = new THREE.Vector3(s[0], 0, s[2]).normalize();
        const half = Math.max(looks[i]?.w ?? 1.6, looks[i]?.d ?? 1.6) / 2;
        return {
          lineFrom: dir.clone().multiplyScalar(TOWER_R + 0.9),
          lineTo: dir.clone().multiplyScalar(RING_R - half - 0.3),
          from: dir.clone().multiplyScalar(TOWER_R).setY(0.35),
          to: dir.clone().multiplyScalar(RING_R - half - 0.1).setY(0.3),
        };
      }),
    [looks],
  );
  const cleanIn = useMemo(
    () => ({ from: new THREE.Vector3(-SOURCE_X + 1.6, 0.35, 0), to: new THREE.Vector3(-TOWER_R, 0.35, 0) }),
    [],
  );
  const gasIn = useMemo(
    () => ({ from: new THREE.Vector3(SOURCE_X - 1.6, 0.35, 0), to: new THREE.Vector3(TOWER_R, 0.35, 0) }),
    [],
  );

  const setStatus = (id: string, text: string, tone: string) => {
    const key = `${text}|${tone}`;
    if (lastStatus.current.get(id) === key) return;
    lastStatus.current.set(id, key);
    const el = status.current.get(id);
    if (!el) return;
    el.textContent = text;
    el.className = `num block text-[10px] font-medium ${tone || "text-ink-2"}`;
  };

  useFrame(({ camera, size }, dt) => {
    const f = frameRef.current ?? 0;
    const k = 1 - Math.exp(-dt * 6);
    core.current += (coreAt(f) - core.current) * k;
    fossil.current += (fossilAt(f) - fossil.current) * k;
    const fo = fossil.current;
    const pay = payoutsRef.current;

    const out: Stream[] = [
      { id: "wind", from: cleanIn.from, to: cleanIn.to, every: 0.55, color: colors.clean },
    ];
    if (fo > 0.3) out.push({ id: "gas", from: gasIn.from, to: gasIn.to, every: 0.28, color: colors.fossil });
    let on = 0;
    companies.forEach((c, i) => {
      const vi = visRefs[i].current;
      vi.p = ramp(f, 6 + i * 9, 32 + i * 9);
      const active = vi.p >= 1 && activeAt(i, f);
      if (active) {
        on++;
        out.push({ id: c.id, from: ends[i].from, to: ends[i].to, every: 0.3, color: fo > 0.5 ? colors.fossil : colors.clean });
      }
      const share = pay?.[c.id]?.share ?? 0;
      if (f >= PAY_ON && share > 0.01) {
        out.push({ id: `pay-${c.id}`, from: ends[i].from, to: ends[i].to, every: Math.min(2.5, 0.12 / share), color: colors.pay });
      }
      vi.glow += ((active ? 0.85 : 0) - vi.glow) * k;
      const fl = Math.max(0, (flash.current.get(c.id) ?? 0) - dt * 2.4);
      flash.current.set(c.id, fl);
      vi.flash = fl;
      vi.color.copy(f >= PAY_ON ? colors.gold : colors.green).lerp(colors.coral, f < PAY_ON ? fo : 0);

      // the status line under each label
      let s = "";
      let tone = "text-muted";
      if (f >= PAY_ON) {
        const p = pay?.[c.id];
        s = p ? `paid ${fmtUsd(p.usd)} · ${(p.share * 100).toFixed(0)}%` : "";
        tone = "text-[#a07818]";
      } else if (active) {
        s = fo > 0.5 ? "drawing gas & coal" : "drawing clean power";
        tone = fo > 0.5 ? "text-danger" : "text-[#0f8a55]";
      } else if (vi.p >= 1) {
        s = f >= COORD_ON && f < COORD_OFF ? "waiting its turn" : "idle";
      }
      setStatus(c.id, s, tone);
    });
    streams.current = out;
    towerLevel.current += (Math.min(1, on / 2) - towerLevel.current) * k;
    setStatus(
      "grid",
      fo > 0.5 ? "clean energy used up · gas & coal on" : `clean energy left ${Math.round(core.current * 100)}%`,
      "",
    );

    // labels follow their buildings
    const place = (id: string, x: number, y: number, z: number) => {
      const el = labels.current.get(id);
      if (!el) return;
      v.set(x, y, z).project(camera);
      el.style.transform = `translate(${((v.x + 1) / 2) * size.width}px, ${((1 - v.y) / 2) * size.height}px)`;
    };
    place("grid", 0, TOWER_TOP + 2, 0);
    place("clean", -SOURCE_X, 5.6, -1.4);
    place("gas", SOURCE_X, 4.4, 0);
    companies.forEach((c, i) => {
      const h = looks[i].h * Math.min(1, visRefs[i].current.p * 1.2);
      place(c.id, SPOTS[i][0], h + 0.7, SPOTS[i][2]);
    });
  });



  return (
    <>
      <Frame />
      <CityGround road={ROAD} shadowR={SOURCE_X + 4} />
      <GridTower levelRef={towerLevel}>
        <CleanGauge coreRef={core} />
      </GridTower>

      {companies.map((c, i) => (
        <group key={c.id}>
          <PowerLine
            from={ends[i].lineFrom}
            to={ends[i].lineTo}
            live={CITY.green}
            level={() => visRefs[i].current.glow + visRefs[i].current.flash * 0.5}
          />
          <group position={SPOTS[i]}>
            <BuildingMesh look={looks[i]} glowMap={glowMap} vis={visRefs[i]} phase={i * 0.23} />
          </group>
        </group>
      ))}

      <PowerLine from={cleanIn.from} to={cleanIn.to.clone().setX(-TOWER_R - 0.9)} live={CITY.green} level={() => 0.55} />
      <PowerLine from={gasIn.from} to={gasIn.to.clone().setX(TOWER_R + 0.9)} live="#ef8a63" level={() => fossil.current} />
      <WindAndSolar />
      <GasPlant fossilRef={fossil} />

      <PulseField
        streams={streams}
        onLand={(id) => {
          const cid = id.startsWith("pay-") ? id.slice(4) : id;
          if (flash.current.has(cid)) flash.current.set(cid, 1);
        }}
      />

      <EffectComposer multisampling={4}>
        <Bloom mipmapBlur luminanceThreshold={1} intensity={0.5} radius={0.6} />
        <ToneMapping mode={ToneMappingMode.NEUTRAL} />
      </EffectComposer>
    </>
  );
}

/** Fixed three-quarter view that fits the sources on both sides at this canvas size. */
function Frame() {
  const camera = useThree((s) => s.camera) as THREE.PerspectiveCamera;
  const aspect = useThree((s) => s.size.width / Math.max(1, s.size.height));
  useEffect(() => {
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
    const fitWidth = ((SOURCE_X + 2.4) * 1.02) / (aspect * tanHalf);
    const fitDepth = (ROAD * 0.62 + 2) / tanHalf;
    const d = Math.max(fitWidth, fitDepth) * 1.1; // headroom for the labels over the back buildings
    camera.position.copy(new THREE.Vector3(0, 0.7, 1).normalize().multiplyScalar(d)).add(new THREE.Vector3(0, 2.4, 0));
    camera.lookAt(0, 2.4, 0);
  }, [camera, aspect]);
  return null;
}

/** An arc around the plaza: how much spare clean energy the grid has left. */
function CleanGauge({ coreRef }: { coreRef: RefObject<number> }) {
  const fill = useRef<THREE.Mesh>(null);
  const green = useMemo(() => new THREE.Color(CITY.green), []);
  const coral = useMemo(() => new THREE.Color("#ef8a63"), []);
  useFrame(() => {
    const m = fill.current;
    if (!m) return;
    const level = clamp01(coreRef.current ?? 0);
    m.geometry.setDrawRange(0, Math.round(level * GAUGE_SEGS) * 6);
    (m.material as THREE.MeshBasicMaterial).color.copy(coral).lerp(green, clamp01(level * 2.5));
  });
  return (
    <group rotation-x={-Math.PI / 2} position-y={0.02}>
      <mesh>
        <ringGeometry args={[2.9, 3.25, GAUGE_SEGS]} />
        <meshBasicMaterial color="#e2dac8" />
      </mesh>
      <mesh ref={fill} position-z={0.002}>
        <ringGeometry args={[2.9, 3.25, GAUGE_SEGS, 1, Math.PI / 2, Math.PI * 2]} />
        <meshBasicMaterial color={CITY.green} />
      </mesh>
    </group>
  );
}

function WindAndSolar() {
  const rotors = useRef<THREE.Group[]>([]);
  useFrame((_, dt) => rotors.current.forEach((r, i) => r && (r.rotation.z -= dt * (1.1 + i * 0.25))));
  const turbines: [number, number, number][] = [
    [-SOURCE_X - 0.9, 0, -2.6],
    [-SOURCE_X + 1.1, 0, -4.4],
  ];
  return (
    <group>
      {turbines.map((p, i) => (
        <group key={i} position={p}>
          <mesh position-y={2.3} castShadow>
            <cylinderGeometry args={[0.07, 0.13, 4.6, 10]} />
            <meshStandardMaterial color="#f6f3ec" roughness={0.7} />
          </mesh>
          <mesh position={[0, 4.65, 0.05]} castShadow>
            <boxGeometry args={[0.28, 0.24, 0.5]} />
            <meshStandardMaterial color="#efebe2" />
          </mesh>
          <group
            position={[0, 4.65, 0.34]}
            ref={(g) => {
              if (g) rotors.current[i] = g;
            }}
          >
            {[0, 1, 2].map((b) => (
              <mesh key={b} rotation-z={(b * Math.PI * 2) / 3} position={[0, 0, 0]} castShadow>
                <boxGeometry args={[0.11, 1.9, 0.04]} />
                <meshStandardMaterial color="#fbf9f4" />
              </mesh>
            ))}
          </group>
        </group>
      ))}
      {/* a small solar field */}
      {[0, 1].map((row) =>
        [0, 1, 2].map((col) => (
          <group key={`${row}-${col}`} position={[-SOURCE_X - 1.2 + col * 1.25, 0, 1.4 + row * 1.25]}>
            <mesh position-y={0.25}>
              <boxGeometry args={[0.06, 0.5, 0.06]} />
              <meshStandardMaterial color="#b9b2a6" />
            </mesh>
            <mesh position-y={0.55} rotation-x={-0.55} castShadow>
              <boxGeometry args={[1.1, 0.05, 0.8]} />
              <meshStandardMaterial color="#8ea3b8" roughness={0.35} metalness={0.2} />
            </mesh>
          </group>
        )),
      )}
    </group>
  );
}

/** Low plant with two stacks; smoke rises from them while gas and coal cover the load. */
function GasPlant({ fossilRef }: { fossilRef: RefObject<number> }) {
  const puffs = useRef<THREE.Mesh[]>([]);
  const embers = useRef<THREE.MeshBasicMaterial[]>([]);
  const PUFFS = 8;
  const stacks: [number, number][] = [
    [SOURCE_X - 0.5, -0.9],
    [SOURCE_X + 0.6, -0.5],
  ];
  useFrame((state) => {
    const fo = fossilRef.current ?? 0;
    const t = state.clock.elapsedTime;
    puffs.current.forEach((m, n) => {
      if (!m) return;
      const j = n % PUFFS;
      const ph = (t * 0.35 + j / PUFFS) % 1;
      m.position.y = 3.3 + ph * 3.2;
      m.position.x = stacks[Math.floor(n / PUFFS)][0] + ph * 0.8;
      m.scale.setScalar(0.25 + ph * 0.75);
      (m.material as THREE.MeshStandardMaterial).opacity = fo * (1 - ph) * 0.55;
      m.visible = fo > 0.01;
    });
    embers.current.forEach((e) => e && (e.opacity = fo));
  });
  return (
    <group>
      <mesh position={[SOURCE_X, 0.7, 0.6]} castShadow receiveShadow>
        <boxGeometry args={[2.8, 1.4, 1.9]} />
        <meshStandardMaterial color="#d3cbbd" roughness={0.9} />
      </mesh>
      <mesh position={[SOURCE_X - 0.4, 1.55, 0.6]} castShadow>
        <boxGeometry args={[1.4, 0.3, 1.5]} />
        <meshStandardMaterial color="#c4bbab" roughness={0.9} />
      </mesh>
      {stacks.map(([x, z], i) => (
        <group key={i} position={[x, 0, z]}>
          <mesh position-y={1.6} castShadow>
            <cylinderGeometry args={[0.24, 0.3, 3.2, 14]} />
            <meshStandardMaterial color="#c9c0b1" roughness={0.85} />
          </mesh>
          <mesh position-y={2.7}>
            <cylinderGeometry args={[0.255, 0.255, 0.2, 14]} />
            <meshStandardMaterial color="#d97d5c" />
          </mesh>
          <mesh position-y={3.22} rotation-x={-Math.PI / 2}>
            <ringGeometry args={[0.12, 0.24, 20]} />
            <meshBasicMaterial
              ref={(m) => {
                if (m) embers.current[i] = m;
              }}
              color="#ff9a6a"
              transparent
              opacity={0}
            />
          </mesh>
        </group>
      ))}
      {Array.from({ length: PUFFS * stacks.length }, (_, n) => (
        <mesh
          key={n}
          position-z={stacks[Math.floor(n / PUFFS)][1]}
          ref={(m) => {
            if (m) puffs.current[n] = m;
          }}
          visible={false}
        >
          <sphereGeometry args={[0.5, 12, 10]} />
          <meshStandardMaterial color="#c7c0b6" transparent opacity={0} depthWrite={false} roughness={1} />
        </mesh>
      ))}
    </group>
  );
}
