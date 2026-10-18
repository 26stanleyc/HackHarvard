"""M10 + M12 checks: forecast inputs are leakage-free (design M10, M12; pre-registered there).

(a) ERCOT point-in-time extraction on synthetic postings: only postings before the issue time,
the latest complete one, DST handled; adding or changing postings at/after the issue time changes
nothing. (b) Open-Meteo: only ``*_previous_day2`` columns are accepted. (c) The processed table:
every ERCOT posting before its day's issue time, every Open-Meteo run initialized >= 12 h before
it, no forbidden columns. (d) Feature level: every new feature's source time is before as_of for
every train/test day, values published at/after as_of are masked, later days' inputs are never read.
M12: the same for ERCOT's load forecast (NP3-565-CD) and DAM prices (archived NP4-190-CD files).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from clearinghouse.data import load_eia930
from clearinghouse.data.forecast_inputs import (
    ERCOT_COLS,
    OM_LEAD,
    OUT_FILE,
    dam_point_in_time,
    ercot_load_point_in_time,
    ercot_point_in_time,
    load_forecast_inputs,
    open_meteo_slots,
    power_curve,
)
from clearinghouse.forecast import features as F

WIND = ERCOT_COLS["wind"]


def _posting(target_day: str, posted_local: str, value: float, n_hours: int = 24,
             cdt: bool = False) -> pd.DataFrame:
    """One synthetic NP4 posting covering ``target_day``'s CST slots (hour ending, local time)."""
    d = pd.Timestamp(target_day)
    rows = []
    for h in range(n_hours):  # CST slot h -> local hour ending h+1 (CST) or h+2 (CDT)
        local_end = d + pd.Timedelta(hours=h + 1 + (1 if cdt else 0))
        day = (local_end - pd.Timedelta(hours=1)).strftime("%Y-%m-%d")
        he = int((local_end - pd.Timestamp(day)) / pd.Timedelta(hours=1))
        rows.append({"target_day_cst": target_day, "postedDatetime": posted_local,
                     "deliveryDate": day, "hourEnding": he, "DSTFlag": False,
                     "STWPFSystemWide": value + h, "WGRPPSystemWide": value - 1.0,
                     "COPHSLSystemWide": value + 1.0})
    return pd.DataFrame(rows)


# --- (a) ERCOT point in time -------------------------------------------------------------------


def test_latest_posting_strictly_before_issue_time_is_used():
    day = "2024-12-02"  # CST: issue time 2024-12-02 00:00 local
    p = pd.concat([_posting(day, "2024-12-01T21:55:00", 100.0),
                   _posting(day, "2024-12-01T23:55:00", 200.0),
                   _posting(day, "2024-12-02T00:00:00", 900.0),  # exactly at I: excluded
                   _posting(day, "2024-12-02T00:55:00", 999.0)])  # after I
    out = ercot_point_in_time(p, WIND, "posted")
    assert len(out) == 24
    assert (out["posted"] == pd.Timestamp("2024-12-02 05:55", tz="UTC")).all()
    np.testing.assert_array_equal(out["ercot_stwpf"], 200.0 + np.arange(24))
    assert out["ts_utc"].iloc[0] == pd.Timestamp("2024-12-02 06:00", tz="UTC")


def test_postings_at_or_after_issue_time_change_nothing():
    day = "2024-12-02"
    base = pd.concat([_posting(day, "2024-12-01T22:55:00", 150.0),
                      _posting(day, "2024-12-01T23:55:00", 200.0)])
    a = ercot_point_in_time(base, WIND, "posted")
    later = pd.concat([base, _posting(day, "2024-12-02T00:00:00", -5e5),
                       _posting(day, "2024-12-02T03:55:00", 7e5)])
    b = ercot_point_in_time(later, WIND, "posted")
    pd.testing.assert_frame_equal(a, b)


