"""eGRID2023 (rev2) ERCOT fossil plants for the merit-order simulator, plus fuel emission factors.

Source: EPA eGRID2023 rev2, ``data/raw/egrid/egrid2023_data_rev2.xlsx``. Sheet ``PLNT23`` (plant
level; row 0 holds long descriptions, row 1 the column codes -> ``header=1``) and ``GEN23``
(generator level, for prime movers).

Selection: ``BACODE == "ERCO"`` and ``PLFUELCT`` in COAL / GAS / OIL (299 plants). Rules, applied in
order and recorded per plant in ``docs/results/plant_exclusions.json``:

1. Excluded if nameplate capacity, 2023 net generation or 2023 heat input is missing or <= 0
   (retired or inactive in 2023, e.g. J T Deely; or not modelable without a heat rate).
2. Imputed if heat input and generation are present but the CO2 output rate is missing or 0:
   CO2 rate = heat rate x EPA fuel CO2 coefficient for the plant's primary fuel (``PLPRMFL``).
3. Otherwise eGRID values are used as published.

Units: heat rate MMBtu/MWh (= PLHTIAN / PLNGENAN), CO2 rate METRIC tons per MWh
(= PLCO2RTA lb/MWh / 2204.62). eGRID CO2 rates are its CHP-adjusted output emission rates.
"""

from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from clearinghouse.config import DATA_PROCESSED, DATA_RAW, RESULTS_DIR

RAW_FILE = DATA_RAW / "egrid" / "egrid2023_data_rev2.xlsx"
OUT_FILE = DATA_PROCESSED / "plants.parquet"
FACTORS_FILE = DATA_PROCESSED / "fuel_emission_factors.json"
EXCLUSIONS_FILE = RESULTS_DIR / "plant_exclusions.json"

LB_PER_METRIC_TON = 2204.62
FUEL_CATEGORIES = {"COAL": "coal", "GAS": "gas", "OIL": "oil"}

# EPA GHG Emission Factors Hub (Table 1, stationary combustion), kg CO2 per MMBtu.
EPA_KG_CO2_PER_MMBTU = {
    "NG": 53.06,   # natural gas
    "BIT": 93.28,  # bituminous coal
    "SUB": 97.17,  # sub-bituminous coal
    "LIG": 97.72,  # lignite
    "DFO": 73.96,  # distillate fuel oil No. 2
    "RFO": 75.10,  # residual fuel oil No. 6
    "PC": 102.41,  # petroleum coke
}
EPA_SOURCE = "EPA GHG Emission Factors Hub, Table 1 (stationary combustion), kg CO2/MMBtu"

PRIME_MOVER_TECH = {
    "CA": "combined_cycle",  # CC steam part
    "CT": "combined_cycle",  # CC combustion-turbine part
    "CS": "combined_cycle",  # CC single shaft
    "CC": "combined_cycle",
    "GT": "combustion_turbine",
    "ST": "steam",
    "IC": "internal_combustion",
}

PLANT_COLS = (
    "orispl", "name", "fuel", "primary_fuel_code", "prime_mover", "tech", "capacity_mw",
    "heat_rate_mmbtu_per_mwh", "co2_t_per_mwh", "annual_gen_mwh", "capacity_factor", "state",
    "lat", "lon", "data_note",
)


def _read_sheets(raw_file=RAW_FILE) -> tuple[pd.DataFrame, pd.DataFrame]:
    plnt = pd.read_excel(raw_file, sheet_name="PLNT23", header=1)
    gen = pd.read_excel(raw_file, sheet_name="GEN23", header=1)
    return plnt, gen


