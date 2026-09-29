"""
Trend study tests.   backend/venv/bin/python3 -m pytest research/trend_features -q
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_trend as rt  # noqa: E402

r1, r3 = rt.r1, rt.r3


def _frame(close, start="2019-06-03 00:00"):
    idx = pd.date_range(start, periods=len(close), freq="1min", tz="UTC")
    close = np.asarray(close, float)
    return pd.DataFrame({"open": close, "high": close * (1 + 1e-5), "low": close * (1 - 1e-5),
                         "close": close, "volume": 1.0}, index=idx)


def test_Z_straight_line_is_sqrt_count_and_E_is_one():
    n = 3 * 1440
    m = _frame(1500 * np.exp(1e-5 * np.arange(n)))          # constant 1-min drift
    R = rt.five_min_returns(m)
    bars = r1.hourly_bars(m, r1.minute_returns(m)[1])
    Z = rt.z_features(bars.index, R)
    t = pd.Timestamp("2019-06-04 12:00", tz="UTC")
    assert Z.loc[t, "Z4"] == pytest.approx(math.sqrt(48), rel=1e-9)
    assert Z.loc[t, "Z24"] == pytest.approx(math.sqrt(288), rel=1e-9)
    E = rt.targets(bars.index, R, m.index[-1] + pd.Timedelta(days=1))
    assert E.loc[t, "E4"] == pytest.approx(1.0, abs=1e-12)
    assert E.loc[t, "E24"] == pytest.approx(1.0, abs=1e-12) or np.isnan(E.loc[t, "E24"])


def test_Z_alternating_returns_is_zero():
    n = 3 * 1440
    bucket = np.arange(n) // 5
    m = _frame(1500 * np.exp(1e-4 * (bucket % 2)))          # 5-min prices alternate
    R = rt.five_min_returns(m)
    assert set(np.round(np.abs(R.dropna().to_numpy()), 10)) == {1e-4}
    bars = r1.hourly_bars(m, r1.minute_returns(m)[1])
    Z = rt.z_features(bars.index, R).dropna(subset=["Z4", "Z24"])
    Z = Z[Z.index >= pd.Timestamp("2019-06-04 01:00", tz="UTC")]   # windows past the first bucket
    assert len(Z) > 20
    np.testing.assert_allclose(Z[["Z4", "Z24"]].to_numpy(), 0.0, atol=1e-9)


def test_E4_uses_only_the_next_240_minutes():
    rng = np.random.default_rng(12345)
    m = _frame(1500 * np.exp(np.cumsum(rng.normal(0, 2e-4, 2 * 1440))))
    R = rt.five_min_returns(m)
    bars = r1.hourly_bars(m, r1.minute_returns(m)[1])
    end = m.index[-1] + pd.Timedelta(days=1)
    t = pd.Timestamp("2019-06-03 08:00", tz="UTC")
    E = rt.targets(bars.index, R, end).loc[t, "E4"]
    w = R[(R.index >= t) & (R.index < t + pd.Timedelta(minutes=240))].dropna()
    assert len(w) == 48
    assert E == pytest.approx(abs(w.sum()) / w.abs().sum(), rel=1e-12)
    for minute, changes in [(t, True), (t - pd.Timedelta(minutes=1), False),
                            (t + pd.Timedelta(minutes=239), True), (t + pd.Timedelta(minutes=240), False)]:
        m2 = m.copy()
        m2.iloc[m2.index.get_loc(minute):, m2.columns.get_loc("close")] *= 1.01
        e2 = rt.targets(bars.index, rt.five_min_returns(m2), end).loc[t, "E4"]
        assert (abs(e2 - E) > 1e-9) == changes, minute


def test_gap_returns_excluded_consistently():
    rng = np.random.default_rng(7)
    base = 1500 * np.exp(np.cumsum(rng.normal(0, 2e-4, 3 * 1440)))
    m = _frame(base)
    hole = (m.index >= "2019-06-04 10:00") & (m.index < "2019-06-04 10:12")
    m_gap = m[~hole].copy()
    m_jump = m_gap.copy()
    m_jump.loc[m_jump.index >= "2019-06-04 10:12", ["open", "high", "low", "close"]] *= 1.05
    outs = []
    for mm in (m_gap, m_jump):
        R = rt.five_min_returns(mm)
        assert np.isnan(R.loc[pd.Timestamp("2019-06-04 10:10", tz="UTC")])   # bucket after the gap
        bars = r1.hourly_bars(mm, r1.minute_returns(mm)[1])
        outs.append(pd.concat([rt.z_features(bars.index, R),
                               rt.targets(bars.index, R, mm.index[-1] + pd.Timedelta(days=1))], axis=1))
    for c in ["Z4", "Z24", "E4", "E24"]:
        np.testing.assert_allclose(outs[0][c].to_numpy(float), outs[1][c].to_numpy(float),
                                   rtol=1e-9, atol=1e-12, equal_nan=True, err_msg=c)


@pytest.fixture(scope="module")
def conf_slice():
    if not (r3.CACHE.exists() or (r3.ROOT / "database/ohlcv_data/XAUUSD.txt").exists()):
        pytest.skip("long-history data not available")
    return r3.load_conf(start=pd.Timestamp("2012-01-01", tz="UTC"), end=pd.Timestamp("2012-05-01", tz="UTC"))


def test_all_features_no_look_ahead(conf_slice):
    m = conf_slice
    end = pd.Timestamp("2012-05-01", tz="UTC")
    vb = rt.VolBlock().fit(m[m.index < pd.Timestamp("2012-02-15", tz="UTC")])

    def feats(mm):
        bars, st = rt.static_frame(mm, end)
        return pd.concat([st, vb.transform(mm, bars.index)], axis=1)

    full = feats(m)
    cols = rt.M1P + rt.MT + ["Z24_signed"] + rt.MV + rt.HOD
    ok = full.index[full[cols].notna().all(axis=1)]
    rng = np.random.default_rng(12345)
    for t in pd.DatetimeIndex(rng.choice(ok, 20, replace=False)).sort_values():
        tr = feats(m[m.index < t])
        assert tr.index[-1] == t
        np.testing.assert_allclose(full.loc[t, cols].to_numpy(float), tr.loc[t, cols].to_numpy(float),
                                   rtol=1e-9, atol=1e-12, err_msg=str(t))


def test_existing_trend_block_equals_repo_compute(conf_slice):
    m = conf_slice
    bars = r1.hourly_bars(m, r1.minute_returns(m)[1]).iloc[:900]
    rf, rc = r1._import_feature_engine()
    ref = rf.RegimeFeatureEngine(rc.RegimeConfig()).compute(bars[["open", "high", "low", "close"]])
    mine = rt.existing_trend(bars)
    pd.testing.assert_frame_equal(mine[rt.M1S], ref[rt.M1S], check_exact=True)
    np.testing.assert_array_equal(mine["abs_di_bias"], ref["di_bias"].abs())


def test_sealed_assert_fires():
    bad = pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2024-01-02"], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        r3.assert_sealed(bad)
    with pytest.raises(AssertionError, match="SEALED"):
        r3.assert_sealed(pd.DataFrame({"x": [1.0]}, index=pd.DatetimeIndex(["2021-01-02"], tz="UTC")),
                         end=r3.CONF_END)
