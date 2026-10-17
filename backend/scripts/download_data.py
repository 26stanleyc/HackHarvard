"""Reproducible raw-data downloads into backend/data/raw (gitignored).

Usage (from backend/)::

    uv run python scripts/download_data.py                 # every dataset, skipping files present
    uv run python scripts/download_data.py --only egrid fuel_prices
    uv run python scripts/download_data.py --dry-run       # report present/missing, no network
    uv run python scripts/download_data.py --only ercot_sced --force

Datasets (see backend/data/raw/SOURCES.md for exact URLs, columns and quirks):

* ``eia930_bulk``  EIA-930 six-month BALANCE files (all BAs), no key.
* ``egrid``        EPA eGRID2023 rev2 workbook, no key.
* ``fuel_prices``  EIA API v2 electric-power-operational-data, Texas monthly fuel cost
                   (needs EIA_API_KEY).
* ``ercot_sced``   ERCOT NP3-965-ER 60-day SCED disclosure, aggregated per hour to wind/solar HSL,
                   base point, telemetered output and curtailment (needs ERCOT_* keys). One zip
                   (~10 MB) per operating day; zips are not kept.
* ``ercot_np4``    ERCOT NP4-732-CD (wind) / NP4-737-CD (solar) hourly system-wide reports, used to
                   validate the SCED aggregate (needs ERCOT_* keys).

Keys come from ``clearinghouse.config`` (repo-root .env). Key values are never printed: any error
text is scrubbed of them before printing. Nothing touches the network or reads keys for a
dataset whose output is already present (unless ``--force``).
"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import io
import sys
import threading
import time
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from clearinghouse.config import DATA_RAW, get_settings

# ---------------------------------------------------------------- dataset parameters
EIA930_BULK_URL = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles/{name}"
EIA930_BULK_FILES = (
    "EIA930_BALANCE_2023_Jul_Dec.csv",
    "EIA930_BALANCE_2024_Jan_Jun.csv",
    "EIA930_BALANCE_2024_Jul_Dec.csv",
    "EIA930_BALANCE_2025_Jan_Jun.csv",
)
EGRID_URL = "https://www.epa.gov/system/files/documents/2025-06/egrid2023_data_rev2.xlsx"
EIA_API = "https://api.eia.gov/v2/{route}/data/"
FUEL_ROUTE = "electricity/electric-power-operational-data"
FUEL_START, FUEL_END = "2023-12", "2025-01"
FUEL_FACETS = {
    "location": ["TX"],
    "fueltypeid": ["COW", "NG", "PET", "SUB", "LIG", "FOS"],
    "sectorid": ["98", "99", "1", "94"],
}
FUEL_DATA = ["cost-per-btu", "cost", "receipts-btu", "consumption-for-eg-btu"]

ERCOT_TOKEN_URL = ("https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/"
                   "B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token")
ERCOT_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"
ERCOT_BASE = "https://api.ercot.com/api/public-reports"
# SCED disclosures post ~60 days after the operating day: these posting dates cover operating days
# 2023-12-01 .. 2025-01-31.
SCED_POST_FROM, SCED_POST_TO = "2024-01-29", "2025-04-02"
SCED_OD_FROM, SCED_OD_TO = "2023-12-01", "2025-01-31"
SCED_TYPES = {"WIND": "wind", "PVGR": "solar"}
NP4_REPORTS = (("np4-732-cd", "wpp_hrly_avrg_actl_fcast", "wind"),
               ("np4-737-cd", "spp_hrly_avrg_actl_fcast", "solar"))
NP4_MONTHS = ("2023-12", "2025-12")
# ERCOT published corrupted NP4 actuals (5-15x too high) in these postings.
NP4_CORRUPT_POSTINGS = ("2024-08-22T15:00", "2024-08-23T16:00")

# ---------------------------------------------------------------- output paths
EIA930_DIR = DATA_RAW / "eia930" / "bulk"
EGRID_FILE = DATA_RAW / "egrid" / "egrid2023_data_rev2.xlsx"
FUEL_FILE = DATA_RAW / "fuel_prices" / f"eia_tx_fuel_cost_monthly_{FUEL_START}_{FUEL_END}.csv"
ERCOT_DIR = DATA_RAW / "ercot"
SCED_DAILY_DIR = ERCOT_DIR / "sced_hourly"
SCED_FILE = ERCOT_DIR / "ercot_sced_wind_solar_hourly_2023-12_2025-01.csv"
NP4_FILES = {name: ERCOT_DIR / name / f"ercot_{name}_hourly_actual_median_2023-12_2025-12.csv"
             for _, _, name in NP4_REPORTS}


# ---------------------------------------------------------------- helpers
class Secrets:
    """Loads keys lazily and scrubs them from any text before it is printed."""

    def __init__(self) -> None:
        self._settings = None

    @property
    def settings(self):
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def require(self, *names: str) -> None:
        missing = [n for n in names if not getattr(self.settings, n)]
        if missing:
            raise SystemExit(f"missing keys in .env: {', '.join(missing)}")

    def scrub(self, text: str) -> str:
        if self._settings is None:
            return text
        for name in self.settings.key_status():
            value = getattr(self.settings, name)
            if value:
                text = text.replace(value, f"<{name}>")
        return text


SECRETS = Secrets()


def log(msg: str) -> None:
    print(SECRETS.scrub(msg), flush=True)


def download_file(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    tmp.rename(dest)
    log(f"  wrote {dest.relative_to(DATA_RAW)} ({dest.stat().st_size / 1e6:.1f} MB)")


# ---------------------------------------------------------------- EIA
def get_eia930_bulk(force: bool) -> None:
    for name in EIA930_BULK_FILES:
        dest = EIA930_DIR / name
        if dest.exists() and not force:
            continue
        download_file(EIA930_BULK_URL.format(name=name), dest)


def get_egrid(force: bool) -> None:
    download_file(EGRID_URL, EGRID_FILE)


def eia_api_rows(route: str, params: list[tuple[str, str]]) -> list[dict]:
    SECRETS.require("EIA_API_KEY")
    rows: list[dict] = []
    offset, total = 0, None
    while total is None or offset < total:
        q = [("api_key", SECRETS.settings.EIA_API_KEY), *params,
             ("sort[0][column]", "period"), ("sort[0][direction]", "asc"),
             ("offset", str(offset)), ("length", "5000")]
        for attempt in range(6):
            try:
                r = requests.get(EIA_API.format(route=route), params=q, timeout=240)
                r.raise_for_status()
                resp = r.json()["response"]
                break
            except Exception as e:  # noqa: BLE001 - retry any transport/HTTP error
                log(f"  retry offset {offset} attempt {attempt}: {type(e).__name__} {str(e)[:200]}")
                time.sleep(10 * (attempt + 1))
        else:
            raise SystemExit(f"EIA API failed at offset {offset}")
        total = int(resp["total"])
        rows += resp["data"]
        if not resp["data"]:
            break
        offset += len(resp["data"])
    return rows


def get_fuel_prices(force: bool) -> None:
    params = [("frequency", "monthly"), ("start", FUEL_START), ("end", FUEL_END)]
    params += [(f"data[{i}]", c) for i, c in enumerate(FUEL_DATA)]
    params += [(f"facets[{k}][]", v) for k, vals in FUEL_FACETS.items() for v in vals]
    rows = eia_api_rows(FUEL_ROUTE, params)
    FUEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(FUEL_FILE, index=False)
    log(f"  wrote {FUEL_FILE.relative_to(DATA_RAW)} ({len(rows)} rows)")


# ---------------------------------------------------------------- ERCOT
class Ercot:
    """ERCOT Public API client: ROPC id_token (refreshed every 50 min) + subscription key."""

    def __init__(self) -> None:
        SECRETS.require("ERCOT_USERNAME", "ERCOT_PASSWORD", "ERCOT_SUBSCRIPTION_KEY")
        self._token: str | None = None
        self._at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if self._token and time.time() - self._at < 50 * 60:
                return self._token
            s = SECRETS.settings
            r = requests.post(ERCOT_TOKEN_URL, timeout=60, data={
                "username": s.ERCOT_USERNAME, "password": s.ERCOT_PASSWORD,
                "grant_type": "password", "scope": f"openid {ERCOT_CLIENT_ID} offline_access",
                "client_id": ERCOT_CLIENT_ID, "response_type": "id_token",
            })
            if r.status_code != 200:
                raise SystemExit(SECRETS.scrub(f"ERCOT token error {r.status_code} {r.text[:300]}"))
            self._token, self._at = r.json()["id_token"], time.time()
            return self._token

    def get(self, path: str, params: dict | None = None, raw: bool = False):
        for attempt in range(6):
            headers = {"Authorization": f"Bearer {self.token()}",
                       "Ocp-Apim-Subscription-Key": SECRETS.settings.ERCOT_SUBSCRIPTION_KEY}
            try:
                r = requests.get(ERCOT_BASE + path, params=params, headers=headers, timeout=180)
            except requests.RequestException as e:
                log(f"  ERCOT transport error {type(e).__name__}; retrying")
                time.sleep(5)
                continue
            if r.status_code == 200:
                return r.content if raw else r.json()
            log(f"  ERCOT HTTP {r.status_code} {r.text[:200]}")
            if r.status_code == 429:
                time.sleep(15 * (attempt + 1))
            elif r.status_code >= 500:
                time.sleep(5)
            else:
                r.raise_for_status()
        raise RuntimeError(f"ERCOT request failed: {path}")


def _sced_aggregate(api: Ercot, arc: dict) -> tuple[int, str | None]:
    """Download one SCED zip and write per-hour wind/solar aggregates for its operating day."""
    doc = arc["docId"]
    for attempt in range(4):
        try:
            z = zipfile.ZipFile(io.BytesIO(api.get("/archive/np3-965-er", {"download": str(doc)},
                                                   raw=True)))
            break
        except Exception as e:  # noqa: BLE001
            log(f"  SCED download failed doc {doc} attempt {attempt}: {type(e).__name__}")
            time.sleep(10)
    else:
        return doc, None
    name = next(n for n in z.namelist() if n.startswith("60d_SCED_Gen_Resource_Data"))
    reader = csv.reader(io.TextIOWrapper(z.open(name), encoding="latin-1"))
    header = [c.strip() for c in next(reader)]
    i_ts, i_rep, i_type = (header.index(c) for c in
                           ("SCED Time Stamp", "Repeated Hour Flag", "Resource Type"))
    i_hsl, i_bp, i_tno = (header.index(c) for c in
                          ("HSL", "Base Point", "Telemetered Net Output"))

    def num(x: str) -> float:
        return float(x) if x else 0.0

    agg: dict[tuple, list[float]] = collections.defaultdict(lambda: [0.0] * 5)
    snaps: dict[tuple, set] = collections.defaultdict(set)
    op_day = None
    for row in reader:
        tech = SCED_TYPES.get(row[i_type])
        if not tech:
            continue
        ts = row[i_ts]  # MM/DD/YYYY HH:MM:SS, Central prevailing; snapshots at :00:30 :15 :30 :45
        day = f"{ts[6:10]}-{ts[0:2]}-{ts[3:5]}"
        op_day = op_day or day
        key = (f"{day} {ts[11:13]}", row[i_rep], tech)
        hsl, bp, tno = num(row[i_hsl]), num(row[i_bp]), num(row[i_tno])
        a = agg[key]
        a[0] += hsl
        a[1] += bp
        a[2] += tno
        a[3] += max(0.0, hsl - bp)
        a[4] += max(0.0, hsl - tno)
        snaps[key].add(ts)
    out = SCED_DAILY_DIR / f"sced_hourly_{op_day}.csv"
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["hour_start_local", "repeated_hour_flag", "tech", "n_snapshots", "hsl_mw",
                    "base_point_mw", "telemetered_mw", "curt_hsl_minus_bp_mw",
                    "curt_hsl_minus_tno_mw", "post_datetime", "doc_id"])
        for key in sorted(agg):
            n = len(snaps[key])
            w.writerow([key[0] + ":00", key[1], key[2], n] + [round(x / n, 2) for x in agg[key]]
                       + [arc["postDatetime"], doc])
    return doc, op_day


def get_ercot_sced(force: bool) -> None:
    api = Ercot()
    SCED_DAILY_DIR.mkdir(parents=True, exist_ok=True)
    archives, page = [], 1
    while True:
        j = api.get("/archive/np3-965-er", {"postDatetimeFrom": f"{SCED_POST_FROM}T00:00:00",
                                            "postDatetimeTo": f"{SCED_POST_TO}T23:59:59",
                                            "size": "1000", "page": str(page)})
        archives += j["archives"]
        if page >= j["_meta"]["totalPages"]:
            break
        page += 1
    done_docs: set[int] = set()
    if not force:
        for f in SCED_DAILY_DIR.glob("sced_hourly_*.csv"):
            done_docs |= set(pd.read_csv(f, usecols=["doc_id"])["doc_id"].astype(int))
    todo = [a for a in archives if int(a["docId"]) not in done_docs]
    log(f"  SCED archives: {len(archives)} listed, {len(todo)} to download")
    with ThreadPoolExecutor(4) as ex:
        for doc, op_day in ex.map(lambda a: _sced_aggregate(api, a), todo):
            log(f"  SCED doc {doc}: operating day {op_day}")
    frames = [pd.read_csv(f) for f in sorted(SCED_DAILY_DIR.glob("sced_hourly_*.csv"))]
    s = pd.concat(frames, ignore_index=True)
    date = s["hour_start_local"].str[:10]
    s = s[(date >= SCED_OD_FROM) & (date <= SCED_OD_TO)]
    keys = ["hour_start_local", "repeated_hour_flag", "tech"]
    s = s.drop_duplicates(keys, keep="last").sort_values(keys)
    s.to_csv(SCED_FILE, index=False)
    log(f"  wrote {SCED_FILE.relative_to(DATA_RAW)} ({len(s)} rows)")


def _months(a: str, b: str):
    y, m = map(int, a.split("-"))
    while f"{y:04d}-{m:02d}" <= b:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def get_ercot_np4(force: bool) -> None:
    api = Ercot()
    for report, endpoint, name in NP4_REPORTS:
        raw_dir = ERCOT_DIR / name / "raw_postings"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for y, m in _months(*NP4_MONTHS):
            fn = raw_dir / f"{name}_{y:04d}-{m:02d}.csv"
            if fn.exists() and not force:
                continue
            d0 = dt.date(y, m, 1)
            d1 = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
            rows, fields, page = [], None, 1
            while True:
                j = api.get(f"/{report}/{endpoint}", {
                    "deliveryDateFrom": str(d0), "deliveryDateTo": str(d1),
                    "genSystemWideFrom": "-1000000", "size": "50000", "page": str(page)})
                fields = [f["name"] for f in j["fields"]]
                rows += j["data"]
                if page >= j["_meta"]["totalPages"]:
                    break
                page += 1
            pd.DataFrame(rows, columns=fields).to_csv(fn, index=False)
            log(f"  wrote {fn.relative_to(DATA_RAW)} ({len(rows)} rows)")
        df = pd.concat([pd.read_csv(f) for f in sorted(raw_dir.glob(f"{name}_*.csv"))],
                       ignore_index=True)
        bad = ((df["postedDatetime"] >= NP4_CORRUPT_POSTINGS[0])
               & (df["postedDatetime"] < NP4_CORRUPT_POSTINGS[1]))
        df = df[~bad]
        keys = ["deliveryDate", "hourEnding", "DSTFlag"]
        g = df.groupby(keys)
        med = g.median(numeric_only=True)
        med["n_postings"] = g.size()
        med["first_posted"] = g["postedDatetime"].min()
        med["last_posted"] = g["postedDatetime"].max()
        med.reset_index().sort_values(keys).to_csv(NP4_FILES[name], index=False)
        log(f"  wrote {NP4_FILES[name].relative_to(DATA_RAW)} (median over postings; "
            f"{int(bad.sum())} corrupt-window rows dropped)")


# ---------------------------------------------------------------- driver
DATASETS: dict[str, tuple[Callable[[bool], None], Callable[[], list[Path]]]] = {
    "eia930_bulk": (get_eia930_bulk, lambda: [EIA930_DIR / n for n in EIA930_BULK_FILES]),
    "egrid": (get_egrid, lambda: [EGRID_FILE]),
    "fuel_prices": (get_fuel_prices, lambda: [FUEL_FILE]),
    "ercot_sced": (get_ercot_sced, lambda: [SCED_FILE]),
    "ercot_np4": (get_ercot_np4, lambda: list(NP4_FILES.values())),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="+", choices=list(DATASETS), help="datasets to fetch")
    ap.add_argument("--force", action="store_true", help="re-download even if present")
    ap.add_argument("--dry-run", action="store_true", help="report status only; no network")
    args = ap.parse_args(argv)
    for name in args.only or list(DATASETS):
        fetch, outputs = DATASETS[name]
        paths = outputs()
        present = all(p.is_file() and p.stat().st_size > 0 for p in paths)
        if present and not args.force:
            log(f"[{name}] skip: present ({len(paths)} file(s))")
            continue
        if args.dry_run:
            missing = [str(p.relative_to(DATA_RAW)) for p in paths if not p.is_file()]
            log(f"[{name}] would download (missing: {missing or 'none; --force'})")
            continue
        log(f"[{name}] downloading")
        fetch(args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
