"""
Tests for the volatility feature study.

    backend/venv/bin/python3 -m pytest research/vol_features -q
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_study as rs  # noqa: E402


def _bars(o, h, l, c, start="2021-03-01 01:00"):
    idx = pd.date_range(start, periods=len(o), freq="1h", tz="UTC")
    b = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx, dtype=float)
    b["r1h"] = np.log(b["close"]).diff()
    return b


def _minutes(n, start="2021-03-01 00:00", seed=0, sigma=2e-4):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    close = 1800 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    open_ = np.r_[1800.0, close[:-1]]
    high = np.maximum(open_, close) * (1 + 1e-4)
    low = np.minimum(open_, close) * (1 - 1e-4)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": 1.0}, index=idx)


# ---------------------------------------------------------------- GK formula

def test_gk_matches_hand_computed_bar():
    # O=100 H=102 L=99 C=101, by hand:
    #   ln(102/99) = 0.0298530, squared /2      = 0.00044560
    #   ln(101/100) = 0.0099503, squared * 0.38629 = 0.00003825
    #   sigma2_GK = 0.00044560 - 0.00003825     = 0.00040735
    b = _bars([100.0] * 60, [102.0] * 60, [99.0] * 60, [101.0] * 60)
    f = rs.new_features(b)
    assert math.exp(f["log_gk60"].iloc[-1]) == pytest.approx(0.000407353, rel=1e-5)
    assert f["log_gk60"].iloc[:59].isna().all()      # needs all 60 bars


def test_gk_clipped_at_floor():
    # A zero-range bar gives sigma2_GK = 0, which is clipped to 1e-12 before the mean.
    b = _bars([100.0] * 60, [100.0] * 60, [100.0] * 60, [100.0] * 60)
    f = rs.new_features(b)
    assert math.exp(f["log_gk60"].iloc[-1]) == pytest.approx(1e-12)


# ---------------------------------------------------------------- wick60

def test_wick60_straight_line_is_zero():
    # Each bar opens at its low and closes at its high: no wicks.
    o = 100 + np.arange(80.0)
    c = o + 1.0
    f = rs.new_features(_bars(o, c, o, c))
    assert f["wick60"].iloc[59:].to_numpy() == pytest.approx(0.0, abs=1e-15)
    assert f["wick60"].iloc[:59].isna().all()


def test_wick60_close_at_open_is_one():
    o = np.full(80, 100.0)
    f = rs.new_features(_bars(o, o + 1.5, o - 0.5, o))
    assert f["wick60"].iloc[59:].to_numpy() == pytest.approx(1.0)


def test_wick60_zero_range_is_nan():
    o = np.full(70, 100.0)
    f = rs.new_features(_bars(o, o, o, o))
    assert f["wick60"].isna().all()


# ---------------------------------------------------------------- gap handling

def test_minute_and_hourly_gap_exclusion():
    m = _minutes(600)
    m = m.drop(m.index[200:210])        # a 10-minute hole -> flagged
    m = m.drop(m.index[300:303])        # a 3-minute hole -> kept (<= 5 min)
    r, flag = rs.minute_returns(m)
    assert flag.iloc[0] and flag.sum() == 2
    assert np.isnan(r.iloc[200]) and not np.isnan(r.iloc[300])   # 4-min step kept
    bars = rs.hourly_bars(m, flag)
    hole_bar = bars.index[bars.index > m.index[199]][0]  # the bar containing the hole
    assert bars.loc[hole_bar, "has_gap"] == 1 and np.isnan(bars.loc[hole_bar, "r1h"])
    assert bars["r1h"].notna().sum() == len(bars) - 2    # first bar + hole bar


# ---------------------------------------------------------------- truncation

@pytest.fixture(scope="module")
def real_minutes():
    if not rs.DATA.exists():
        pytest.skip("parquet not available")
    m, _ = rs.load_minutes(start=pd.Timestamp("2021-01-01", tz="UTC"),
                           end=pd.Timestamp("2021-05-01", tz="UTC"))
    return m


def test_features_do_not_look_ahead(real_minutes):
    m = real_minutes
    _, full, _ = rs.build_features(m)
    cols = rs.NEW + rs.EXISTING + rs.HAR
    eligible = full.index[full[cols].notna().all(axis=1)]
    rng = np.random.default_rng(12345)
    picks = rng.choice(eligible, size=20, replace=False)
    for t in pd.DatetimeIndex(picks).sort_values():
        _, trunc, _ = rs.build_features(m[m.index < t])   # bar t is complete
        assert trunc.index[-1] == t
        a, b = full.loc[t, cols].to_numpy(float), trunc.loc[t, cols].to_numpy(float)
        np.testing.assert_allclose(a, b, rtol=1e-9, atol=1e-12, err_msg=str(t))


def test_existing_block_equals_repo_compute(real_minutes):
    """The imported volatility block is identical to RegimeFeatureEngine.compute()."""
    r, flag = rs.minute_returns(real_minutes)
    bars = rs.hourly_bars(real_minutes, flag).iloc[:900]
    rf, rc = rs._import_feature_engine()
    ref = rf.RegimeFeatureEngine(rc.RegimeConfig()).compute(bars[["open", "high", "low", "close"]])
    mine = rs.existing_features(bars)
    pd.testing.assert_frame_equal(mine, ref[rs.EXISTING], check_exact=True)


# ---------------------------------------------------------------- targets

def test_y4_uses_only_returns_in_next_240_minutes():
    m = _minutes(24 * 60)
    r, flag = rs.minute_returns(m)
    bars = rs.hourly_bars(m, flag)
    tg = rs.targets(bars, m.index, r)
    t = pd.Timestamp("2021-03-01 06:00", tz="UTC")
    realised_at = m.index + pd.Timedelta(minutes=1)      # return i is known at open_i + 1 min
    mask = (realised_at > t) & (realised_at <= t + pd.Timedelta(minutes=240))
    assert mask.sum() == 240
    assert tg.loc[t, "y4"] == pytest.approx(math.log(np.nansum(r[mask] ** 2)), rel=1e-12)

    # A shock in the first return after t moves y4(t); one realised exactly at t does not.
    for minute, should_change in [(t, True), (t - pd.Timedelta(minutes=1), False),
                                  (t + pd.Timedelta(minutes=239), True),
                                  (t + pd.Timedelta(minutes=240), False)]:
        m2 = m.copy()
        i = m2.index.get_loc(minute)
        m2.iloc[i:, m2.columns.get_loc("close")] *= 1.01
        r2, f2 = rs.minute_returns(m2)
        y2 = rs.targets(rs.hourly_bars(m2, f2), m2.index, r2).loc[t, "y4"]
        assert (abs(y2 - tg.loc[t, "y4"]) > 1e-6) == should_change, minute


def test_target_nan_when_window_uncovered():
    m = _minutes(24 * 60)
    m = m.drop(m.index[(m.index >= "2021-03-01 08:00") & (m.index < "2021-03-01 09:00")])
    r, flag = rs.minute_returns(m)
    tg = rs.targets(rs.hourly_bars(m, flag), m.index, r)
    assert np.isnan(tg.loc[pd.Timestamp("2021-03-01 06:00", tz="UTC"), "y4"])   # 75 % coverage
    assert not np.isnan(tg.loc[pd.Timestamp("2021-03-01 10:00", tz="UTC"), "y4"])


# ---------------------------------------------------------------- sealing

def test_sealed_assert_triggers_on_2024_row():
    ok = pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2023-12-31 23:00"], tz="UTC"))
    rs.assert_sealed(ok)
    bad = pd.DataFrame({"x": [1.0, 2.0]},
                       index=pd.DatetimeIndex(["2023-12-31 23:00", "2024-01-02 00:00"], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        rs.assert_sealed(bad)


def test_loader_never_returns_sealed_rows():
    if not rs.DATA.exists():
        pytest.skip("parquet not available")
    m, _ = rs.load_minutes(start=pd.Timestamp("2023-12-20", tz="UTC"))
    assert m.index.max() < rs.SEAL


# ---------------------------------------------------------------- inference

def test_dm_and_newey_west():
    rng = np.random.default_rng(1)
    d = rng.normal(0, 1, 5000)
    assert rs.newey_west_lrv(d, 0) == pytest.approx(d.var())
    a, b = d ** 2, d ** 2 + 0.5                       # A is better by 0.5 every row
    stat, p = rs.dm_test(a + rng.normal(0, 0.1, 5000), b, 8)
    assert stat < -10 and p < 1e-6
    stat, p = rs.dm_test(b, a, 8)
    assert p > 0.999


def test_ols_recovers_coefficients():
    rng = np.random.default_rng(2)
    X = pd.DataFrame(rng.normal(size=(2000, 2)), columns=["a", "b"])
    X["y"] = 1.0 + 2.0 * X["a"] - 0.5 * X["b"]
    yhat, beta, s2 = rs.ols_fit_predict(X, X, ["a", "b"], "y")
    np.testing.assert_allclose(yhat, X["y"], atol=1e-10)
    assert s2 == pytest.approx(0.0, abs=1e-20)
