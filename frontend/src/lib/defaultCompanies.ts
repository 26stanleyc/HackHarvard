import type { Bid } from "./types";

/**
 * The 20 companies the app opens with: four of each preset load type, at different sizes and
 * windows around the preset values, scaled by SCALE. Names are made up (place + kind), not real
 * companies.
 */

/**
 * Size multiplier for energy and max MW. At 1x the 20 companies are about 2% of the summer peak,
 * too little flexible load to herd; at 3x (about 6-11% of peak on the demo days) herding shows
 * and coordination has something to fix.
 */
export const SCALE = 3;

type Row = [name: string, load_type: string, energy_mwh: number, max_mw: number, earliest: number, deadline: number];

const ROWS: Row[] = [
  ["Houston EV depot", "ev_fleet", 1200, 300, 0, 7],
  ["Dallas delivery vans", "ev_fleet", 800, 200, 0, 6],
  ["Austin transit buses", "ev_fleet", 600, 150, 1, 6],
  ["San Antonio rideshare hub", "ev_fleet", 400, 100, 0, 8],
  ["Abilene AI campus", "data_center", 2400, 200, 0, 24],
  ["Round Rock batch compute", "data_center", 1440, 120, 0, 24],
  ["Plano render farm", "data_center", 960, 80, 0, 24],
  ["El Paso backup sync", "data_center", 600, 50, 0, 24],
  ["Downtown Houston towers", "office", 600, 100, 6, 18],
  ["Dallas office park", "office", 450, 75, 6, 18],
  ["Austin campus pre-cooling", "office", 360, 60, 7, 17],
  ["Fort Worth medical offices", "office", 240, 40, 6, 16],
  ["Gulf Coast cold storage", "cold_storage", 360, 60, 0, 24],
  ["Rio Grande produce warehouse", "cold_storage", 240, 40, 0, 24],
  ["Panhandle meat packing", "cold_storage", 180, 30, 0, 20],
  ["Lubbock dairy chillers", "cold_storage", 120, 20, 0, 24],
  ["Houston water utility", "water_pumping", 720, 90, 0, 16],
  ["Dallas reservoir pumps", "water_pumping", 480, 60, 0, 16],
  ["Corpus Christi desalination", "water_pumping", 360, 45, 0, 18],
  ["Waco irrigation district", "water_pumping", 240, 30, 2, 14],
];

export const DEFAULT_BIDS: Bid[] = ROWS.map(([name, load_type, energy_mwh, max_mw, earliest_hour, deadline_hour], i) => ({
  id: `b${i + 1}`,
  name,
  load_type,
  energy_mwh: energy_mwh * SCALE,
  max_mw: max_mw * SCALE,
  earliest_hour,
  deadline_hour,
}));