def test_dst_day_uses_cdt_posting_before_midnight_cst():
    day = "2024-07-01"  # CDT: I = 00:00 CST = 01:00 CDT local
    p = pd.concat([_posting(day, "2024-07-01T00:55:00", 300.0, cdt=True),  # 23:55 CST
                   _posting(day, "2024-07-01T01:00:00", 900.0, cdt=True),  # = I
                   _posting(day, "2024-07-01T01:55:00", 999.0, cdt=True)])
    out = ercot_point_in_time(p, WIND, "posted")
    assert len(out) == 24 and (out["posted"] == pd.Timestamp("2024-07-01 05:55", tz="UTC")).all()
    np.testing.assert_array_equal(out["ercot_stwpf"], 300.0 + np.arange(24))
    assert out["ts_utc"].iloc[-1] == pd.Timestamp("2024-07-02 05:00", tz="UTC")


def test_incomplete_or_too_old_postings_are_skipped():
    day = "2024-12-02"
    p = pd.concat([_posting(day, "2024-12-01T20:55:00", 100.0),
                   _posting(day, "2024-12-01T23:55:00", 200.0, n_hours=23)])
    out = ercot_point_in_time(p, WIND, "posted")
    np.testing.assert_array_equal(out["ercot_stwpf"], 100.0 + np.arange(24))
    old = _posting(day, "2024-12-01T17:55:00", 50.0)  # 6 h 5 min before I: outside the window
    assert ercot_point_in_time(old, WIND, "posted").empty


# --- (b) Open-Meteo columns -------------------------------------------------------------------


@pytest.mark.parametrize("col", ["temperature_2m", "temperature_2m_previous_day1",
                                 "temperature_2m_previous_day0"])
def test_open_meteo_rejects_anything_but_previous_day2(col):
    raw = pd.DataFrame({"point_id": ["p"], "time_utc": ["2024-06-01T00:00"], col: [20.0]})
    with pytest.raises(ValueError, match="previous_day2"):
        open_meteo_slots(raw, {}, pd.DatetimeIndex([], tz="UTC"))


def test_open_meteo_slot_alignment_and_power_curve():
    times = pd.date_range("2024-06-01 06:00", periods=3, freq="h", tz="UTC")
    raw = pd.DataFrame({"point_id": "w", "time_utc": times.strftime("%Y-%m-%dT%H:%M"),
                        "wind_speed_100m_previous_day2": [2.0, 12.0, 30.0],
                        "shortwave_radiation_previous_day2": [0.0, 100.0, 300.0],
                        "temperature_2m_previous_day2": [20.0, 22.0, 24.0]})
    pts = {k: [{"id": "w", "weight": 1.0}] for k in ("wind", "solar", "load")}
    out = open_meteo_slots(raw, pts, times[:2])
    np.testing.assert_allclose(out["om_wind100"], [7.0, 21.0])  # mean of slot start and end
    np.testing.assert_allclose(out["om_temp"], [21.0, 23.0])
    np.testing.assert_allclose(out["om_ghi"], [100.0, 300.0])  # value stamped at slot end
    np.testing.assert_allclose(out["om_wind_power"], [0.5, 0.5])  # (0 + 1) / 2, (1 + 0) / 2
    np.testing.assert_allclose(power_curve(np.array([2.9, 3.0, 12.0, 25.0, 25.1])),
                               [0.0, 0.0, 1.0, 1.0, 0.0])


# --- (c) the processed table -------------------------------------------------------------------


@pytest.fixture(scope="module")
def inputs() -> pd.DataFrame:
    if not OUT_FILE.is_file():
        pytest.skip("forecast_inputs.parquet absent: run scripts/build_processed.py")
    return load_forecast_inputs()


def test_processed_table_has_only_allowed_columns(inputs):
    allowed = {"ts_utc", "ts_cst", "date_cst", "hour_cst", *F.EXOG_SERIES, *F.EXOG_SOURCE,
               "om_valid_max_utc"}
    assert set(inputs.columns) == allowed
    assert not any("previous_day0" in c or "previous_day1" in c for c in inputs.columns)


