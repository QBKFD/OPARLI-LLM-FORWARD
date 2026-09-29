"""
Round 3 tests (round 2 tests adapted + two new).
    backend/venv/bin/python3 -m pytest research/vol_features_r3 -q
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_r3 as r2  # noqa: E402  (alias keeps the round-2 test bodies unchanged)
r1 = r2.r1


def _minutes(n, start="2019-06-03 00:00", seed=0, sigma=2e-4):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    close = 1300 * np.exp(np.cumsum(rng.normal(0, sigma, n)))
    open_ = np.r_[1300.0, close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * (1 + 1e-4),
                         "low": np.minimum(open_, close) * (1 - 1e-4), "close": close,
                         "volume": 1.0}, index=idx)


@pytest.fixture(scope="module")
def conf_slice():
    if not (r2.CACHE.exists() or (r2.ROOT / "database/ohlcv_data/XAUUSD.txt").exists()):
        pytest.skip("long-history data not available")
    return r2.load_conf(start=pd.Timestamp("2010-01-01", tz="UTC"),
                        end=pd.Timestamp("2010-05-01", tz="UTC"))


# ---------------------------------------------------------------- seasonal profile

def test_profile_uses_only_training_rows():
    m = _minutes(20 * 1440)
    r, _ = r1.minute_returns(m)
    b = r2.minute_buckets(m.index)
    t0, t1 = m.index[1440], m.index[13 * 1440]    # 12 days -> 60 returns per bucket
    base = r2.fit_profile(m.index, r, b, t0, t1)
    assert np.isfinite(base).all()
    for i in (5, 15 * 1440, 19 * 1440):            # before t0 and after t1
        r_mod = r.copy()
        r_mod.iloc[i] = 0.5
        np.testing.assert_array_equal(r2.fit_profile(m.index, r_mod, b, t0, t1), base)
    r_mod = r.copy()
    r_mod.iloc[5 * 1440] = 0.5                     # inside the window: must change
    assert not np.array_equal(r2.fit_profile(m.index, r_mod, b, t0, t1), base)


def test_profile_floor_and_sparse_buckets():
    m = _minutes(12 * 1440)
    r, _ = r1.minute_returns(m)
    with pytest.raises(ValueError):                 # 2 days: every bucket too sparse
        r2.fit_profile(m.index, r, r2.minute_buckets(m.index), m.index[0], m.index[2 * 1440])
    b = r2.minute_buckets(m.index)
    prof = r2.fit_profile(m.index, r, b, m.index[0], m.index[-1])
    assert prof.shape == (576,) and np.isfinite(prof).all() and (prof > 0).all()
    ok = (m.index < m.index[-1]) & r.notna().to_numpy()
    n = np.bincount(b[ok], minlength=576)
    raw = np.bincount(b[ok], weights=r.to_numpy()[ok] ** 2, minlength=576) / np.maximum(n, 1)
    raw = np.where(n >= 50, raw, np.nan)
    raw = np.where(np.isnan(raw), np.nanmedian(raw), raw)       # sparse buckets -> median
    floor = np.quantile(raw, 0.01)
    np.testing.assert_allclose(prof, np.maximum(raw, floor), rtol=1e-12)
    assert prof.min() == pytest.approx(floor) and (raw < floor).any()


# ---------------------------------------------------------------- truncation

def test_all_features_do_not_look_ahead(conf_slice):
    m = conf_slice
    r, _ = r1.minute_returns(m)
    prof = r2.fit_profile(m.index, r, r2.minute_buckets(m.index), m.index[0],
                          pd.Timestamp("2010-02-01", tz="UTC"))
    end = pd.Timestamp("2010-05-01", tz="UTC")
    full = r2.all_features(m, prof, end)
    cols = r2.M1 + r2.C3 + ["m1", "m24", "m120"] + r2.HOD
    eligible = full.index[full[cols].notna().all(axis=1)]
    rng = np.random.default_rng(12345)
    for t in pd.DatetimeIndex(rng.choice(eligible, 20, replace=False)).sort_values():
        trunc = r2.all_features(m[m.index < t], prof, end)
        assert trunc.index[-1] == t
        np.testing.assert_allclose(full.loc[t, cols].to_numpy(float),
                                   trunc.loc[t, cols].to_numpy(float),
                                   rtol=1e-9, atol=1e-12, err_msg=str(t))


# ---------------------------------------------------------------- targets

def test_y4_ds_uses_only_next_240_minutes():
    m = _minutes(12 * 1440)
    r, flag = r1.minute_returns(m)
    b = r2.minute_buckets(m.index)
    prof = r2.fit_profile(m.index, r, b, m.index[0], m.index[-1])
    bars = r1.hourly_bars(m, flag)
    end = m.index[-1] + pd.Timedelta(days=1)
    ds = r2.ds_features(bars.index, m.index, r, b, prof, end)
    t = pd.Timestamp("2019-06-03 06:00", tz="UTC")
    realised = m.index + pd.Timedelta(minutes=1)
    mask = (realised > t) & (realised <= t + pd.Timedelta(minutes=240))
    x = r.to_numpy()[mask] ** 2 / prof[b[mask]]
    manual = np.nanmean(x)                          # round 3: mean over available minutes
    assert ds.loc[t, "y4_ds"] == pytest.approx(math.log(manual), rel=1e-12)
    for minute, changes in [(t, True), (t - pd.Timedelta(minutes=1), False),
                            (t + pd.Timedelta(minutes=239), True),
                            (t + pd.Timedelta(minutes=240), False)]:
        m2 = m.copy()
        i = m2.index.get_loc(minute)
        m2.iloc[i:, m2.columns.get_loc("close")] *= 1.01
        r_2, f_2 = r1.minute_returns(m2)
        y2 = r2.ds_features(r1.hourly_bars(m2, f_2).index, m2.index, r_2, b, prof, end).loc[t, "y4_ds"]
        assert (abs(y2 - ds.loc[t, "y4_ds"]) > 1e-6) == changes, minute


# ---------------------------------------------------------------- reparametrisation

def test_LST_equals_raw_log_rv_har(conf_slice):
    m = conf_slice
    r, _ = r1.minute_returns(m)
    prof = r2.fit_profile(m.index, r, r2.minute_buckets(m.index), m.index[0], m.index[-1])
    F = r2.all_features(m, prof, pd.Timestamp("2010-05-01", tz="UTC")).dropna(
        subset=["L", "S", "T", "m1", "m24", "m120", "y4_ds"])
    tr, te = F.iloc[: len(F) // 2], F.iloc[len(F) // 2:]
    a, *_ = r1.ols_fit_predict(tr, te, ["L", "S", "T"], "y4_ds")
    b, *_ = r1.ols_fit_predict(tr, te, ["m1", "m24", "m120"], "y4_ds")
    np.testing.assert_allclose(a, b, atol=1e-8)


# ---------------------------------------------------------------- gaps

def test_gap_returns_excluded_from_ds_features_and_targets():
    m = _minutes(8 * 1440)
    hole = (m.index >= "2019-06-03 10:00") & (m.index < "2019-06-03 10:30")
    m_gap = m[~hole].copy()
    m_jump = m_gap.copy()
    after = m_jump.index >= "2019-06-03 10:30"
    m_jump.loc[after, ["open", "high", "low", "close"]] *= 1.05   # 5 % jump across the gap
    outs = []
    for mm in (m_gap, m_jump):
        r, flag = r1.minute_returns(mm)
        assert np.isnan(r.loc["2019-06-03 10:30"])
        b = r2.minute_buckets(mm.index)
        prof = np.full(576, 4e-8)
        bars = r1.hourly_bars(mm, flag)
        outs.append(r2.ds_features(bars.index, mm.index, r, b, prof, mm.index[-1] + pd.Timedelta(days=1)))
    # the log-price jump across the gap never enters any sum
    t = pd.Timestamp("2019-06-03 09:00", tz="UTC")
    for c in ["m1", "m24", "L", "S", "y24_ds"]:
        a, bb = outs[0][c], outs[1][c]
        np.testing.assert_allclose(a.to_numpy(float), bb.to_numpy(float), rtol=1e-9, atol=1e-10, equal_nan=True)
    assert np.isnan(outs[0].loc[t, "y4_ds"])       # 30 missing minutes -> 87.5 % coverage


# ---------------------------------------------------------------- sealing

def test_sealed_assert_fires_on_2024_row():
    bad = pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2024-01-02"], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        r2.assert_sealed(bad)
    late = pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2021-01-02"], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        r2.assert_sealed(late, end=r2.CONF_END)
    r2.assert_sealed(pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2020-12-31"], tz="UTC")),
                     end=r2.CONF_END)


def test_conf_loader_respects_seal():
    if not r2.CACHE.exists():
        pytest.skip("cache not built")
    m = r2.load_conf(start=pd.Timestamp("2020-12-01", tz="UTC"))
    assert m.index.max() < r2.CONF_END


# ---------------------------------------------------------------- HAC

def test_hac_t_close_to_ols_t_for_iid_data():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"a": rng.normal(size=4000)})
    df["y"] = 0.1 * df["a"] + rng.normal(size=4000)
    t, _ = r2.hac_tstats(df, ["a"], "y", 8)
    ols_t = 0.1 * math.sqrt(4000)                   # approx beta / (sigma / sqrt(n sd²))
    assert abs(t["a"] - ols_t) / ols_t < 0.35


# ---------------------------------------------------------------- round 3: means

def test_mrv_with_20_minute_gap_equals_mean_of_remaining_minutes():
    m = _minutes(12 * 1440)
    hole = (m.index >= "2019-06-05 10:20") & (m.index < "2019-06-05 10:40")
    m = m[~hole]
    r, flag = r1.minute_returns(m)
    b = r2.minute_buckets(m.index)
    prof = r2.fit_profile(m.index, r, b, m.index[0], m.index[-1])
    bars = r1.hourly_bars(m, flag)
    ds = r2.ds_features(bars.index, m.index, r, b, prof, m.index[-1] + pd.Timedelta(days=1))
    t = pd.Timestamp("2019-06-05 11:00", tz="UTC")           # bar holding the gap
    inbar = (m.index >= t - pd.Timedelta(hours=1)) & (m.index < t)
    x = r.to_numpy()[inbar] ** 2 / prof[b[inbar]]
    assert np.isnan(x).sum() == 1                            # the return across the gap is dropped
    assert (~np.isnan(x)).sum() == 39                        # 40 minutes left, one dropped
    assert ds.loc[t, "m1"] == pytest.approx(math.log(np.nanmean(x)), rel=1e-12)


def test_constant_variance_with_varying_minute_counts_gives_S_zero():
    # Every observed return has |r| = sigma, and bars keep between 32 and 60 minutes.
    rng = np.random.default_rng(12345)
    idx = pd.date_range("2019-06-03 00:00", periods=8 * 1440, freq="1min", tz="UTC")
    keep = np.ones(len(idx), bool)
    for h0 in range(0, len(idx), 60):
        drop = rng.integers(0, 29)                           # drop up to 28 minutes per bar
        pos = h0 + np.sort(rng.choice(np.arange(1, 60), size=drop, replace=False))
        keep[pos[pos < len(idx)]] = False
    idx = idx[keep]
    sigma = 2e-4
    logp = np.log(1500.0) + sigma * (np.arange(len(idx)) % 2)   # alternating +-sigma steps
    close = np.exp(logp)
    m = pd.DataFrame({"open": close, "high": close * (1 + 1e-5), "low": close * (1 - 1e-5),
                      "close": close, "volume": 1.0}, index=idx)
    r, flag = r1.minute_returns(m)
    bars = r1.hourly_bars(m, flag)
    counts = bars["n_min"]
    assert counts.nunique() > 10 and counts.min() >= 32      # minute counts really vary
    prof = np.full(576, 1.0)
    ds = r2.ds_features(bars.index, m.index, r, r2.minute_buckets(m.index), prof,
                        m.index[-1] + pd.Timedelta(days=1))
    S = ds["S"].dropna()
    assert len(S) > 100
    np.testing.assert_allclose(S.to_numpy(), 0.0, atol=1e-12)
    np.testing.assert_allclose(ds["T"].dropna().to_numpy(), 0.0, atol=1e-12)
