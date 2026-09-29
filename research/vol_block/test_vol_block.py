"""
Tests for research/vol_block/vol_block.py.
    backend/venv/bin/python3 -m pytest research/vol_block -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "vol_features_r3"))
from vol_block import VolBlock  # noqa: E402
import run_r3 as r3  # noqa: E402

r1 = r3.r1
T0 = pd.Timestamp("2021-01-03 22:00", tz="UTC")     # first minute after a weekend gap
T1 = pd.Timestamp("2021-03-01 00:00", tz="UTC")


@pytest.fixture(scope="module")
def minutes():
    if not r1.DATA.exists():
        pytest.skip("parquet not available")
    m, _ = r1.load_minutes(start=pd.Timestamp("2020-12-14", tz="UTC"),
                           end=pd.Timestamp("2021-06-01", tz="UTC"))
    return m


def test_matches_round3_outputs(minutes):
    m = minutes
    t0 = m.index[m.index >= T0][0]
    assert (t0 - m.index[m.index < t0][-1]) > pd.Timedelta(minutes=5)   # starts after a gap
    r, flag = r1.minute_returns(m)
    b = r3.minute_buckets(m.index)
    prof = r3.fit_profile(m.index, r, b, t0, T1)
    bars = r1.hourly_bars(m, flag)
    ref = r3.ds_features(bars.index, m.index, r, b, prof, m.index[-1] + pd.Timedelta(days=1))

    vb = VolBlock().fit(m[(m.index >= t0) & (m.index < T1)])
    np.testing.assert_allclose(vb.profile_, prof, rtol=0, atol=1e-18)
    got = vb.transform(m, bars.index)
    for c in ["L", "S", "T"]:
        np.testing.assert_allclose(got[c].to_numpy(float), ref[c].to_numpy(float),
                                   rtol=0, atol=1e-10, equal_nan=True, err_msg=c)
    assert got[["L", "S", "T"]].notna().sum().min() > 2000


def test_no_look_ahead(minutes):
    m = minutes
    vb = VolBlock().fit(m[(m.index >= T0) & (m.index < T1)])
    full = vb.transform(m)
    ok = full.index[full.notna().all(axis=1)]
    rng = np.random.default_rng(12345)
    for t in pd.DatetimeIndex(rng.choice(ok, 20, replace=False)).sort_values():
        trunc = vb.transform(m[m.index < t])
        assert trunc.index[-1] == t
        np.testing.assert_allclose(full.loc[t].to_numpy(float), trunc.loc[t].to_numpy(float),
                                   rtol=1e-12, atol=1e-12, err_msg=str(t))


def test_fit_uses_only_training_rows(minutes):
    m = minutes
    train = m[(m.index >= T0) & (m.index < T1)]
    base = VolBlock().fit(train)
    m2 = m.copy()
    later = m2.index >= T1 + pd.Timedelta(days=3)
    m2.loc[later, ["open", "high", "low", "close"]] *= np.exp(
        np.random.default_rng(1).normal(0, 0.01, (later.sum(), 1)))
    again = VolBlock().fit(m2[(m2.index >= T0) & (m2.index < T1)])
    np.testing.assert_array_equal(base.profile_, again.profile_)
    pd.testing.assert_series_equal(base.s_hour_mean_, again.s_hour_mean_)
    # the later change does move transform() output after it (sanity: the test has teeth)
    a, b = base.transform(m), base.transform(m2)
    assert not np.allclose(a.loc[a.index > T1 + pd.Timedelta(days=10), "L"].dropna(),
                           b.loc[b.index > T1 + pd.Timedelta(days=10), "L"].dropna())
    # and a change inside the training data moves the profile
    m3 = train.copy()
    m3.iloc[5000:, m3.columns.get_loc("close")] *= 1.02
    assert not np.array_equal(VolBlock().fit(m3).profile_, base.profile_)


def test_S_hd_hour_means_zero_on_training_window(minutes):
    m = minutes
    train = m[(m.index >= T0) & (m.index < T1)]
    vb = VolBlock().fit(train)
    f = vb.transform(train)
    f = f[f.index <= vb.train_end_]
    hm = f["S_hd"].groupby(f.index.hour).mean()
    assert len(hm) >= 20
    np.testing.assert_allclose(hm.to_numpy(), 0.0, atol=1e-12)
    raw = f["S"].groupby(f.index.hour).mean()
    assert raw.abs().max() > 0.05                     # S itself does have hour means


def test_input_validation():
    idx = pd.date_range("2021-01-04", periods=10, freq="1min")          # naive
    with pytest.raises(ValueError):
        VolBlock().fit(pd.DataFrame({"close": 1.0}, index=idx))
    with pytest.raises(RuntimeError):
        VolBlock().transform(pd.DataFrame({"close": [1.0]},
                                          index=pd.DatetimeIndex(["2021-01-04"], tz="UTC")))