def test_every_ercot_posting_is_before_its_days_issue_time(inputs):
    issue = pd.to_datetime(inputs["date_cst"]).dt.tz_localize("UTC") + pd.Timedelta(hours=6)
    for report in ("wind", "solar"):
        posted = inputs[f"ercot_{report}_posted_utc"]
        has = inputs[next(iter(ERCOT_COLS[report]))].notna()
        assert (has == posted.notna()).all()
        assert (posted[has] < issue[has]).all()
        assert (posted[has] >= issue[has] - pd.Timedelta(hours=6)).all()
        # one posting per day
        assert (inputs[has].groupby("date_cst")[f"ercot_{report}_posted_utc"].nunique() == 1).all()


def test_every_open_meteo_run_was_initialized_12h_before_issue_time(inputs):
    issue = pd.to_datetime(inputs["date_cst"]).dt.tz_localize("UTC") + pd.Timedelta(hours=6)
    slot_end = inputs["ts_utc"] + pd.Timedelta(hours=1)
    assert (inputs["om_valid_max_utc"] == slot_end).all()
    assert (inputs["om_run_init_max_utc"] == inputs["om_valid_max_utc"] - OM_LEAD).all()
    allowance = pd.Timedelta(hours=F.OM_PUBLICATION_ALLOWANCE_H)
    assert (inputs["om_run_init_max_utc"] + allowance < issue).all()
    assert (inputs["om_run_init_max_utc"] <= issue - pd.Timedelta(hours=24)).all()


# --- (d) feature level -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def grid(inputs) -> F.Grid:
    return F.add_forecast_inputs(F.make_grid(load_eia930()), inputs)


@pytest.mark.parametrize("as_of_hour", [F.AS_OF_HOUR, F.REJECTED_AS_OF_HOUR])
def test_new_feature_sources_before_as_of_every_train_and_test_day(grid, as_of_hour):
    days = np.arange(F.MIN_HISTORY_DAYS, grid.day_index("2025-01-01"))
    for target in F.TARGETS:
        X, S = F.build_features(grid, target, days, as_of_hour)
        assert F.leakage_violations(X, S, as_of_hour) == []
        a = F.as_of_index(X["day"].to_numpy(), as_of_hour)
        for name in F.EXOG_FEATURES:
            v, s = X[name].to_numpy(), S[name].to_numpy()
            assert (np.isfinite(v) <= np.isfinite(s)).all(), name  # every value has a source
            assert (s[np.isfinite(s)] < a[np.isfinite(s)]).all(), name


def test_new_features_present_on_test_days(grid):
    days = np.arange(grid.day_index("2024-10-01"), grid.day_index("2025-01-01"))
    for target in F.TARGETS:
        X, _ = F.build_features(grid, target, days)
        assert X[list(F.EXOG_BY_TARGET[target])].notna().all().all(), target
        assert X["ercot_load_fc"].notna().all()
        # DAM: every slot, except CST slot 23 on daylight-time days (D+1's DAM run, after as_of)
        nan = X["dam_price"].isna().to_numpy()
        assert (X.loc[nan, "hour_slot"] == 23).all() and nan.sum() <= 33  # Oct 1 .. Nov 2


def test_load_forecast_and_dam_masked_under_earlier_issue_time(grid):
    """At 10:00 CST on D-1 neither the 23:30 load forecast nor the ~12:30 DAM file exists yet."""
    d = grid.day_index("2024-10-15")
    X, _ = F.build_features(grid, "demand", np.array([d]), F.REJECTED_AS_OF_HOUR)
    assert X[["ercot_load_fc", "dam_price", "dam_price_rank"]].isna().all().all()
    np.testing.assert_array_equal(X["demand_base_ercot"], X["same_hour_latest"])


def test_ercot_inputs_masked_under_earlier_issue_time(grid):
    """At 10:00 CST on D-1 the 23:55 posting does not exist yet: masked, not read."""
    d = grid.day_index("2024-10-15")
    X, _ = F.build_features(grid, "wind", np.array([d]), F.REJECTED_AS_OF_HOUR)
    assert X["ercot_stwpf"].isna().all()
    np.testing.assert_array_equal(X["wind_base_ercot"], X["mean24h"])  # falls back to M4 base


def _copy(grid: F.Grid) -> F.Grid:
    return F.Grid(first_day=grid.first_day, dates=grid.dates,
                  series={k: v.copy() for k, v in grid.series.items()})


