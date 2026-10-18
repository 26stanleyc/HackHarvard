"""M12: does the day-ahead price rank the actual dirtiest / cleanest hours? (evidence only)

    uv run python scripts/price_signal.py

Pre-registered in design M12. For every day, Spearman rho across the CST slots between:

* the point-in-time DAM price (``HB_HUBAVG``, archived file posted before 00:00 CST on the day;
  in daylight time slot 23 has no price and is left out for every signal), and
* the simulator's hourly marginal CO2 rate on the day's ACTUAL inputs (calibrated params, zero
  added load; ``DaySimulator.marginal_rate``, the 100 MW probe).

The same rho is computed, on the same slots, for the signals the planners already use: average
intensity and marginal rate from the simulator on the served forecast inputs (local provider's
default model). Days where the actual marginal rate (or a signal) is constant over the slots have
no rho; they are excluded and counted. Primary period: the forecast test period
(2024-10-01..12-31); secondary: every 2024 day. Nothing in planning uses the price.

Writes ``docs/results/price_signal.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from clearinghouse.config import RESULTS_DIR  # noqa: E402
from clearinghouse.data import load_days  # noqa: E402
from clearinghouse.data.forecast_inputs import load_forecast_inputs  # noqa: E402
from clearinghouse.forecast.local import LocalForecastProvider  # noqa: E402
from clearinghouse.inputs import actual_inputs, forecast_inputs  # noqa: E402
from clearinghouse.params import load_params  # noqa: E402
from clearinghouse.simulator import DaySimulator  # noqa: E402

OUT_FILE = RESULTS_DIR / "price_signal.json"
SIGNALS = ("dam_price", "forecast_avg_intensity", "forecast_marginal_rate")


def rho(x: np.ndarray, y: np.ndarray) -> float:
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return float("nan")
    return float(spearmanr(x, y).statistic)


def summarize(per_day: pd.DataFrame) -> dict:
    out = {"days": int(len(per_day))}
    for s in SIGNALS:
        r = per_day[s].dropna()
        out[s] = {"days_with_rho": int(len(r)),
                  "days_excluded_constant": int(len(per_day) - len(r)),
                  "median_rho": round(float(r.median()), 3), "mean_rho": round(float(r.mean()), 3),
                  "share_days_rho_positive": round(float((r > 0).mean()), 3)}
    out["mean_slots_with_zero_actual_marginal_rate"] = round(
        float(per_day["zero_rate_slots"].mean()), 2)
    return out


def main() -> None:
    params, _ = load_params()
    provider = LocalForecastProvider()
    fin = load_forecast_inputs()
    split = load_days()["forecast_split"]
    rows, skipped = [], []
    for day in pd.date_range("2024-01-01", "2024-12-31").strftime("%Y-%m-%d"):
        dam = fin[fin["date_cst"] == day].sort_values("hour_cst")["dam_price"].to_numpy()
        ok = np.isfinite(dam)
        if ok.sum() < 2:
            continue
        try:
            actual = DaySimulator(actual_inputs(day), params).marginal_rate()[ok]
        except ValueError as e:  # e.g. 2024-03-28 (missing interchange, excluded in M1)
            skipped.append({"date": day, "reason": str(e)})
            continue
        sim_fc = DaySimulator(forecast_inputs(provider.get_forecast("ERCO", day)), params)
        sig = {"dam_price": dam[ok], "forecast_avg_intensity": sim_fc.avg_intensity()[ok],
               "forecast_marginal_rate": sim_fc.marginal_rate()[ok]}
        rows.append({"date": day, "slots": int(ok.sum()),
                     "zero_rate_slots": int((actual <= 1e-9).sum()),
                     **{k: rho(v, actual) for k, v in sig.items()}})
    per_day = pd.DataFrame(rows)
    in_test = per_day["date"].between(split["test_start"], split["test_end"])
    test = per_day[in_test]
    payload = {
        "milestone": "M12",
        "question": "does the DAM price rank the actual hourly marginal CO2 rate? (Spearman rho "
                    "across a day's slots; evidence only, planning unchanged)",
        "price": "ERCOT NP4-190-CD HB_HUBAVG from archived original files posted before 00:00 CST "
                 "on the day",
        "actual": "DaySimulator(actual_inputs(day), calibrated params).marginal_rate(): 100 MW "
                  "probe, zero added load",
        "forecast_model_set": provider.spec.get("model_set", "M4"),
        "test_period": summarize(test),
        "all_2024": summarize(per_day),
        "skipped_days": skipped,
        "per_day": per_day.round(4).to_dict(orient="records"),
    }
    OUT_FILE.write_text(json.dumps(payload, indent=2) + "\n")
    for label in ("test_period", "all_2024"):
        p = payload[label]
        print(f"{label} ({p['days']} days; mean slots with zero actual marginal rate "
              f"{p['mean_slots_with_zero_actual_marginal_rate']}):")
        for s in SIGNALS:
            q = p[s]
            print(f"  {s:<24} median rho {q['median_rho']:+.3f}  mean {q['mean_rho']:+.3f}  "
                  f"rho>0 on {q['share_days_rho_positive']:.0%} of {q['days_with_rho']} days "
                  f"({q['days_excluded_constant']} excluded)")
    print(f"wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
