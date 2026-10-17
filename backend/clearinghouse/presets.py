"""Example companies for the bid builder (GET /api/presets; design B3 "Sizing").

ERCOT peaks in the tens of GW, so a single site barely moves the margin; presets therefore
represent portfolios or aggregators sized from the kind of public figure named in
``size_reference``. The sizes are illustrative round numbers, not any real company's bid.
Windows use the API hour convention (``earliest_hour`` inclusive, ``deadline_hour`` exclusive,
fixed-CST slots). Every preset is feasible with slack (energy / (max_mw x window) <= 0.6).

The example scenario (``fixtures/clear_request.json``, built by
``scripts/regenerate_fixtures.py``) is exactly one company per preset, in this order, with ids
``b1..bn``: a fixed rule, chosen before any result was seen.
"""

from __future__ import annotations

from clearinghouse.schemas import Bid, Preset, bid_feasibility_error

PRESETS: tuple[Preset, ...] = (
    Preset(
        name="EV fleet aggregator",
        load_type="ev_fleet",
        energy_mwh=2000.0,
        earliest_hour=0,
        deadline_hour=7,
        max_mw=500.0,
        size_reference=(
            "Overnight depot and home charging aggregated across Texas metros. A single large "
            "electric bus or truck depot is roughly 5-20 MW (utility make-ready and "
            "interconnection filings), so 500 MW stands for a regional aggregator (illustrative)."
        ),
    ),
    Preset(
        name="Data center (flexible batch)",
        load_type="data_center",
        energy_mwh=3600.0,
        earliest_hour=0,
        deadline_hour=24,
        max_mw=300.0,
        size_reference=(
            "Large hyperscale data-center campuses draw 100-300+ MW (public utility filings; "
            "ERCOT tracks loads of 75 MW and up as large loads). Only deferrable batch work "
            "(training, rendering, backups) is bid; illustrative."
        ),
    ),
    Preset(
        name="Office portfolio (pre-cooling)",
        load_type="office",
        energy_mwh=900.0,
        earliest_hour=6,
        deadline_hour=18,
        max_mw=150.0,
        size_reference=(
            "A large downtown office tower's HVAC peaks at a few MW (building energy "
            "benchmarking disclosures); 150 MW stands for a portfolio of dozens of towers across "
            "Houston and Dallas pre-cooling ahead of the afternoon peak (illustrative)."
        ),
    ),
    Preset(
        name="Cold-storage portfolio",
        load_type="cold_storage",
        energy_mwh=480.0,
        earliest_hour=0,
        deadline_hour=24,
        max_mw=80.0,
        size_reference=(
            "A large refrigerated warehouse draws roughly 1-5 MW (utility efficiency case "
            "studies); 80 MW stands for a statewide cold-chain portfolio using its thermal mass "
            "to shift a quarter of its daily refrigeration energy (illustrative)."
        ),
    ),
    Preset(
        name="Municipal water pumping",
        load_type="water_pumping",
        energy_mwh=960.0,
        earliest_hour=0,
        deadline_hour=16,
        max_mw=120.0,
        size_reference=(
            "Pumping for a large city water system uses tens of MW (municipal utility energy "
            "reports); 120 MW stands for several Texas metro systems filling storage tanks "
            "before the evening peak (illustrative)."
        ),
    ),
)


def preset_bids(prefix: str = "b") -> list[Bid]:
    """One bid per preset, ids ``b1..bn`` in preset order (the example-scenario rule)."""
    return [Bid(id=f"{prefix}{k}", **p.model_dump(exclude={"size_reference"}))
            for k, p in enumerate(PRESETS, start=1)]


def _check() -> None:
    for b in preset_bids():
        err = bid_feasibility_error(b)
        if err:
            raise AssertionError(f"preset {b.name!r} infeasible: {err}")


_check()