def test_values_published_at_or_after_as_of_are_masked(grid):
    d = grid.day_index("2024-11-04")
    g = _copy(grid)
    sl = slice(d * 24, (d + 1) * 24)
    for name in [k for k in g.series if k.startswith("src_")]:
        g.series[name][sl] = F.as_of_index(d)  # pretend D's values were published at as_of
    for target in F.TARGETS:
        X, _ = F.build_features(g, target, np.array([d]))
        cols = [c for c in F.EXOG_FEATURES if not c.endswith("_base_ercot")
                and c != "om_temp_prev"]
        assert X[cols].isna().all().all(), target
        assert X["om_temp_prev"].notna().all()  # D-1's values were published earlier


def test_later_days_inputs_never_read(grid):
    for date in ("2024-03-29", "2024-08-20", "2024-11-04", "2024-12-31"):
        d = grid.day_index(date)
        g = _copy(grid)
        rng = np.random.default_rng(d)
        cut = (d + 1) * 24  # every slot after day D
        for name in (*F.EXOG_SERIES, *[k for k in g.series if k.startswith("src_")]):
            g.series[name][cut:] = rng.uniform(-1e6, 1e6, len(g.series[name]) - cut)
        for target in F.TARGETS:
            X0, _ = F.build_features(grid, target, np.array([d]))
            X1, _ = F.build_features(g, target, np.array([d]))
            pd.testing.assert_frame_equal(X0, X1)


def test_spring_forward_day_both_hour_labelings():
    """2024-03-10: NP4-732 labels 01:00-02:00 CST as HE2, NP4-737 as HE3; both map to slot 1."""
    day = "2024-03-10"
    for skipped in (3, 2):
        hes = [h for h in range(1, 25) if h != skipped]
        rows = [{"target_day_cst": day, "postedDatetime": "2024-03-09T23:55:00",
                 "deliveryDate": day, "hourEnding": he, "DSTFlag": False,
                 "STWPFSystemWide": float(he), "WGRPPSystemWide": 0.0,
                 "COPHSLSystemWide": np.nan if he == 3 else 0.0} for he in hes]
        rows.append({**rows[0], "deliveryDate": "2024-03-11", "hourEnding": 1,
                     "STWPFSystemWide": 99.0})
        out = ercot_point_in_time(pd.DataFrame(rows), WIND, "posted")
        assert len(out) == 24, skipped
        expected = pd.date_range("2024-03-10 06:00", periods=24, freq="h", tz="UTC")
        assert (out["ts_utc"].to_numpy() == expected.to_numpy()).all()
        assert out["ercot_stwpf"].iloc[1] == (2.0 if skipped == 3 else 3.0)
        assert out["ercot_stwpf"].iloc[-1] == 99.0


# --- M12: ERCOT load forecast and DAM prices ----------------------------------------------------


def _load_posting(target_day: str, posted_local: str, value: float, model: str = "E1",
                  n_hours: int = 24) -> pd.DataFrame:
    rows = []
    for h in range(n_hours):  # CST day in standard time: slot h = local hour ending h+1
        rows.append({"target_day_cst": target_day, "postedDatetime": posted_local,
                     "deliveryDate": target_day, "hourEnding": f"{h + 1}:00", "DSTFlag": False,
                     "systemTotal": value + h, "model": model, "inUseFlag": True})
    return pd.DataFrame(rows)


def test_load_forecast_latest_in_use_posting_before_issue_time():
    day = "2024-12-02"
    base = pd.concat([_load_posting(day, "2024-12-01T22:30:01", 40_000.0),
                      _load_posting(day, "2024-12-01T23:30:01", 41_000.0, model="E2")])
    a = ercot_load_point_in_time(base)
    assert len(a) == 24 and (a["ercot_load_fc"].to_numpy() == 41_000.0 + np.arange(24)).all()
    assert (a["ercot_load_posted_utc"] == pd.Timestamp("2024-12-02 05:30:01", tz="UTC")).all()
    later = pd.concat([base, _load_posting(day, "2024-12-02T00:00:00", -1.0),
                       _load_posting(day, "2024-12-02T00:30:01", 9e9)])
    pd.testing.assert_frame_equal(a, ercot_load_point_in_time(later))


