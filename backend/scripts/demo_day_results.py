"""Example scenario on every demo day, for comparing forecast model sets (M10).

    uv run python scripts/demo_day_results.py

Uses the pinned example scenario (``regenerate_fixtures.example_requests``: one company per
preset, default background, carbon price and pool) with the day set to each pre-registered demo
day, and calls ``POST /api/clear`` in process. Records per day: the forecast's hourly MAE against
the day's EIA-930 actuals, every strategy's total CO2 (scored on actuals), the value of
coordination (``settlement.total_avoided_t``), whether the planning-time fallback fired, and the
total paid. Writes ``docs/results/demo_days_<model set>.json`` (model set from the default
forecast artifacts: ``M4`` or ``M10``).
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clearinghouse.config import RESULTS_DIR  # noqa: E402
from clearinghouse.data import load_eia930  # noqa: E402
from clearinghouse.forecast import model as M  # noqa: E402

warnings.filterwarnings("ignore", message=".*httpx.*")
from fastapi.testclient import TestClient  # noqa: E402
from regenerate_fixtures import example_requests  # noqa: E402

from clearinghouse.api import app  # noqa: E402


def main() -> None:
    spec = json.loads((M.ARTIFACT_DIR / M.SPEC_FILE).read_text())
    model_set = spec.get("model_set", "M4")
    eia = load_eia930()
    out = {"model_set": model_set, "scenario": "example scenario (one company per preset, "
                                               "default background/carbon price/pool)", "days": {}}
    with TestClient(app) as client:
        meta = client.get("/api/meta").json()
        clear_req, _ = example_requests(meta)
        for d in meta["days"]:
            day = d["date"]
            r = client.post("/api/clear", json={**clear_req, "day": day})
            r.raise_for_status()
            j = r.json()
            act = eia[eia["date_cst"] == day].sort_values("hour_cst")
            fc = j["forecast"]["hourly"]
            mae = {k: round(float(np.mean(np.abs(np.array(fc[k]) - act[f"{k}_mw"].to_numpy()))), 1)
                   for k in ("demand", "wind", "solar")}
            tot = {k: round(v["total_co2_t"], 1) for k, v in j["strategies"].items()}
            st = j["settlement"]
            out["days"][day] = {
                "label": d["label"], "forecast_mae_mw": mae, "total_co2_t": tot,
                "coordinated_vs_default_t": round(tot["coordinated"] - tot["default"], 1),
                "value_of_coordination_t": round(st["total_avoided_t"], 1),
                "fallback_fired": j["fallback_fired"],
                "paid_usd": round(sum(b["payout_usd"] for b in st["per_bid"]), 0),
                "herding_gap_t": {k: round(v["gap_t"], 1) for k, v in j["herding"].items()},
            }
            o = out["days"][day]
            print(f"{model_set} {day} {d['label']:<20} fc MAE {mae}  value of coordination "
                  f"{o['value_of_coordination_t']:+.1f} t  coord-default "
                  f"{o['coordinated_vs_default_t']:+.1f} t  fallback {o['fallback_fired']}  "
                  f"paid ${o['paid_usd']:,.0f}")
    path = RESULTS_DIR / f"demo_days_{model_set}.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
