"""EIA monthly delivered fuel cost for Texas power plants ($/MMBtu).

Source: EIA API v2 ``electricity/electric-power-operational-data`` (``cost-per-btu``, location TX),
saved as ``data/raw/fuel_prices/eia_tx_fuel_cost_monthly_2023-12_2025-01.csv``.

Series used:

* coal: fueltypeid ``COW`` (all coal products), sector 98 (Electric Power).
* gas: fueltypeid ``NG``, sector 98 (Electric Power).
* oil: fueltypeid ``PET``, sector 98 if reported, else sector 1 (Electric Utility). EIA withholds
  sector 98 PET for Texas, so in practice oil comes from sector 1.

Missing months stay NaN; the ``*_source`` columns record the sector actually used per month.
"""

from __future__ import annotations

from functools import lru_cache

import pandas as pd

from clearinghouse.config import DATA_PROCESSED, DATA_RAW

RAW_FILE = DATA_RAW / "fuel_prices" / "eia_tx_fuel_cost_monthly_2023-12_2025-01.csv"
OUT_FILE = DATA_PROCESSED / "fuel_prices_monthly.parquet"

SERIES = {
    # output prefix -> (fueltypeid, sector preference order)
    "coal": ("COW", (98,)),
    "gas": ("NG", (98,)),
    "oil": ("PET", (98, 1)),
}
SECTOR_NAMES = {98: "Electric Power", 1: "Electric Utility"}


def build_fuel_prices(raw_file=RAW_FILE, out_file=OUT_FILE) -> pd.DataFrame:
    raw = pd.read_csv(raw_file, dtype={"period": str})
    raw["cost-per-btu"] = pd.to_numeric(raw["cost-per-btu"], errors="coerce")
    months = sorted(raw["period"].unique())
    out = pd.DataFrame({"month": months})
    for name, (fuel_id, sectors) in SERIES.items():
        values, sources = [], []
        for m in months:
            val, src = float("nan"), None
            for sector in sectors:
                hit = raw[(raw["period"] == m) & (raw["fueltypeid"] == fuel_id)
                          & (raw["sectorid"] == sector)]["cost-per-btu"].dropna()
                if len(hit) and hit.iloc[0] > 0:
                    val = float(hit.iloc[0])
                    src = f"EIA {fuel_id} sector {sector} ({SECTOR_NAMES[sector]})"
                    break
            values.append(val)
            sources.append(src)
        out[f"{name}_usd_per_mmbtu"] = values
        out[f"{name}_source"] = sources
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(out_file, index=False)
    load_fuel_prices.cache_clear()
    return out


@lru_cache(maxsize=1)
def load_fuel_prices(path=OUT_FILE) -> pd.DataFrame:
    """Processed monthly prices. Returns the cached frame: do not mutate it (copy first)."""
    return pd.read_parquet(path)
