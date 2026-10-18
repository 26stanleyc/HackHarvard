"use client";

import { Grid, Line } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { useEffect, useLayoutEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import * as THREE from "three";

/**
 * Shared pieces of the light "architecture model" city used by the story explainer and the
 * results replay: seeded company buildings, the stepped grid tower, the paper ground with its
 * ring road, and the light pulses that travel along the power lines.
 */

export const CITY = {
  bg: "#f1eee6", // close to the page's surface-2, so the scene sits in the card
  ground: "#ebe7dd",
  cell: "#e1dccf",
  section: "#d8d1c0",
  road: "#e0d9c9",
  green: "#3fcf8a", // charging glow, a lighter take on the site accent
  gold: "#e3b54f",
  lineIdle: "#d3c29c",
  clean: "#9fe8c1", // pulse colors: soft, so they read as light on the paper ground
  fossil: "#f3a483",
  red: "#e5483a", // glow when gas, coal or oil serve most of the load (demo replay)
  redPulse: "#f07a6a", // pulse color for those hours: soft, like `clean`
  pay: "#f1cf74",
} as const;

export const TOWER_R = 1.7; // pulses leave the grid tower at this radius
export const TOWER_TOP = 8.4;

export function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** Small seeded generator (mulberry32): the same company always gets the same building. */
export function rng(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const easeOutBack = (p: number) => {
  const c = 1.4;
  return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2;
};

type Roof = "plant" | "antenna" | "tank" | "flat";

interface Tier {
  w: number;
  d: number;
  h: number;
  y: number; // bottom of the tier
  map: THREE.CanvasTexture;
  emissive: THREE.CanvasTexture;
}

export interface Look {
  w: number;
  d: number;
  h: number;
  rot: number;
  tiers: Tier[];
  roof: Roof;
  trim: string;
}

/** A pastel take on the company color with a seeded twist: proportions, setback, roof, windows. */
export function makeLook(id: string, color: string, height: number, scale = 1): Look {
  const r = rng(hash(id));
  const hsl = { h: 0, s: 0, l: 0 };
  new THREE.Color(color).getHSL(hsl);
  // greens would read as "charging", so green companies get teal-blue buildings instead
  const hue = hsl.h > 0.2 && hsl.h < 0.47 ? 0.52 + (hsl.h - 0.2) * 0.22 : hsl.h;
  const facade = new THREE.Color().setHSL(
    (hue + (r() - 0.5) * 0.04 + 1) % 1,
    Math.min(0.7, hsl.s * (0.5 + r() * 0.25)),
    Math.min(0.84, Math.max(0.6, 0.72 + (r() - 0.5) * 0.12)),
  );
  const trim = facade.clone().offsetHSL(0, -0.05, -0.12);
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
    trim: `#${trim.getHexString()}`,
  };
}

export function disposeLook(look: Look) {
  look.tiers.forEach((t) => (t.map.dispose(), t.emissive.dispose()));
}

/** Window grid for one face: a color map and a matching emissive mask (lit = white). */
export function facadeTextures(facade: string, w: number, h: number, r: () => number) {
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
  const dark = `#${f.clone().multiplyScalar(0.66).getHexString()}`;
  const mid = `#${f.clone().multiplyScalar(0.84).getHexString()}`;
  ctx.fillStyle = facade;
  ctx.fillRect(0, 0, colorC.width, colorC.height);
  mask.fillStyle = "#000";
  mask.fillRect(0, 0, maskC.width, maskC.height);
  const litShare = 0.25 + r() * 0.25;
  for (let y = 0; y < floors; y++) {
    for (let x = 0; x < cols; x++) {
      const v = r();
      ctx.fillStyle = v < litShare ? "#fff3c8" : v < 0.75 ? dark : mid;
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
export function glowTexture(): THREE.CanvasTexture {
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

// --- ground, lights -------------------------------------------------------------------

/** Warm daylight, the paper ground with a fading grid, and a ring road at `road` metres. */
export function CityGround({ road, shadowR }: { road: number; shadowR: number }) {
  return (
    <>
      <color attach="background" args={[CITY.bg]} />
      <fog attach="fog" args={[CITY.bg, 70 + road * 2, 140 + road * 3]} />
      <hemisphereLight args={["#fffaf0", "#dcd2bf", 1.9]} />
      <ambientLight intensity={0.75} />
      {/* low sun from the back left: long soft shadows across the ground, like an architecture model */}
      <directionalLight
        position={[-14, 18, -6]}
        intensity={1.9}
        color="#fff4e2"
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
        <meshStandardMaterial color={CITY.ground} roughness={1} />
      </mesh>
      <Grid
        position-y={0.002}
        args={[200, 200]}
        cellSize={1}
        cellThickness={0.6}
        cellColor={CITY.cell}
        sectionSize={4}
        sectionThickness={1}
        sectionColor={CITY.section}
        fadeDistance={road * 2.6}
        fadeStrength={1.5}
        infiniteGrid
      />
      <RingRoad radius={road} />
    </>
  );
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
        <meshStandardMaterial color={CITY.road} roughness={1} />
      </mesh>
      <Line points={dash} color="#f8f5ee" lineWidth={1.5} dashed dashSize={0.5} gapSize={0.5} />
    </group>
  );
}

// --- the grid tower -------------------------------------------------------------------

/**
 * The power grid: a bright stepped golden tower on a plaza, like a city hall in an architecture
 * model. Its windows always glow warm, gold light strips crown each tier and it casts a warm
 * pool of light on the plaza, so it reads as the hub at a glance. `levelRef` (0..1) makes it
 * brighter still while companies draw power; children sit on the plaza.
 */
export function GridTower({ levelRef, children }: { levelRef: RefObject<number>; children?: ReactNode }) {
  const textures = useMemo(() => {
    const r = rng(7);
    return [
      facadeTextures("#f8d47a", 2.6, 4.6, r),
      facadeTextures("#fad98a", 2.0, 2.2, r),
      facadeTextures("#fcdf99", 1.4, 1.3, r),
    ];
  }, []);
  const sides = useMemo(
    () =>
      textures.map(
        (t) =>
          new THREE.MeshStandardMaterial({
            map: t.map,
            emissiveMap: t.emissive,
            emissive: new THREE.Color("#ffcf5c"),
            emissiveIntensity: 0.5,
            roughness: 0.6,
          }),
      ),
    [textures],
  );
  const roof = useMemo(() => new THREE.MeshStandardMaterial({ color: "#f3c45c", roughness: 0.7 }), []);
  const strip = useMemo(() => new THREE.MeshBasicMaterial({ color: "#ffe39a", toneMapped: false }), []);
  const glowMap = useMemo(() => glowTexture(), []);
  useEffect(
    () => () => {
      textures.forEach((t) => (t.map.dispose(), t.emissive.dispose()));
      sides.forEach((m) => m.dispose());
      roof.dispose();
      strip.dispose();
      glowMap.dispose();
    },
    [textures, sides, roof, strip, glowMap],
  );
  const lamp = useRef<THREE.MeshBasicMaterial>(null);
  const halo = useRef<THREE.Mesh>(null);
  const pool = useRef<THREE.MeshBasicMaterial>(null);
  const tiers: [number, number, number][] = [
    [2.6, 4.6, 0.3],
    [2.0, 2.2, 4.9],
    [1.4, 1.3, 7.1],
  ]; // [width, height, bottom]

  useFrame((state) => {
    const level = levelRef.current ?? 0;
    const throb = 0.5 + 0.5 * Math.sin(state.clock.elapsedTime * 3);
    sides.forEach((m) => (m.emissiveIntensity = 0.5 + level * 0.8));
    strip.color.set("#ffe39a").multiplyScalar(1.15 + level * 0.6 + throb * 0.15);
    if (lamp.current) lamp.current.color.set(CITY.gold).multiplyScalar(1.6 + level * (1 + throb));
    if (halo.current) {
      halo.current.scale.setScalar(1 + throb * 0.2 * (0.3 + level));
      (halo.current.material as THREE.MeshBasicMaterial).opacity = 0.4 + 0.45 * level;
    }
    if (pool.current) pool.current.opacity = 0.55 + 0.3 * level;
  });

  return (
    <group>
      {/* warm pool of light on the ground around the plaza */}
      <mesh rotation-x={-Math.PI / 2} position-y={0.015}>
        <planeGeometry args={[9, 9]} />
        <meshBasicMaterial ref={pool} map={glowMap} color="#ffd877" transparent opacity={0.55} depthWrite={false} />
      </mesh>
      <pointLight position={[0, 3.5, 2.5]} color="#ffd98a" intensity={14} distance={12} decay={2} />
      <mesh position-y={0.15} receiveShadow castShadow>
        <boxGeometry args={[3.6, 0.3, 3.6]} />
        <meshStandardMaterial color="#f8efd8" roughness={0.9} />
      </mesh>
      {/* dashed base outline, like the reference's plaza */}
      <mesh position-y={0.31} rotation-x={-Math.PI / 2}>
        <ringGeometry args={[2.05, 2.12, 4, 1, Math.PI / 4]} />
        <meshBasicMaterial color="#e2bf6e" />
      </mesh>
      {tiers.map(([w, h, y], i) => (
        <group key={i}>
          <mesh
            position-y={y + h / 2}
            castShadow
            receiveShadow
            material={[sides[i], sides[i], roof, roof, sides[i], sides[i]]}
          >
            <boxGeometry args={[w, h, w]} />
          </mesh>
          {/* light strips along the top edges of each tier */}
          {[-1, 1].map((sgn) => (
            <group key={sgn}>
              <mesh position={[0, y + h, (sgn * w) / 2]} material={strip}>
                <boxGeometry args={[w + 0.06, 0.08, 0.08]} />
              </mesh>
              <mesh position={[(sgn * w) / 2, y + h, 0]} material={strip}>
                <boxGeometry args={[0.08, 0.08, w + 0.06]} />
              </mesh>
            </group>
          ))}
        </group>
      ))}
      <mesh position-y={TOWER_TOP + 0.6} castShadow>
        <cylinderGeometry args={[0.04, 0.06, 1.2, 8]} />
        <meshStandardMaterial color="#d9ad52" />
      </mesh>
      {/* a small lamp and its ring on the mast, not a ball: the tower itself is the hero */}
      <mesh position-y={TOWER_TOP + 1.25}>
        <sphereGeometry args={[0.12, 16, 12]} />
        <meshBasicMaterial ref={lamp} color={CITY.gold} toneMapped={false} />
      </mesh>
      <mesh ref={halo} position-y={TOWER_TOP + 1.25} rotation-x={Math.PI / 2}>
        <torusGeometry args={[0.38, 0.03, 8, 40]} />
        <meshBasicMaterial color={CITY.gold} transparent opacity={0.4} toneMapped={false} />
      </mesh>
      {children}
    </group>
  );
}

// --- company buildings -----------------------------------------------------------------

/** What a building shows this frame; the caller writes it, the building eases toward it. */
export interface Vis {
  p: number; // 0..1 construction progress
  d: number; // 0..1 demolition progress (0 = standing)
  glow: number; // 0..1 steady glow while it draws power
  flash: number; // 0..1 brief highlight when a pulse lands
  color: THREE.Color; // glow color (green = clean power)
  hovered: boolean;
}

export function newVis(): Vis {
  return { p: 1, d: 0, glow: 0, flash: 0, color: new THREE.Color(CITY.green), hovered: false };
}

/** Seconds a construction or a demolition takes, for callers that drive `p` and `d` by time. */
export const BUILD_S = 1.3;
export const DEMOLISH_S = 1.2;

const RIPPLES = 3;
const RIPPLE_S = 1.8;
const DEBRIS = 12;
const PUFFS = 7;
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));

/** Scaffold outline around the building volume: box edges plus a ring every ~0.8 m. */
function scaffoldGeometry(w: number, h: number, d: number): THREE.BufferGeometry {
  const x = w / 2 + 0.1;
  const z = d / 2 + 0.1;
  const pts: number[] = [];
  const seg = (a: number[], b: number[]) => pts.push(...a, ...b);
  for (const [cx, cz] of [
    [-x, -z],
    [x, -z],
    [x, z],
    [-x, z],
  ])
    seg([cx, 0, cz], [cx, h, cz]);
  const rings = Math.max(2, Math.round(h / 0.8));
  for (let i = 1; i <= rings; i++) {
    const y = (i / rings) * h;
    seg([-x, y, -z], [x, y, -z]);
    seg([x, y, -z], [x, y, z]);
    seg([x, y, z], [-x, y, z]);
    seg([-x, y, z], [-x, y, -z]);
  }
  // a few diagonal braces on the front face
  for (let i = 0; i < rings; i++) seg([-x, (i / rings) * h, z], [x, ((i + 1) / rings) * h, z]);
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  return g;
}

export function BuildingMesh({
  look,
  glowMap,
  vis,
  pending = false,
  phase = 0,
  onOver,
  onOut,
}: {
  look: Look;
  glowMap: THREE.Texture;
  vis: RefObject<Vis>;
  /** added since the last run: a plainer plinth with a dashed outline */
  pending?: boolean;
  /** staggers the ripple rings between buildings */
  phase?: number;
  onOver?: () => void;
  onOut?: () => void;
}) {
  const sideMats = useMemo(
    () =>
      look.tiers.map(
        (t) =>
          new THREE.MeshStandardMaterial({
            map: t.map,
            emissiveMap: t.emissive,
            emissive: new THREE.Color(CITY.green),
            emissiveIntensity: 0,
            roughness: 0.8,
          }),
      ),
    [look],
  );
  const roofMat = useMemo(() => new THREE.MeshStandardMaterial({ color: look.trim, roughness: 0.9 }), [look]);
  const scaffold = useMemo(() => scaffoldGeometry(look.w, look.h + 0.25, look.d), [look]);
  // debris: seeded directions and spins, so each building falls apart its own way
  const chunks = useMemo(() => {
    const r = rng(hash(look.trim) ^ 0x9e3779b9);
    return Array.from({ length: DEBRIS }, () => ({
      a: r() * Math.PI * 2,
      speed: 1.2 + r() * 2.2,
      vy: 1.5 + r() * 2.5,
      y0: look.h * (0.35 + r() * 0.6),
      spin: (r() - 0.5) * 14,
      size: 0.14 + r() * 0.2,
    }));
  }, [look]);
  useEffect(
    () => () => (sideMats.forEach((m) => m.dispose()), roofMat.dispose(), scaffold.dispose()),
    [sideMats, roofMat, scaffold],
  );

  const body = useRef<THREE.Group>(null);
  const rise = useRef<THREE.Group>(null);
  const aura = useRef<THREE.MeshBasicMaterial>(null);
  const glow = useRef<THREE.MeshBasicMaterial>(null);
  const ripples = useRef<THREE.Group>(null);
  const dust = useRef<THREE.Mesh>(null);
  const frame = useRef<THREE.LineSegments>(null);
  const crane = useRef<THREE.Group>(null);
  const jib = useRef<THREE.Group>(null);
  const debris = useRef<THREE.Group>(null);
  const puffs = useRef<THREE.Group>(null);
  const rubble = useRef<THREE.Group>(null);
  const tint = useMemo(() => new THREE.Color(), []);
  const white = useMemo(() => new THREE.Color("#ffffff"), []);
  const ash = useMemo(() => new THREE.Color("#b9b0a2"), []);
  const half = Math.max(look.w, look.d) / 2;
  const floors = Math.max(3, Math.round(look.h / 0.36));

  useFrame((state, dt) => {
    const v = vis.current;
    if (!v) return;
    const t = state.clock.elapsedTime;
    const k = 1 - Math.exp(-dt * 7);

    // construction: floors stack up one at a time inside a scaffold, a crane swings overhead
    const p = v.p;
    const built = p >= 1 ? 1 : Math.max(0.001, Math.ceil((1 - (1 - p) ** 1.6) * floors) / floors);
    const building = p > 0 && p < 1;
    // demolition: the building shakes, then drops into a cloud of dust and flying debris
    const d = v.d;
    const fall = clamp01((d - 0.18) / 0.55);
    const grown = Math.max(0.001, built * (1 - fall * fall));
    const spread = 0.9 + 0.1 * Math.min(1, p * 1.4);
    rise.current?.scale.set(spread, grown, spread);
    if (rise.current) rise.current.visible = p > 0 && fall < 1;

    if (frame.current) {
      frame.current.visible = building || (p >= 1 && p < 1.001);
      (frame.current.material as THREE.LineBasicMaterial).opacity = p < 0.85 ? 0.9 : Math.max(0, (1 - p) / 0.15) * 0.9;
    }
    if (crane.current) {
      crane.current.visible = building;
      const pop = Math.min(1, p * 5) * Math.min(1, (1 - p) * 6);
      crane.current.scale.set(1, Math.max(0.001, pop), 1);
    }
    if (jib.current) jib.current.rotation.y = Math.sin(t * 2.2) * 0.9;
    if (dust.current) {
      dust.current.visible = building;
      const s = 1 + p * 1.6;
      dust.current.scale.set(s, s, 1);
      (dust.current.material as THREE.MeshBasicMaterial).opacity = (1 - p) * 0.5;
    }

    const b = body.current;
    if (b) {
      const shake = d > 0 && d < 0.75 ? (1 - fall) * 0.06 : 0;
      b.position.x = Math.sin(t * 61) * shake;
      b.position.z = Math.cos(t * 47) * shake;
      const s = v.hovered && d === 0 ? 1.06 : 1 + v.flash * 0.035;
      b.scale.setScalar(b.scale.x + (s - b.scale.x) * k);
    }
    const dg = debris.current;
    if (dg) {
      const tt = (d - 0.15) * DEMOLISH_S; // seconds since the collapse began
      dg.visible = d > 0.15 && d < 1;
      dg.children.forEach((m, i) => {
        const c = chunks[i];
        m.position.set(
          Math.cos(c.a) * c.speed * tt,
          Math.max(0.08, c.y0 * (1 - fall) + c.vy * tt - 4.9 * tt * tt),
          Math.sin(c.a) * c.speed * tt,
        );
        m.rotation.set(c.spin * tt, c.spin * 0.7 * tt, 0);
        m.scale.setScalar(c.size * (d > 0.8 ? (1 - d) / 0.2 : 1));
      });
    }
    const pg = puffs.current;
    if (pg) {
      const e = clamp01((d - 0.15) / 0.85);
      pg.visible = d > 0.15 && d < 1;
      pg.children.forEach((m, i) => {
        const a = (i / PUFFS) * Math.PI * 2;
        const r = half * (0.6 + e * 1.4);
        m.position.set(Math.cos(a) * r, 0.3 + e * (0.6 + (i % 3) * 0.4), Math.sin(a) * r);
        m.scale.setScalar(0.4 + e * 1.3);
        ((m as THREE.Mesh).material as THREE.MeshStandardMaterial).opacity = (1 - e) * 0.65;
      });
    }
    if (rubble.current) {
      const r = clamp01((d - 0.55) / 0.3);
      rubble.current.visible = r > 0;
      rubble.current.scale.set(1, Math.max(0.001, r), 1);
    }

    const alive = d === 0 ? 1 : 0;
    const lit = Math.min(1.3, v.glow + v.flash * 0.8) * alive;
    sideMats.forEach((m) => {
      m.emissive.copy(v.color);
      m.emissiveIntensity = (v.glow * 1.5 + v.flash * 2.2) * alive;
      m.color.copy(tint.copy(white).lerp(v.color, Math.min(1, lit) * 0.22)).lerp(ash, fall * 0.6);
    });
    if (aura.current) {
      aura.current.color.copy(v.color);
      aura.current.opacity = Math.min(0.28, lit * 0.15);
    }
    if (glow.current) {
      glow.current.color.copy(v.color);
      glow.current.opacity = Math.min(0.85, lit * 0.7);
    }
    const rg = ripples.current;
    if (rg) {
      rg.visible = v.glow * alive > 0.02;
      rg.children.forEach((ring, i) => {
        const ph = (t / RIPPLE_S + i / RIPPLES + phase) % 1;
        const r = 1 + ph * 1.1;
        ring.scale.set(r, r, 1);
        const m = (ring as THREE.Mesh).material as THREE.MeshBasicMaterial;
        m.color.copy(v.color);
        m.opacity = (1 - ph) ** 1.6 * v.glow * 0.8;
      });
    }
  });

  return (
    <>
      <group ref={ripples} rotation-x={-Math.PI / 2} position-y={0.03} scale={half * 1.25} visible={false}>
        {Array.from({ length: RIPPLES }, (_, i) => (
          <mesh key={i}>
            <ringGeometry args={[0.95, 1, 64]} />
            <meshBasicMaterial transparent opacity={0} depthWrite={false} side={THREE.DoubleSide} />
          </mesh>
        ))}
      </group>
      <mesh rotation-x={-Math.PI / 2} position-y={0.025}>
        <planeGeometry args={[half * 4.4, half * 4.4]} />
        <meshBasicMaterial ref={glow} map={glowMap} transparent opacity={0} depthWrite={false} />
      </mesh>
      <mesh ref={dust} rotation-x={-Math.PI / 2} position-y={0.04} scale={half} visible={false}>
        <ringGeometry args={[0.9, 1.15, 48]} />
        <meshBasicMaterial color="#cdbf9e" transparent opacity={0} depthWrite={false} />
      </mesh>
      <group ref={puffs} visible={false}>
        {Array.from({ length: PUFFS }, (_, i) => (
          <mesh key={i}>
            <sphereGeometry args={[0.5, 12, 10]} />
            <meshStandardMaterial color="#ddd5c6" transparent opacity={0} depthWrite={false} roughness={1} />
          </mesh>
        ))}
      </group>
      <group ref={debris} visible={false}>
        {chunks.map((_, i) => (
          <mesh key={i} castShadow>
            <boxGeometry args={[1, 0.7, 0.8]} />
            <meshStandardMaterial color={i % 3 ? look.trim : "#cfc6b6"} roughness={1} />
          </mesh>
        ))}
      </group>
      <group
        ref={body}
        rotation-y={look.rot}
        onPointerOver={
          onOver &&
          ((e) => {
            e.stopPropagation();
            onOver();
          })
        }
        onPointerOut={onOut}
        onClick={
          onOver &&
          ((e) => {
            e.stopPropagation();
            onOver();
          })
        }
      >
        <mesh position-y={0.06} receiveShadow castShadow>
          <boxGeometry args={[look.w + 0.35, 0.12, look.d + 0.35]} />
          <meshStandardMaterial color={pending ? "#e6dfcf" : "#f3eee4"} roughness={1} />
        </mesh>
        {pending && (
          <mesh rotation-x={-Math.PI / 2} position-y={0.13}>
            <ringGeometry args={[half + 0.3, half + 0.38, 4, 1, Math.PI / 4]} />
            <meshBasicMaterial color="#b9aa88" />
          </mesh>
        )}
        {/* what is left on the lot after a demolition */}
        <group ref={rubble} position-y={0.12} visible={false}>
          {[
            [-0.3, 0.12, 0.2, 0.7, 0.24, 0.5],
            [0.35, 0.09, -0.25, 0.55, 0.18, 0.6],
            [0.05, 0.07, 0.45, 0.5, 0.14, 0.35],
          ].map(([x, y, z, w, h, dd], i) => (
            <mesh key={i} position={[x * look.w, y, z * look.d]} rotation-y={i * 0.7} castShadow receiveShadow>
              <boxGeometry args={[w * look.w, h * 2, dd * look.d]} />
              <meshStandardMaterial color={i === 1 ? look.trim : "#d2c9b8"} roughness={1} />
            </mesh>
          ))}
        </group>
        <lineSegments ref={frame} geometry={scaffold} position-y={0.12} visible={false}>
          <lineBasicMaterial color="#c79a4b" transparent opacity={0.9} />
        </lineSegments>
        <group ref={crane} position={[look.w / 2 + 0.35, 0.12, -look.d / 2 - 0.35]} visible={false}>
          <mesh position-y={(look.h + 1.1) / 2} castShadow>
            <boxGeometry args={[0.1, look.h + 1.1, 0.1]} />
            <meshStandardMaterial color="#e8a93a" />
          </mesh>
          <group ref={jib} position-y={look.h + 1.1}>
            <mesh position-x={-0.7} castShadow>
              <boxGeometry args={[2.2, 0.09, 0.09]} />
              <meshStandardMaterial color="#e8a93a" />
            </mesh>
            <mesh position-x={0.32}>
              <boxGeometry args={[0.22, 0.2, 0.2]} />
              <meshStandardMaterial color="#8f877a" />
            </mesh>
            <mesh position={[-1.6, -0.45, 0]}>
              <boxGeometry args={[0.015, 0.9, 0.015]} />
              <meshBasicMaterial color="#6f685e" />
            </mesh>
          </group>
        </group>
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
          {/* soft shell: the building itself glows while it draws power */}
          <mesh position-y={look.h / 2}>
            <boxGeometry args={[look.w + 0.22, look.h + 0.2, look.d + 0.22]} />
            <meshBasicMaterial
              ref={aura}
              transparent
              opacity={0}
              depthWrite={false}
              blending={THREE.AdditiveBlending}
              side={THREE.BackSide}
            />
          </mesh>
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
        <meshStandardMaterial color="#ece9e2" roughness={0.9} />
      </mesh>
    );
  if (look.roof === "antenna")
    return (
      <group position={[-top.w * 0.18, y, top.d * 0.15]}>
        <mesh position-y={0.55} castShadow>
          <cylinderGeometry args={[0.03, 0.045, 1.1, 6]} />
          <meshStandardMaterial color="#77716a" />
        </mesh>
        <mesh position-y={1.12}>
          <sphereGeometry args={[0.05, 10, 8]} />
          <meshBasicMaterial color="#ff6a5a" />
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

// --- power lines and pulses -----------------------------------------------------------------

/** Dashed line on the ground; `level` (0..1, read every frame) blends it from idle to `live`.
 * A `THREE.Color` passed as `live` is read every frame, so the caller can change it in place. */
export function PowerLine({
  from,
  to,
  level,
  live = CITY.green,
  idle = CITY.lineIdle,
  faint = false,
}: {
  from: THREE.Vector3;
  to: THREE.Vector3;
  level: () => number;
  live?: string | THREE.Color;
  idle?: string;
  faint?: boolean;
}) {
  const ref = useRef<import("react").ComponentRef<typeof Line>>(null);
  const idleC = useMemo(() => new THREE.Color(idle), [idle]);
  const liveC = useMemo(() => (live instanceof THREE.Color ? live : new THREE.Color(live)), [live]);
  const points = useMemo(() => [from.clone().setY(0.03), to.clone().setY(0.03)], [from, to]);
  useFrame(() => {
    const m = ref.current?.material;
    if (!m) return;
    const l = Math.min(1, level());
    m.color.copy(idleC).lerp(liveC, l);
    m.opacity = faint ? 0.55 : 0.7 + 0.3 * l;
  });
  return (
    <Line
      ref={ref}
      points={points}
      color={idleC}
      lineWidth={faint ? 1.5 : 2.2}
      dashed
      dashSize={faint ? 0.18 : 0.45}
      gapSize={faint ? 0.3 : 0.28}
      transparent
    />
  );
}

/** A stream of pulses from `from` to `to`, one every `every` seconds while it is listed. */
export interface Stream {
  id: string;
  from: THREE.Vector3;
  to: THREE.Vector3;
  every: number;
  color: THREE.Color;
}

interface Pulse {
  id: string;
  from: THREE.Vector3;
  to: THREE.Vector3;
  born: number;
  dur: number;
  color: THREE.Color;
}

const PULSE_SPEED = 13; // metres per second
const PULSE_TRAIL = 4; // beads per pulse: a light head and a fading tail

/**
 * Light pulses along the power lines. The caller rewrites `streams` each frame; a stream that
 * appears fires at once (so scrubbing fires pulses), then keeps firing while it stays listed.
 * `onLand` is called with the stream id as each pulse arrives.
 */
export function PulseField({
  streams,
  onLand,
  max = 240,
}: {
  streams: RefObject<Stream[]>;
  onLand?: (id: string) => void;
  max?: number;
}) {
  const mesh = useRef<THREE.InstancedMesh>(null);
  const pulses = useRef<Pulse[]>([]);
  const next = useRef(new Map<string, number>());
  const tmp = useMemo(() => new THREE.Object3D(), []);
  const c = useMemo(() => new THREE.Color(), []);
  const head = useMemo(() => new THREE.Color("#ffffff"), []);
  const p = useMemo(() => new THREE.Vector3(), []);

  // instance colors must exist before the first render so the material compiles with them
  useLayoutEffect(() => {
    const m = mesh.current;
    if (!m) return;
    for (let i = 0; i < m.count; i++) m.setColorAt(i, head);
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  }, [head]);

  useFrame((state) => {
    const t = state.clock.elapsedTime;
    const live = new Set<string>();
    for (const s of streams.current ?? []) {
      live.add(s.id);
      const due = next.current.get(s.id);
      if (due === undefined || t >= due) {
        if (pulses.current.length < max) {
          pulses.current.push({
            id: s.id,
            from: s.from.clone(),
            to: s.to.clone(),
            born: t,
            dur: Math.max(0.25, s.from.distanceTo(s.to) / PULSE_SPEED),
            color: s.color.clone(),
          });
        }
        next.current.set(s.id, t + s.every);
      }
    }
    for (const id of next.current.keys()) if (!live.has(id)) next.current.delete(id);

    const m = mesh.current;
    if (!m) return;
    let n = 0;
    pulses.current = pulses.current.filter((q) => {
      const u = (t - q.born) / q.dur;
      if (u >= 1) {
        onLand?.(q.id);
        return false;
      }
      const hop = 0.4 + q.from.distanceTo(q.to) * 0.035;
      for (let j = 0; j < PULSE_TRAIL; j++) {
        const uj = u - j * 0.035;
        if (uj < 0) continue;
        p.lerpVectors(q.from, q.to, uj);
        p.y += Math.sin(Math.PI * uj) * hop;
        tmp.position.copy(p);
        tmp.scale.setScalar(j === 0 ? 1 : 0.78 - j * 0.15);
        tmp.updateMatrix();
        m.setMatrixAt(n, tmp.matrix);
        // white-hot head, then the stream's soft color fading into the paper
        m.setColorAt(n, j === 0 ? c.copy(q.color).lerp(head, 0.6).multiplyScalar(1.15) : c.copy(q.color));
        n++;
      }
      return true;
    });
    m.count = n;
    m.instanceMatrix.needsUpdate = true;
    if (m.instanceColor) m.instanceColor.needsUpdate = true;
  });

  return (
    <instancedMesh ref={mesh} args={[undefined, undefined, max * PULSE_TRAIL]} frustumCulled={false}>
      <sphereGeometry args={[0.14, 12, 8]} />
      <meshBasicMaterial toneMapped={false} />
    </instancedMesh>
  );
}
