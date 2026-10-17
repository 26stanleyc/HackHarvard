#!/usr/bin/env node
// Copy the repo's API fixtures (../fixtures/*.json) into src/fixtures/ so the
// frontend's fixture mode always serves the single source of truth.
// Runs automatically before `npm run dev` and `npm run build` (predev/prebuild).
// If ../fixtures is missing (e.g. the frontend was copied out of the repo), the
// committed copies in src/fixtures/ are kept and a warning is printed.
import { copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "..", "..", "fixtures");
const dest = resolve(here, "..", "src", "fixtures");

if (!existsSync(src)) {
  console.warn(`[sync-fixtures] ${src} not found; keeping existing copies in ${dest}`);
  process.exit(0);
}

mkdirSync(dest, { recursive: true });
const files = readdirSync(src).filter((f) => f.endsWith(".json"));
let changed = 0;
for (const f of files) {
  const from = join(src, f);
  const to = join(dest, f);
  JSON.parse(readFileSync(from, "utf8")); // fail loudly on a malformed fixture
  const same = existsSync(to) && readFileSync(to, "utf8") === readFileSync(from, "utf8");
  if (!same) {
    copyFileSync(from, to);
    changed++;
  }
}
console.log(`[sync-fixtures] ${files.length} fixtures checked, ${changed} updated -> src/fixtures/`);
