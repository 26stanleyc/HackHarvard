"""Data ingestion (M1): raw downloads -> small processed tables in ``backend/data/processed``.

Each module has a ``build_*()`` (raw -> processed) and a cached ``load_*()`` (processed only).
Rebuild everything with ``uv run python scripts/build_processed.py``.

Time base (all modules): fixed Central Standard Time (UTC-6) days of 24 hourly slots, no DST;
see ``clearinghouse.data.timebase``.
"""

from clearinghouse.data.curtailment import build_curtailment, load_curtailment
from clearinghouse.data.days import build_days, load_days
from clearinghouse.data.egrid import build_plants, load_emission_factors, load_plants
from clearinghouse.data.eia930 import build_eia930, load_eia930
from clearinghouse.data.fuel_prices import build_fuel_prices, load_fuel_prices

__all__ = [
    "build_curtailment", "build_days", "build_eia930", "build_fuel_prices", "build_plants",
    "load_curtailment", "load_days", "load_eia930", "load_emission_factors", "load_fuel_prices",
    "load_plants",
]