def _prime_movers(gen: pd.DataFrame, orispls) -> pd.DataFrame:
    g = gen[gen["ORISPL"].isin(orispls) & (gen["GENSTAT"] != "RE")].copy()
    g["tech"] = g["PRMVR"].map(PRIME_MOVER_TECH).fillna("other")
    g["cap"] = pd.to_numeric(g["NAMEPCAP"], errors="coerce").fillna(0.0)
    tech_cap = g.groupby(["ORISPL", "tech"])["cap"].sum().reset_index()
    tech = tech_cap.sort_values(["ORISPL", "cap"], ascending=[True, False]).drop_duplicates("ORISPL")
    pm_cap = g.groupby(["ORISPL", "tech", "PRMVR"])["cap"].sum().reset_index()
    pm = pm_cap.merge(tech[["ORISPL", "tech"]], on=["ORISPL", "tech"])
    pm = pm.sort_values(["ORISPL", "cap"], ascending=[True, False]).drop_duplicates("ORISPL")
    return pm[["ORISPL", "PRMVR", "tech"]].rename(columns={"PRMVR": "prime_mover"})


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def build_plants(raw_file=RAW_FILE, out_file=OUT_FILE, exclusions_file=EXCLUSIONS_FILE,
                 factors_file=FACTORS_FILE) -> pd.DataFrame:
    plnt, gen = _read_sheets(raw_file)
    erco = plnt[plnt["BACODE"] == "ERCO"].copy()
    fos = erco[erco["PLFUELCT"].isin(list(FUEL_CATEGORIES))].copy()

    cap, ht, ngen, co2r = (_num(fos[c]) for c in ("NAMEPCAP", "PLHTIAN", "PLNGENAN", "PLCO2RTA"))
    excluded, imputed, keep_rows = [], [], []
    for idx in fos.index:
        row = fos.loc[idx]
        base = {
            "orispl": int(row["ORISPL"]), "name": str(row["PNAME"]),
            "fuel": FUEL_CATEGORIES[row["PLFUELCT"]], "primary_fuel_code": str(row["PLPRMFL"]),
            "capacity_mw": float(cap[idx]) if np.isfinite(cap[idx]) else None,
        }
        reasons = []
        if not cap[idx] > 0:
            reasons.append("no nameplate capacity")
        if not ngen[idx] > 0:
            reasons.append(f"no positive 2023 net generation (PLNGENAN={_fmt(ngen[idx])})")
        if not ht[idx] > 0:
            reasons.append(f"no 2023 heat input (PLHTIAN={_fmt(ht[idx])})")
        if reasons:
            excluded.append({**base, "reason": "; ".join(reasons) + " -> retired/inactive, excluded"})
            continue
        keep_rows.append(idx)

    kept = fos.loc[keep_rows]
    hr = _num(kept["PLHTIAN"]) / _num(kept["PLNGENAN"])  # MMBtu/MWh
    co2 = _num(kept["PLCO2RTA"]) / LB_PER_METRIC_TON
    notes = pd.Series("", index=kept.index, dtype=object)
    for idx in kept.index:
        if not co2[idx] > 0:
            fuel_code = str(kept.at[idx, "PLPRMFL"])
            coef = EPA_KG_CO2_PER_MMBTU.get(fuel_code)
            if coef is None:
                raise ValueError(f"no EPA coefficient for primary fuel {fuel_code}")
            co2[idx] = hr[idx] * coef / 1000.0  # t/MWh
            note = (f"imputed CO2: eGRID PLCO2RTA={_fmt(_num(kept['PLCO2RTA'])[idx])}; "
                    f"heat rate {hr[idx]:.3f} MMBtu/MWh x {coef} kg/MMBtu ({fuel_code}, {EPA_SOURCE})")
            notes[idx] = note
            imputed.append({
                "orispl": int(kept.at[idx, "ORISPL"]), "name": str(kept.at[idx, "PNAME"]),
                "fuel": FUEL_CATEGORIES[kept.at[idx, "PLFUELCT"]], "primary_fuel_code": fuel_code,
                "capacity_mw": float(kept.at[idx, "NAMEPCAP"]),
                "heat_rate_mmbtu_per_mwh": round(float(hr[idx]), 4),
                "co2_t_per_mwh": round(float(co2[idx]), 4), "reason": note,
            })
    lowhr = hr < 6.0
    notes[lowhr] = notes[lowhr].where(notes[lowhr] == "", notes[lowhr] + "; ") + (
        "heat rate below 6 MMBtu/MWh (CHP heat-input allocation artefact); kept as published")
    outside = kept["PSTATABB"] != "TX"
    notes[outside] = notes[outside].where(notes[outside] == "", notes[outside] + "; ") + (
        "located outside TX but in ERCOT BA")

    pm = _prime_movers(gen, kept["ORISPL"])
    plants = pd.DataFrame({
        "orispl": kept["ORISPL"].astype("int64"),
        "name": kept["PNAME"].astype(str),
        "fuel": kept["PLFUELCT"].map(FUEL_CATEGORIES),
        "primary_fuel_code": kept["PLPRMFL"].astype(str),
        "capacity_mw": _num(kept["NAMEPCAP"]),
        "heat_rate_mmbtu_per_mwh": hr,
        "co2_t_per_mwh": co2,
        "annual_gen_mwh": _num(kept["PLNGENAN"]),
        "capacity_factor": _num(kept["CAPFAC"]),
        "state": kept["PSTATABB"].astype(str),
        "lat": _num(kept["LAT"]),
        "lon": _num(kept["LON"]),
        "data_note": notes,
    })
    plants = plants.merge(pm, left_on="orispl", right_on="ORISPL", how="left").drop(columns="ORISPL")
    missing_pm = plants["prime_mover"].isna()
    plants.loc[missing_pm, "tech"] = "unknown"
    plants.loc[missing_pm, "data_note"] = plants.loc[missing_pm, "data_note"].where(
        plants.loc[missing_pm, "data_note"] == "", plants.loc[missing_pm, "data_note"] + "; "
    ) + "no non-retired GEN23 generator rows"
    plants = plants[list(PLANT_COLS)].sort_values(["fuel", "orispl"]).reset_index(drop=True)

    out_file.parent.mkdir(parents=True, exist_ok=True)
    plants.to_parquet(out_file, index=False)

    summary = {
        "source": "EPA eGRID2023 rev2 (egrid2023_data_rev2.xlsx, PLNT23 + GEN23)",
        "selection": "BACODE == ERCO and PLFUELCT in {COAL, GAS, OIL}",
        "fossil_plants_total": int(len(fos)),
        "fossil_capacity_total_gw": round(float(cap.sum()) / 1000, 3),
        "plants_used": int(len(plants)),
        "capacity_used_gw": round(float(plants["capacity_mw"].sum()) / 1000, 3),
        "plants_imputed": len(imputed),
        "capacity_imputed_gw": round(sum(p["capacity_mw"] for p in imputed) / 1000, 3),
        "plants_excluded": len(excluded),
        "capacity_excluded_gw": round(sum(p["capacity_mw"] or 0 for p in excluded) / 1000, 3),
        "used_by_fuel": {
            f: {"plants": int(len(g)), "capacity_gw": round(float(g["capacity_mw"].sum()) / 1000, 3)}
            for f, g in plants.groupby("fuel")
        },
    }
    exclusions_file.parent.mkdir(parents=True, exist_ok=True)
    exclusions_file.write_text(json.dumps({
        "summary": summary,
        "rules": [
            "Excluded: nameplate capacity, 2023 net generation (PLNGENAN) or 2023 heat input "
            "(PLHTIAN) missing or <= 0.",
            "Imputed: heat input and generation present but PLCO2RTA missing or 0 -> CO2 rate = "
            "heat rate x EPA coefficient for the primary fuel (PLPRMFL).",
        ],
        "epa_kg_co2_per_mmbtu": EPA_KG_CO2_PER_MMBTU,
        "epa_source": EPA_SOURCE,
        "excluded": sorted(excluded, key=lambda p: -(p["capacity_mw"] or 0)),
        "imputed": sorted(imputed, key=lambda p: -p["capacity_mw"]),
    }, indent=2) + "\n")

    _write_emission_factors(erco, plants, factors_file)
    load_plants.cache_clear()
    load_emission_factors.cache_clear()
    return plants


