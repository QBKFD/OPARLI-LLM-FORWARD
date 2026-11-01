"""research/variance_ratio: the estimator and the return grid, on synthetic data only."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vr  # noqa: E402
import vr_params as V  # noqa: E402


def _cell(n, k, rng, frac_invalid=0.05, mask_every=None):
    r = rng.standard_normal(n) * (1 + 0.8 * np.sin(np.arange(n) * 2 * np.pi / 24) ** 2)
    r[rng.random(n) < frac_invalid] = np.nan
    ok = vr.window_starts(r, k)
    mask = np.ones(n, bool) if mask_every is None else (np.arange(n) % mask_every == 3)
    mu = np.nanmean(r)
    return r, np.where(np.isfinite(r), r - mu, 0.0), ok & mask, mu


@pytest.mark.parametrize("k, every", [(2, None), (4, 24), (8, None), (24, 24)])
def test_vr_equals_the_definition(k, every):
    r, e, starts, mu = _cell(3000, k, np.random.default_rng(k), mask_every=every)
    assert vr.vr_cell(e, starts, k)["vr"] == pytest.approx(vr.vr_direct(r, starts, k, mu), rel=1e-10)
    assert vr.vr_cell(e, starts, k)["n"] == starts.sum()


def test_contiguous_full_sample_z_is_lo_mackinlay():
    rng = np.random.default_rng(1)
    k, n = 6, 5000
    r = rng.standard_normal(n) * np.exp(0.5 * np.sin(np.arange(n) / 50))
    mu = r.mean()
    e = r - mu
    starts = vr.window_starts(r, k)
    got = vr.vr_cell(e, starts, k)
    # Lo-MacKinlay z*: delta_j and theta, with this sample's edge counts
    s2 = (e ** 2).sum()
    theta = sum((2 * (k - j) / k) ** 2 * (e[j:] ** 2 * e[:-j] ** 2).sum() / s2 ** 2 for j in range(1, k))
    vr_lm = 1 + 2 * sum((k - j) / k * (e[j:] * e[:-j]).sum() / s2 for j in range(1, k))
    assert got["vr"] == pytest.approx(vr_lm, abs=5e-3)                        # edges differ by O(k/n)
    assert got["z"] == pytest.approx((vr_lm - 1) / np.sqrt(theta), abs=0.05)


@pytest.mark.parametrize("every", [None, 24])
def test_z_has_its_size_under_a_heteroskedastic_random_walk(every):
    rng = np.random.default_rng(7)
    rej = 0
    reps = 300
    for _ in range(reps):
        _, e, starts, _ = _cell(2400, 4, rng, mask_every=every)
        rej += vr.vr_cell(e, starts, 4)["p"] < 0.05
    assert 0.02 <= rej / reps <= 0.09


def test_positive_autocorrelation_gives_vr_above_one():
    rng = np.random.default_rng(3)
    x = np.zeros(20000)
    for t in range(1, len(x)):
        x[t] = 0.3 * x[t - 1] + rng.standard_normal()
    res = vr.vr_cell(x - x.mean(), vr.window_starts(x, 2), 2)
    assert res["vr"] == pytest.approx(1.3, abs=0.03) and res["z"] > 10


def test_too_few_windows_give_nan():
    assert np.isnan(vr.vr_cell(np.zeros(10), np.zeros(10, bool), 4)["vr"])


def test_bh():
    got = vr.bh(np.array([0.01, 0.04, 0.03, np.nan, 0.5]))
    assert np.isnan(got[3])
    np.testing.assert_allclose(got[[0, 1, 2, 4]], [0.04, 0.04 * 4 / 3, 0.04 * 4 / 3, 0.5])


def test_sessions_cover_every_hour_once():
    s = vr.session_of(np.arange(24), V.SESSIONS_UTC)
    assert (s != "").all()
    assert list(s[[22, 23, 0, 6, 7, 11, 12, 15, 16, 21]]) == ["Asia"] * 4 + ["London"] * 2 + ["Overlap"] * 2 + \
        ["NewYork"] * 2


# --------------------------------------------------------------------------- the return grid

def _bars(start, end, drop=()):
    idx = pd.date_range(start, end, freq="1min", inclusive="left", tz="UTC")
    keep = np.ones(len(idx), bool)
    for a, b in drop:
        keep &= ~((idx >= pd.Timestamp(a, tz="UTC")) & (idx < pd.Timestamp(b, tz="UTC")))
    idx = idx[keep]
    c = 100 * np.exp(np.cumsum(np.random.default_rng(0).standard_normal(len(idx)) * 1e-4))
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c}, index=idx)


def test_slot_returns_exclusions():
    # Mon 2019-03-04 .. Mon 2019-03-11 (US DST from 03-10): FX-like week, weekend closed
    s, e = "2019-03-03 22:00", "2019-03-11 22:00"
    m = _bars(s, e, drop=[("2019-03-08 22:00", "2019-03-10 21:00"),          # weekend (Fri 17:00 NY .. Sun 17:00 NY)
                          ("2019-03-05 10:20", "2019-03-05 10:40"),          # a 20-min hole
                          ("2019-03-06 22:00", "2019-03-06 23:00"),          # a daily break hour (17:00 NY)
                          ("2019-03-07 12:00", "2019-03-07 21:00")])         # a short day (Thu 03-07 trade date)
    slots, r, counts = vr.slot_returns(m, 60, 15, 15, 1000, 17, None, pd.Timestamp(s, tz="UTC"),
                                       pd.Timestamp(e, tz="UTC"))
    t = slots.get_loc(pd.Timestamp("2019-03-04 15:00", tz="UTC"))
    c = m["close"]
    want = np.log(c[pd.Timestamp("2019-03-04 15:59", tz="UTC")] / c[pd.Timestamp("2019-03-04 14:59", tz="UTC")])
    assert r[t] == pytest.approx(want)
    assert np.isnan(r[slots.get_loc(pd.Timestamp("2019-03-05 10:00", tz="UTC"))])     # the hole is inside
    assert counts["gap"] >= 1 and counts["weekend"] >= 40 and counts["daily_break"] >= 1
    assert counts["short_day"] >= 10 and counts["seam"] == 0
    assert counts.sum() == len(slots)
    assert counts["valid"] == np.isfinite(r).sum()


def test_slot_returns_seam_is_counted():
    s, e = "2026-01-30 20:00", "2026-02-02 04:00"
    m = _bars(s, e)
    _, r, counts = vr.slot_returns(m, 60, 15, 15, 0, 17, pd.Timestamp("2026-02-01", tz="UTC"),
                                   pd.Timestamp(s, tz="UTC"), pd.Timestamp(e, tz="UTC"))
    assert counts["seam"] == 1


def test_slot_returns_refuses_bars_outside_the_range():
    m = _bars("2019-03-04 00:00", "2019-03-04 03:00")
    with pytest.raises(AssertionError):
        vr.slot_returns(m, 60, 15, 15, 0, 17, None, pd.Timestamp("2019-03-04 01:00", tz="UTC"),
                        pd.Timestamp("2019-03-04 03:00", tz="UTC"))


def test_trade_date_rolls_at_17_new_york():
    t = pd.DatetimeIndex(["2019-03-04 21:59", "2019-03-04 22:00"], tz="UTC")       # 16:59 / 17:00 EST
    assert [str(d.date()) for d in vr.trade_dates(t, 17)] == ["2019-03-04", "2019-03-05"]
