"""Build every processed data file from the raw downloads and print the M1 check summary.

Usage (from backend/)::

    uv run python scripts/build_processed.py

Raw inputs: backend/data/raw (gitignored; fetch with scripts/download_data.py).
Outputs: backend/data/processed/*.parquet + *.json (committed), docs/results/plant_exclusions.json,
docs/results/curtailment_validation.json.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data import curtailment, days, egrid, eia930, fuel_prices


def _step(name, fn, *args):
    t = time.time()
    out = fn(*args)
    print(f"[build] {name}: {time.time() - t:.1f}s")
    return out


def summarize(eia: pd.DataFrame, plants: pd.DataFrame, prices: pd.DataFrame,
              curt: pd.DataFrame, split: dict, validation: dict | None) -> None:
    print("\n=== M1 check summary ===")
    y = eia[eia["date_cst"].str.startswith("2024")]
    ok = eia930.complete_hours(y)
    per_day = ok.groupby(y["date_cst"]).sum()
    complete_days = int((per_day == 24).sum())
    missing = y.loc[~ok, "ts_cst"]
    print(f"EIA-930 2024 CST: {len(y)} hours, {complete_days} complete days, "
          f"{len(missing)} incomplete hours ({len(missing) / len(y):.3%})")
    if len(missing):
        print("  incomplete hours (CST):", [str(t) for t in missing])
    d = y["demand_mw"]
    print(f"Demand 2024: min {d.min():,.0f} MW, max {d.max():,.0f} MW, mean {d.mean():,.0f} MW; "
          f"within 20-90 GW: {d.between(20_000, 90_000).mean():.2%}")
    rel = (eia930.fuel_sum(y) - y["net_generation_mw"]).abs() / y["net_generation_mw"]
    print(f"Fuel sum within 2% of net generation: {(rel <= 0.02).mean():.2%} of 2024 hours "
          f"(max dev {rel.max():.2%})")
    ti = y["interchange_mw"]
    print(f"Interchange 2024: {ti.min():,.0f}..{ti.max():,.0f} MW")

    print(f"Plants used: {len(plants)} ({plants['capacity_mw'].sum() / 1000:.1f} GW); by fuel:",
          plants.groupby("fuel")["capacity_mw"].agg(["size", "sum"]).round(0).to_dict("index"))
    print("  tech counts:", plants["tech"].value_counts().to_dict())
    cols = ["capacity_mw", "heat_rate_mmbtu_per_mwh", "co2_t_per_mwh"]
    finite_pos = np.isfinite(plants[cols]).all(axis=1) & (plants[cols] > 0).all(axis=1)
    print(f"  plants with capacity, heat rate, CO2 rate > 0 and finite: {int(finite_pos.sum())}")
    print(f"  emission factors (t/MWh): {egrid.load_emission_factors()}")

    p24 = prices[prices["month"].str.startswith("2024")]
    nan = {f: int(p24[f"{f}_usd_per_mmbtu"].isna().sum()) for f in ("coal", "gas", "oil")}
    print(f"Fuel prices 2024 months: {len(p24)}; NaN months per fuel {nan}; oil source "
          f"{sorted(set(p24.oil_source.dropna()))}")
    print("  gas 2024:", p24.gas_usd_per_mmbtu.round(2).tolist())

    c = curt[curt["date_cst"].str.startswith("2024")]
    cok = curtailment.complete_hours(c)
    print(f"Curtailment 2024 CST: {int(cok.sum())}/{len(c)} complete hours; "
          f"min value {c[list(curtailment.VALUE_COLS)].min().min():.2f}")
    if (~cok).any():
        print("  missing hours (CST):", [str(t) for t in c.loc[~cok, "ts_cst"]])
    for tech in ("wind", "solar"):
        print(f"  {tech}: curtailed {c[f'{tech}_curtailed_mw'].sum() / 1e6:.2f} TWh, "
              f"HSL {c[f'{tech}_hsl_mw'].sum() / 1e6:.1f} TWh, "
              f"telemetered {c[f'{tech}_telemetered_mw'].sum() / 1e6:.1f} TWh")
    if validation:
        for tech in ("wind", "solar"):
            print(f"  NP4 validation {tech}: {validation[tech]}")

    print(f"Days: {split['n_candidates']} candidates; counts {split['counts']}")
    for dd in split["demo_days"]:
        print(f"  demo {dd['date']} {dd['label']}: {dd['description']}")
    print(f"  gate days: {split['herding_gate_days']}")
    print("Processed files:")
    total = 0
    for p in sorted(DATA_PROCESSED.iterdir()):
        total += p.stat().st_size
        print(f"  {p.name}: {p.stat().st_size / 1024:.0f} KB")
    print(f"  total: {total / 1e6:.2f} MB")


def main() -> None:
    eia = _step("eia930", eia930.build_eia930)
    plants = _step("egrid plants + emission factors", egrid.build_plants)
    prices = _step("fuel prices", fuel_prices.build_fuel_prices)
    curt = _step("curtailment", curtailment.build_curtailment)
    validation = _step("curtailment NP4 validation", curtailment.validate_against_np4, curt)
    split = _step("days", days.build_days, eia, curt)
    summarize(eia, plants, prices, curt, split, validation)


if __name__ == "__main__":
    main()
