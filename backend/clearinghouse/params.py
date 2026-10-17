"""Named simulator parameters (design B1): per-fuel availability and fuel price scaling.

These are the ONLY fitted parameters of the merit-order simulator. M2 runs on the stated
uncalibrated defaults (:data:`DEFAULT_PARAMS`); M3 fits them on calibration training days and
writes ``backend/data/processed/calibrated_params.json``, which :func:`load_params` then returns.

Parameters
----------
``availability[fuel]``
    Fraction of eGRID nameplate capacity available for dispatch, per fuel (``coal``, ``gas``,
    ``oil``). Available capacity of a plant = nameplate x availability[its fuel].
``price_scale[fuel]``
    Multiplier on the month's EIA Texas delivered fuel price ($/MMBtu) for that fuel, applied in
    every month. Marginal cost = heat rate x EIA price(month) x scale(fuel, month).
``monthly_price_scale[month][fuel]``
    Optional per-calendar-month (1..12) override multiplied on top of ``price_scale``. Missing
    months / fuels mean 1.0. This is the "monthly fuel price scaling" of design B1; the
    uncalibrated default has no monthly overrides.

Uncalibrated defaults (stated before any gate result was seen)
--------------------------------------------------------------
* availability coal 0.90, gas 0.90: fossil fleets typically lose ~5-10% of capacity to forced
  and planned outages at any time (NERC GADS equivalent forced outage rates for coal steam and
  gas units are mostly in the 5-10% range); 0.90 is a round mid value, not fitted.
* availability oil 0.50: ERCOT's oil-fired capacity (26 plants, 0.26 GW) is small old
  peakers/backup units that EIA-930 never reports as generating; half availability is a stated,
  deliberately conservative placeholder. They sit at the top of the stack either way.
* price scales 1.0 for every fuel and month: use EIA prices as published.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from clearinghouse.config import DATA_PROCESSED

FUELS: tuple[str, ...] = ("coal", "gas", "oil")
CALIBRATED_PARAMS_FILE = DATA_PROCESSED / "calibrated_params.json"


def _freeze(d: Mapping) -> Mapping:
    return MappingProxyType({k: (_freeze(v) if isinstance(v, Mapping) else float(v))
                             for k, v in d.items()})


@dataclass(frozen=True)
class SimParams:
    """Immutable, hashable simulator parameters (hash/equality by value)."""

    availability: Mapping[str, float]
    price_scale: Mapping[str, float] = field(
        default_factory=lambda: {f: 1.0 for f in FUELS})
    monthly_price_scale: Mapping[int, Mapping[str, float]] = field(default_factory=dict)
    label: str = "custom"

    def __post_init__(self) -> None:
        for f in FUELS:
            if f not in self.availability:
                raise ValueError(f"availability missing fuel {f!r}")
            if not 0.0 <= float(self.availability[f]) <= 1.0:
                raise ValueError(f"availability[{f}] must be in [0, 1]")
        for f, v in self.price_scale.items():
            if f not in FUELS or not float(v) > 0:
                raise ValueError(f"price_scale[{f}] must be a known fuel with value > 0")
        monthly = {int(m): dict(v) for m, v in self.monthly_price_scale.items()}
        for m, v in monthly.items():
            if not 1 <= m <= 12:
                raise ValueError(f"monthly_price_scale month {m} not in 1..12")
            for f, s in v.items():
                if f not in FUELS or not float(s) > 0:
                    raise ValueError(f"monthly_price_scale[{m}][{f}] invalid")
        object.__setattr__(self, "availability", _freeze(self.availability))
        object.__setattr__(self, "price_scale", _freeze(self.price_scale))
        object.__setattr__(self, "monthly_price_scale",
                           MappingProxyType({m: _freeze(v) for m, v in monthly.items()}))

    def scale(self, fuel: str, month: int) -> float:
        """Effective fuel price multiplier for ``fuel`` in calendar ``month`` (1..12)."""
        base = float(self.price_scale.get(fuel, 1.0))
        return base * float(self.monthly_price_scale.get(int(month), {}).get(fuel, 1.0))

    def key(self) -> tuple:
        return (
            tuple(sorted((k, float(v)) for k, v in self.availability.items())),
            tuple(sorted((k, float(v)) for k, v in self.price_scale.items())),
            tuple(sorted((m, tuple(sorted(v.items())))
                         for m, v in self.monthly_price_scale.items())),
        )

    def __hash__(self) -> int:  # label is cosmetic: excluded from hash and equality
        return hash(self.key())

    def __eq__(self, other: object) -> bool:
        return isinstance(other, SimParams) and self.key() == other.key()

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "availability": dict(self.availability),
            "price_scale": dict(self.price_scale),
            "monthly_price_scale": {str(m): dict(v) for m, v in self.monthly_price_scale.items()},
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> SimParams:
        return cls(
            availability=dict(d["availability"]),
            price_scale=dict(d.get("price_scale", {f: 1.0 for f in FUELS})),
            monthly_price_scale={int(m): dict(v)
                                 for m, v in d.get("monthly_price_scale", {}).items()},
            label=str(d.get("label", "custom")),
        )


DEFAULT_PARAMS = SimParams(
    availability={"coal": 0.90, "gas": 0.90, "oil": 0.50},
    price_scale={"coal": 1.0, "gas": 1.0, "oil": 1.0},
    monthly_price_scale={},
    label="uncalibrated_default",
)


def load_params(path: Path | None = None, verbose: bool = False) -> tuple[SimParams, str]:
    """Return ``(params, source)``: calibrated params from ``path`` (default
    ``calibrated_params.json``, written by M3) if the file exists, else :data:`DEFAULT_PARAMS`.

    ``source`` is ``"calibrated:<path>"`` or ``"uncalibrated_default"``. The JSON holds
    :meth:`SimParams.to_dict` at the top level or under a ``"params"`` key.
    """
    p = Path(path) if path is not None else CALIBRATED_PARAMS_FILE
    if p.is_file():
        raw = json.loads(p.read_text())
        params = SimParams.from_dict(raw.get("params", raw))
        source = f"calibrated:{p}"
    else:
        params, source = DEFAULT_PARAMS, "uncalibrated_default"
    if verbose:
        print(f"simulator params: {source}")
    return params, source
