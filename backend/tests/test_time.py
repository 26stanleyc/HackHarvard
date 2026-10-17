"""Time-base checks: every source lands in fixed-CST (UTC-6) slots 0-23."""

from __future__ import annotations

import pandas as pd
import pytest

from clearinghouse.config import DATA_PROCESSED
from clearinghouse.data.timebase import (
    add_cst_columns,
    central_local_to_utc,
    eia930_utc_end_to_start,
    utc_hour_index,
)


def _cst(ts_utc: pd.Series) -> pd.DataFrame:
    return add_cst_columns(pd.DataFrame({"ts_utc": ts_utc}))


def test_eia930_winter_row():
    # UTC end-of-hour 2024-01-01 07:00 -> UTC start 06:00 -> CST 2024-01-01 slot 0
    start = eia930_utc_end_to_start(pd.Series(["01/01/2024 7:00:00 AM"]))
    assert start.iloc[0] == pd.Timestamp("2024-01-01 06:00", tz="UTC")
    r = _cst(start).iloc[0]
    assert (r.date_cst, r.hour_cst) == ("2024-01-01", 0)


def test_eia930_summer_row():
    # Bulk row: Data Date 07/01/2024, Hour Number 1 (01:00 CDT end), UTC end 06:00 AM.
    # UTC start 05:00 = 00:00 CDT = 23:00 CST on Jun 30 -> CST 2024-06-30 slot 23.
    start = eia930_utc_end_to_start(pd.Series(["07/01/2024 6:00:00 AM"]))
    r = _cst(start).iloc[0]
    assert (r.date_cst, r.hour_cst) == ("2024-06-30", 23)
    # and 2024-07-01 13:00 UTC end (12:00 UTC start) = 06:00 CST slot 6
    r = _cst(eia930_utc_end_to_start(pd.Series(["07/01/2024 1:00:00 PM"]))).iloc[0]
    assert (r.date_cst, r.hour_cst) == ("2024-07-01", 6)


def test_sced_fall_back_repeated_hour():
    local = pd.Series(["2024-11-03 00:00", "2024-11-03 01:00", "2024-11-03 01:00",
                       "2024-11-03 02:00"])
    flag = pd.Series(["N", "N", "Y", "N"])
    utc = central_local_to_utc(local, flag)
    assert list(utc) == [pd.Timestamp(t, tz="UTC") for t in (
        "2024-11-03 05:00", "2024-11-03 06:00", "2024-11-03 07:00", "2024-11-03 08:00")]
    cst = _cst(utc)
    # 00:00 CDT = 23:00 CST previous day; first 01:00 (CDT) = slot 0; repeated 01:00 (CST) = slot 1
    assert list(zip(cst.date_cst, cst.hour_cst, strict=True)) == [
        ("2024-11-02", 23), ("2024-11-03", 0), ("2024-11-03", 1), ("2024-11-03", 2)]


def test_sced_spring_forward():
    local = pd.Series(["2024-03-10 01:00", "2024-03-10 03:00"])
    utc = central_local_to_utc(local, pd.Series(["N", "N"]))
    # 01:00 CST and 03:00 CDT are consecutive hours: CST slots 1 and 2
    cst = _cst(utc)
    assert list(cst.hour_cst) == [1, 2]
    with pytest.raises(ValueError):
        central_local_to_utc(pd.Series(["2024-03-10 02:00"]), pd.Series(["N"]))


def test_sced_summer_hour():
    utc = central_local_to_utc(pd.Series(["2024-07-15 17:00"]), pd.Series(["N"]))
    assert utc.iloc[0] == pd.Timestamp("2024-07-15 22:00", tz="UTC")
    assert _cst(utc).iloc[0].hour_cst == 16


def test_window_index():
    idx = utc_hour_index()
    assert idx[0] == pd.Timestamp("2023-12-01 06:00", tz="UTC")
    assert idx[-1] == pd.Timestamp("2025-02-01 05:00", tz="UTC")
    assert len(idx) == 24 * 428


def test_processed_rows_use_cst():
    eia_path = DATA_PROCESSED / "eia930_erco_hourly.parquet"
    curt_path = DATA_PROCESSED / "curtailment_hourly.parquet"
    if not (eia_path.is_file() and curt_path.is_file()):
        pytest.skip("processed files absent")
    e = pd.read_parquet(eia_path).set_index("ts_utc")
    r = e.loc[pd.Timestamp("2024-01-01 06:00", tz="UTC")]
    assert (r.date_cst, r.hour_cst) == ("2024-01-01", 0)
    assert r.ts_cst == pd.Timestamp("2024-01-01 00:00")
    # EIA-930 bulk: 07/01/2024 Hour Number 1 (UTC end 06:00 AM) has demand 61,479 MW
    r = e.loc[pd.Timestamp("2024-07-01 05:00", tz="UTC")]
    assert (r.date_cst, r.hour_cst, r.demand_mw) == ("2024-06-30", 23, 61479.0)
    c = pd.read_parquet(curt_path).set_index("ts_utc")
    # SCED repeated 01:00 (CST) on 2024-11-03: wind HSL 20,418.33 MW -> CST slot 1
    r = c.loc[pd.Timestamp("2024-11-03 07:00", tz="UTC")]
    assert (r.date_cst, r.hour_cst, r.wind_hsl_mw) == ("2024-11-03", 1, 20418.33)
    # first (CDT) 01:00: wind HSL 22,075.47 -> slot 0
    r = c.loc[pd.Timestamp("2024-11-03 06:00", tz="UTC")]
    assert (r.date_cst, r.hour_cst, r.wind_hsl_mw) == ("2024-11-03", 0, 22075.47)