def _write_emission_factors(erco: pd.DataFrame, plants: pd.DataFrame, factors_file) -> dict:
    """Generation-weighted CO2 rate (t/MWh) per EIA-930 fuel category from eGRID ERCOT plants."""
    factors, detail = {}, {}
    for fuel, g in plants.groupby("fuel"):
        w = g["annual_gen_mwh"]
        factors[fuel] = float((g["co2_t_per_mwh"] * w).sum() / w.sum())
        detail[fuel] = {"plants": int(len(g)), "gen_twh": round(float(w.sum()) / 1e6, 3),
                        "basis": "plants.parquet (generation-weighted)"}
    oth = erco[erco["PLFUELCT"].isin(["BIOMASS", "OFSL", "OTHF"])].copy()
    oth_gen = _num(oth["PLNGENAN"])
    oth = oth[oth_gen > 0]
    oth_gen = _num(oth["PLNGENAN"])
    oth_rate = (_num(oth["PLCO2RTA"]).fillna(0.0) / LB_PER_METRIC_TON).clip(lower=0)
    factors["other"] = float((oth_rate * oth_gen).sum() / oth_gen.sum())
    detail["other"] = {
        "plants": int(len(oth)), "gen_twh": round(float(oth_gen.sum()) / 1e6, 3),
        "basis": ("generation-weighted eGRID CO2 output rate of ERCOT plants with PLFUELCT in "
                  "{BIOMASS, OFSL, OTHF} and positive 2023 generation; missing rates (batteries, "
                  "waste heat) count as 0; biomass CO2 as reported by eGRID (0 for most). "
                  "Dominated by Channel Energy Center (other gases). EIA-930 'other' is ~0.2 GW "
                  "in ERCO, so this choice moves hourly CO2 by well under 1%."),
    }
    for zero in ("nuclear", "hydro", "wind", "solar", "storage"):
        factors[zero] = 0.0
        detail[zero] = {"basis": "no direct combustion emissions (storage: charging emissions "
                                 "are counted when the charging energy is generated)"}
    factors["unknown"] = factors["gas"]
    detail["unknown"] = {"basis": "never reported by ERCO in EIA-930; set to the gas factor if "
                                  "ever needed"}
    payload = {
        "units": "metric tons CO2 per MWh",
        "source": "EPA eGRID2023 rev2, ERCOT (BACODE ERCO) plants",
        "eia930_column_map": {f: f"{f}_mw" for f in factors},
        "factors": {k: round(v, 5) for k, v in factors.items()},
        "detail": detail,
    }
    factors_file.parent.mkdir(parents=True, exist_ok=True)
    factors_file.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def _fmt(x) -> str:
    return "NaN" if x is None or not np.isfinite(x) else f"{x:g}"


@lru_cache(maxsize=1)
def load_plants(path=OUT_FILE) -> pd.DataFrame:
    """Processed plant table. Returns the cached frame: do not mutate it (copy first)."""
    return pd.read_parquet(path)


@lru_cache(maxsize=1)
def load_emission_factors(path=FACTORS_FILE) -> dict[str, float]:
    return dict(json.loads(path.read_text())["factors"])
