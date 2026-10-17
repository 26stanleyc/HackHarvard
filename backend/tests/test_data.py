"""M1 data-ingestion checks on the processed files (thresholds pre-registered in docs/design.md).

Skipped only if the processed files are absent (run ``uv run python scripts/build_processed.py``).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from clearinghouse.config import DATA_PROCESSED, RESULTS_DIR
from clearinghouse.data import curtailment, eia930

EIA = DATA_PROCESSED / "eia930_erco_hourly.parquet"
PLANTS = DATA_PROCESSED / "plants.parquet"
PRICES = DATA_PROCESSED / "fuel_prices_monthly.parquet"
CURT = DATA_PROCESSED / "curtailment_hourly.parquet"
FACTORS = DATA_PROCESSED / "fuel_emission_factors.json"
DAYS = DATA_PROCESSED / "days.json"
EXCLUSIONS = RESULTS_DIR / "plant_exclusions.json"


def _need(*paths):
    missing = [p.name for p in paths if not p.is_file()]
    if missing:
        pytest.skip(f"processed files absent: {missing}")


@pytest.fixture(scope="module")
def eia() -> pd.DataFrame:
    _need(EIA)
    return pd.read_parquet(EIA)


@pytest.fixture(scope="module")
def eia2024(eia) -> pd.DataFrame:
    return eia[eia["date_cst"].str.startswith("2024")]


@pytest.fixture(scope="module")
def curt() -> pd.DataFrame:
    _need(CURT)
    return pd.read_parquet(CURT)


@pytest.fixture(scope="module")
def plants() -> pd.DataFrame:
    _need(PLANTS)
    return pd.read_parquet(PLANTS)


# ---------------------------------------------------------------- EIA-930
def test_eia930_structure(eia):
    assert list(eia.columns) == list(eia930.OUTPUT_COLS)
    assert eia["ts_utc"].is_unique and eia["ts_utc"].is_monotonic_increasing
    assert (eia["ts_utc"].diff().dropna() == pd.Timedelta(hours=1)).all()
    # every CST day has exactly 24 slots 0..23
    per_day = eia.groupby("date_cst")["hour_cst"].apply(lambda h: sorted(h) == list(range(24)))
    assert per_day.all()
    assert eia["date_cst"].min() == "2023-12-01" and eia["date_cst"].max() == "2025-01-31"


def test_eia930_2024_coverage(eia2024):
    assert len(eia2024) == 366 * 24
    ok = eia930.complete_hours(eia2024)
    complete_days = int((ok.groupby(eia2024["date_cst"]).sum() == 24).sum())
    assert complete_days >= 360
    assert (~ok).mean() < 0.02


def test_eia930_demand_range(eia2024):
    d = eia2024["demand_mw"].dropna()
    assert len(d) == len(eia2024)
    assert d.between(20_000, 90_000).all()


def test_eia930_fuel_sum_matches_net_generation(eia2024):
    rel = (eia930.fuel_sum(eia2024) - eia2024["net_generation_mw"]).abs() / eia2024[
        "net_generation_mw"]
    assert (rel <= 0.02).mean() >= 0.95


def test_eia930_values_plausible(eia2024):
    for col in ("coal_mw", "gas_mw", "nuclear_mw", "solar_mw", "wind_mw"):
        assert (eia2024[col].dropna() >= -50).all(), col
    assert eia2024["interchange_mw"].abs().max() < 5_000  # ERCOT is nearly islanded


# ---------------------------------------------------------------- eGRID
def test_plants_complete(plants):
    assert len(plants) > 100
    cols = ["capacity_mw", "heat_rate_mmbtu_per_mwh", "co2_t_per_mwh"]
    vals = plants[cols].to_numpy(dtype=float)
    assert np.isfinite(vals).all() and (vals > 0).all()
    assert set(plants["fuel"]) <= {"coal", "gas", "oil"}
    assert plants["orispl"].is_unique
    assert plants["tech"].notna().all()
    # CO2 rates in metric t/MWh: physically ~0.3-1.5 for fossil plants (CHP-adjusted can be lower)
    assert plants["co2_t_per_mwh"].between(0.1, 2.5).all()


def test_plant_exclusions_listed(plants):
    _need(EXCLUSIONS)
    ex = json.loads(EXCLUSIONS.read_text())
    s = ex["summary"]
    assert s["plants_used"] == len(plants)
    assert s["plants_used"] + s["plants_excluded"] == s["fossil_plants_total"]
    assert len(ex["excluded"]) == s["plants_excluded"]
    assert len(ex["imputed"]) == s["plants_imputed"]
    for p in ex["excluded"] + ex["imputed"]:
        assert p["reason"]
    excluded_ids = {p["orispl"] for p in ex["excluded"]}
    assert not excluded_ids & set(plants["orispl"])
    assert 6181 in excluded_ids  # J T Deely (retired)
    imputed_ids = {p["orispl"] for p in ex["imputed"]}
    assert imputed_ids <= set(plants["orispl"])
    notes = plants.set_index("orispl").loc[sorted(imputed_ids), "data_note"]
    assert notes.str.contains("imputed").all()


def test_emission_factors():
    _need(FACTORS)
    f = json.loads(FACTORS.read_text())["factors"]
    for k in ("coal", "gas", "oil", "other", "nuclear", "hydro", "wind", "solar", "storage"):
        assert k in f and np.isfinite(f[k]) and f[k] >= 0
    assert f["coal"] > f["gas"] > 0
    assert f["nuclear"] == f["wind"] == f["solar"] == f["hydro"] == 0


# ---------------------------------------------------------------- fuel prices
def test_fuel_prices_2024():
    _need(PRICES)
    p = pd.read_parquet(PRICES)
    p24 = p[p["month"].str.startswith("2024")]
    assert sorted(p24["month"]) == [f"2024-{m:02d}" for m in range(1, 13)]
    for col in ("coal_usd_per_mmbtu", "gas_usd_per_mmbtu"):
        assert p24[col].notna().all() and (p24[col] > 0).all(), col
        assert p24[col].between(0.5, 20).all(), col


# ---------------------------------------------------------------- curtailment
def test_curtailment_coverage_2024(curt):
    c = curt[curt["date_cst"].str.startswith("2024")]
    assert len(c) == 366 * 24
    ok = curtailment.complete_hours(c)
    assert ok.all(), f"missing curtailment hours: {list(c.loc[~ok, 'ts_cst'].astype(str))}"


def test_curtailment_nonnegative(curt):
    vals = curt[list(curtailment.VALUE_COLS)]
    assert (vals.dropna() >= 0).all().all()
    for tech in ("wind", "solar"):
        assert (curt[f"{tech}_curtailed_mw"] <= curt[f"{tech}_hsl_mw"] + 1).all()


def test_curtailment_aligned_with_eia930(eia, curt):
    assert (eia["ts_utc"].to_numpy() == curt["ts_utc"].to_numpy()).all()
    # Same physical quantity from two sources: hourly wind output must line up in the same slot.
    m = eia[["ts_utc", "wind_mw"]].merge(curt[["ts_utc", "wind_telemetered_mw"]], on="ts_utc")
    assert m["wind_mw"].corr(m["wind_telemetered_mw"]) > 0.98


# ---------------------------------------------------------------- days
def test_days_split(eia, curt):
    _need(DAYS)
    d = json.loads(DAYS.read_text())
    demo = [x["date"] for x in d["demo_days"]]
    sets = {"demo": set(demo), "gate": set(d["herding_gate_days"]),
            "train": set(d["calibration_train_days"]),
            "holdout": set(d["calibration_holdout_days"])}
    names = list(sets)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not sets[a] & sets[b], (a, b)
    assert len(demo) == 4 and len(sets["demo"]) == 4
    assert len(sets["gate"]) == 7
    assert sum(map(len, sets.values())) == d["n_candidates"]
    assert set(d["never_trained_days"]).isdisjoint(sets["train"])
    assert sets["demo"] | sets["gate"] <= set(d["never_trained_days"])
    # holdout roughly 30% of the calibration pool
    pool = len(sets["train"]) + len(sets["holdout"])
    assert 0.25 <= len(sets["holdout"]) / pool <= 0.35
    for day in demo:
        e = eia[eia["date_cst"] == day]
        c = curt[curt["date_cst"] == day]
        assert len(e) == 24 and eia930.complete_hours(e).all(), day
        assert len(c) == 24 and curtailment.complete_hours(c).all(), day
    for x in d["demo_days"]:
        assert x["label"] and x["rule"] and x["description"]


def test_days_deterministic(eia, curt):
    _need(DAYS)
    from clearinghouse.data import days

    payload = days.build_days(eia, curt, out_file=DAYS.parent / ".days_rebuild_check.json")
    try:
        assert payload == json.loads(DAYS.read_text())
    finally:
        (DAYS.parent / ".days_rebuild_check.json").unlink(missing_ok=True)
