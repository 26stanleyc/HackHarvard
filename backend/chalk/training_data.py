"""Leakage-free training data via a Chalk point-in-time offline query (design B2).

Not run by the app or pytest; needs chalkpy and deployed features. Docs:
https://docs.chalk.ai/docs/query-offline , https://docs.chalk.ai/docs/time

Each row's ``input_times`` value is that target day's forecast issue time (00:00 CST on D).
Chalk resolves every feature as of that instant: windowed aggregates and the ``hours`` has-many
only include GridHour rows with ``ts`` (slot end) <= issue time. That is the same rule the app's
feature builder enforces and tests (``tests/test_forecast.py``), so training data built here is
leakage-free by construction rather than by careful indexing.

    python training_data.py 2023-12-16 2024-09-30
"""

from __future__ import annotations

import sys

import pandas as pd
from chalk.client import ChalkClient


def main(first: str, last: str) -> None:
    dates = pd.date_range(first, last).strftime("%Y-%m-%d")
    ids = [f"ERCO:{d}" for d in dates]
    # 00:00 CST on D == 06:00 UTC on D
    issue = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=6) for d in dates]
    client = ChalkClient()  # reads credentials from `chalk login` / CHALK_* env vars
    ds = client.offline_query(
        input={"grid_day.id": ids},
        input_times=[t.to_pydatetime() for t in issue],
        output=[
            "grid_day.id", "grid_day.as_of",
            "grid_day.demand_mean__86400__", "grid_day.demand_mean__604800__",
            "grid_day.wind_mean__86400__", "grid_day.solar_mean__86400__",
            "grid_day.demand_max__86400__", "grid_day.demand_min__86400__",
        ],
        recompute_features=True,
        dataset_name="clearinghouse_forecast_training",
    )
    df = ds.get_data_as_pandas()
    print(df.head())
    df.to_parquet("clearinghouse_forecast_training.parquet", index=False)


if __name__ == "__main__":
    main(*sys.argv[1:3])