def test_load_forecast_rejects_two_in_use_models_in_one_posting():
    day = "2024-12-02"
    p = pd.concat([_load_posting(day, "2024-12-01T23:30:01", 1.0, model="E1"),
                   _load_posting(day, "2024-12-01T23:30:01", 2.0, model="E2")])
    with pytest.raises(ValueError, match="in-use"):
        ercot_load_point_in_time(p)


def _dam_file(op_day: str, posted_local: str, price: float, point: str = "HB_HUBAVG",
              doc: int = 1) -> pd.DataFrame:
    d = pd.Timestamp(op_day)
    return pd.DataFrame({"DeliveryDate": d.strftime("%m/%d/%Y"),
                         "HourEnding": [f"{h:02d}:00" for h in range(1, 25)],
                         "SettlementPoint": point, "SettlementPointPrice": price + np.arange(24),
                         "DSTFlag": "N", "postDatetime": posted_local, "docId": doc})


def test_dam_uses_original_file_posted_before_issue_time():
    """Standard time: D's 24 CST slots all come from the file posted ~12:30 on D-1; a later
    correction for D (posted after the issue time) and other settlement points are ignored."""
    files = pd.concat([_dam_file("2024-12-02", "2024-12-01T12:37:05.000", 30.0, doc=1),
                       _dam_file("2024-12-02", "2024-12-04T09:00:00.000", 999.0, doc=2),
                       _dam_file("2024-12-02", "2024-12-01T12:37:05.000", 5.0, "HB_WEST", 3)])
    out = dam_point_in_time(files)
    assert len(out) == 24
    np.testing.assert_array_equal(out["dam_price"], 30.0 + np.arange(24))
    assert (out["dam_posted_utc"] == pd.Timestamp("2024-12-01 18:37:05", tz="UTC")).all()
    assert out["ts_utc"].iloc[0] == pd.Timestamp("2024-12-02 06:00", tz="UTC")


def test_dam_daylight_time_last_slot_needs_next_days_run():
    """Daylight time: CST slot 23 of D is local hour 1 of D+1, whose DAM run posts on D after
    the issue time, so that slot has no price; it is never filled from the later file."""
    files = pd.concat([_dam_file("2024-07-01", "2024-06-30T12:30:00.000", 30.0, doc=1),
                       _dam_file("2024-07-02", "2024-07-01T12:30:00.000", 50.0, doc=2)])
    out = dam_point_in_time(files).set_index("ts_utc")
    day = pd.date_range("2024-07-01 06:00", periods=24, freq="h", tz="UTC")
    got = out.reindex(day)["dam_price"].to_numpy()
    np.testing.assert_array_equal(got[:23], 30.0 + np.arange(1, 24))  # local HE2..HE24 of D
    assert np.isnan(got[23])
    # the next CST day's slots do come from the D+1 file (posted before D+1's issue time):
    # its slot 0 is local hour ending 2 of D+1
    assert pd.Timestamp("2024-07-02 05:00", tz="UTC") not in out.index
    assert out.loc[pd.Timestamp("2024-07-02 06:00", tz="UTC"), "dam_price"] == 51.0


def test_every_load_forecast_and_dam_posting_is_before_issue_time(inputs):
    issue = pd.to_datetime(inputs["date_cst"]).dt.tz_localize("UTC") + pd.Timedelta(hours=6)
    load = inputs["ercot_load_posted_utc"]
    has = inputs["ercot_load_fc"].notna()
    assert (has == load.notna()).all()
    assert (load[has] < issue[has]).all()
    assert (load[has] >= issue[has] - pd.Timedelta(hours=6)).all()
    assert (inputs[has].groupby("date_cst")["ercot_load_posted_utc"].nunique() == 1).all()
    dam = inputs["dam_posted_utc"]
    hd = inputs["dam_price"].notna()
    assert (hd == dam.notna()).all()
    assert (dam[hd] < issue[hd]).all()
