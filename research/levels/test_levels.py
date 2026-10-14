"""
Level study tests.
    /Users/x/Desktop/algo_project/backend/venv/bin/python3 -m pytest research/levels -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import levels as lv  # noqa: E402

MIN = lv.MIN


def synth(n_days=3, base=1000.0, start="2019-06-02 22:00"):
    """Trading days of 1380 minutes (18:00 -> 17:00 New York, EDT), flat price."""
    parts = []
    t0 = pd.Timestamp(start, tz="UTC")
    for d in range(n_days):
        parts.append(pd.date_range(t0 + pd.Timedelta(days=d), periods=1380, freq="1min"))
    idx = parts[0].append(parts[1:])
    n = len(idx)
    c = np.full(n, base)
    df = pd.DataFrame({"open": c.copy(), "high": c + 0.02, "low": c - 0.02, "close": c.copy()}, index=idx)
    for dd in range(n_days):                     # each day: one clear high/low so PDH/PDL sit 0.5 away
        df.iloc[dd * 1380 + 600, df.columns.get_loc("high")] = base + 0.5
        df.iloc[dd * 1380 + 700, df.columns.get_loc("low")] = base - 0.5
    return df


def market(df, f=0.001):
    arr = [df[k].to_numpy().copy() for k in ("open", "high", "low", "close")]
    return lv.Market(df.index.asi8, *arr, np.full(len(df), f))


def days_of(df):
    return lv.attach_levels(lv.day_table(df.index, *(df[k].to_numpy() for k in ("open", "high", "low", "close"))))


def unit(level, side, di, placebo=False, uid=0):
    return {"id": uid, "level": level, "side": side, "family": "DAILY", "name": "PDH" if side > 0 else "PDL",
            "days": [di], "cluster": "D", "period_date": pd.Timestamp("2019-06-04"), "parent": 0,
            "is_placebo": placebo, "j": -1}


# ---------------------------------------------------------------- calendar and levels

def test_dst_trading_day_boundaries():
    ts = pd.DatetimeIndex(["2019-03-08 21:59", "2019-03-08 22:00",        # Fri, EST: 16:59 / 17:00 NY
                           "2019-03-10 20:59", "2019-03-10 21:00",        # Sun after spring-forward (EDT)
                           "2019-11-01 20:59", "2019-11-01 21:00",        # Fri, EDT
                           "2019-11-03 21:59", "2019-11-03 22:00"], tz="UTC")  # Sun after fall-back (EST)
    got = [str(x) for x in lv.trading_dates(ts)]
    assert got == ["2019-03-08", "2019-03-09", "2019-03-10", "2019-03-11",
                   "2019-11-01", "2019-11-02", "2019-11-03", "2019-11-04"]


def test_levels_use_only_completed_prior_periods():
    df = synth(3)
    d0 = days_of(df)
    df2 = df.copy()
    last_day = lv.trading_dates(df2.index) == d0.date.iloc[-1]
    df2.loc[last_day, "high"] += 50.0                                    # change today's data
    df2.loc[last_day, "low"] -= 50.0
    d1 = days_of(df2)
    assert d1.PDH.iloc[-1] == d0.PDH.iloc[-1] and d1.PDL.iloc[-1] == d0.PDL.iloc[-1]
    assert d1.PDH.iloc[-1] == pytest.approx(df.high[lv.trading_dates(df.index) == d0.date.iloc[-2]].max())
    assert np.isnan(d0.PDH.iloc[0])                                      # no prior day -> no level


def test_excluded_day_removes_next_days_levels():
    df = synth(3)
    td = lv.trading_dates(df.index)
    first = td == td[0]
    df = df[~first | (np.cumsum(first) <= 900)]                          # day 1: 900/1380 < 80 %
    d = days_of(df)
    assert d.excluded.iloc[0] and np.isnan(d.PDH.iloc[1])


# ---------------------------------------------------------------- events

def test_first_touch_only_and_fill_rule():
    df = synth(3)
    d = days_of(df)
    pdh = d.PDH.iloc[2]
    i0 = int(d.i0.iloc[2])
    df.iloc[i0 + 100, df.columns.get_loc("high")] = pdh + 0.05           # below the $0.10 fill
    df.iloc[i0 + 200, df.columns.get_loc("high")] = pdh + 0.20           # first real touch
    df.iloc[i0 + 300, df.columns.get_loc("high")] = pdh + 0.20           # later touch: ignored
    mk = market(df)
    recs = lv.run_unit(unit(pdh, +1, 2), mk, d)
    touch = {r["fill"]: r for r in recs if r["etype"] == "TOUCH"}
    assert set(touch) == {0.0, 0.10}                                     # $0.30 never reached
    assert touch[0.10]["t_event"] == df.index[i0 + 200].value
    assert touch[0.0]["t_event"] == df.index[i0 + 100].value
    assert sum(r["etype"] == "TOUCH" and r["fill"] == 0.10 for r in recs) == 1


def test_eligibility_excludes_gap_through():
    df = synth(3)
    d = days_of(df)
    pdh = d.PDH.iloc[2]
    i0 = int(d.i0.iloc[2])
    df.iloc[i0:, :] = pdh + 1.0                                          # opens above the upper level
    mk = market(df)
    assert lv.run_unit(unit(pdh, +1, 2), mk, d) == []
    assert not lv.eligible(pdh + 1.0, pdh, 1.0)
    assert not lv.eligible(pdh - 0.05, pdh, 1.0)                         # < 0.1 σ margin
    assert lv.eligible(pdh - 0.2, pdh, 1.0)


def test_first_passage_target_stop_timeout():
    n = 300
    ts = (np.arange(n) * MIN).astype(np.int64)
    base = np.full(n, 100.0)

    def run(hi, lo, d, touch=False):
        return lv.first_passage(hi, lo, base, ts, 0, 0, 100.0, 1.0, d, touch, bs=(0.3,), hs=(240,))[0][(0.3, 240)]

    hi, lo = base.copy(), base.copy()
    lo[10] = 99.6                                                         # away target (d = -1) first
    hi[20] = 100.4
    assert run(hi, lo, -1) == 1
    hi2, lo2 = base.copy(), base.copy()
    hi2[5] = 100.4                                                        # beyond first -> stop
    lo2[20] = 99.6
    assert run(hi2, lo2, -1) == 0
    assert run(base.copy(), base.copy(), -1) == -1                        # timeout
    hi3, lo3 = base.copy(), base.copy()
    hi3[7], lo3[7] = 100.4, 99.6                                          # same bar -> stop
    assert run(hi3, lo3, -1) == 0
    lo4 = base.copy()
    lo4[0] = 99.6                                                         # touch bar low is not a target
    assert run(base.copy(), lo4, -1, touch=True) == -1
    hi5 = base.copy()
    hi5[250] = 100.4                                                      # after the 240-min window
    assert run(hi5, base.copy(), +1) == -1


# ---------------------------------------------------------------- placebos

def test_placebo_generator():
    reals = [1002.0, 997.0, 1005.0, np.nan]
    a = lv.draw_placebos(np.random.default_rng(7), 1002.0, +1, 2.0, 1000.0, reals)
    b = lv.draw_placebos(np.random.default_rng(7), 1002.0, +1, 2.0, 1000.0, reals)
    assert a == b and len(a) == lv.N_PLACEBO
    a = np.array(a)
    assert (a - 1000.0 >= lv.ELIG * 2.0 - 1e-12).all()                    # same side, eligible
    d = np.abs(a - 1002.0) / 2.0
    assert ((d >= lv.PLC_LO - 1e-12) & (d <= lv.PLC_HI + 1e-12)).all()
    for r in (1002.0, 997.0, 1005.0):
        assert (np.abs(a - r) >= lv.PLC_EXCL * 2.0 - 1e-12).all()        # exclusion zone
    lower = np.array(lv.draw_placebos(np.random.default_rng(1), 997.0, -1, 2.0, 1000.0, reals))
    assert (1000.0 - lower >= lv.ELIG * 2.0 - 1e-12).all()


def test_real_and_placebo_share_the_same_code_path(monkeypatch):
    df = synth(3)
    d = days_of(df)
    pdh = d.PDH.iloc[2]
    i0 = int(d.i0.iloc[2])
    df.iloc[i0 + 200, df.columns.get_loc("high")] = pdh + 0.5
    mk = market(df)
    calls = []
    real_fn = lv.run_unit

    def spy(u, *a, **k):
        calls.append(u["is_placebo"])
        return real_fn(u, *a, **k)

    monkeypatch.setattr(lv, "run_unit", spy)
    lv.evaluate_levels([unit(pdh, +1, 2, False, 0), unit(pdh, +1, 2, True, 1)], mk, d)
    assert calls == [False, True]
    monkeypatch.setattr(lv, "run_unit", real_fn)
    r_real = lv.run_unit(unit(pdh, +1, 2, False), mk, d)
    r_plc = lv.run_unit(unit(pdh, +1, 2, True), mk, d)
    strip = lambda rs: [{k: v for k, v in r.items() if k != "is_placebo"} for r in rs]
    assert len(r_real) > 0
    pd.testing.assert_frame_equal(pd.DataFrame(strip(r_real)), pd.DataFrame(strip(r_plc)))   # NaN-safe


# ---------------------------------------------------------------- inference

def test_bootstrap_resamples_days_not_events():
    rng = np.random.default_rng(12345)
    rows = []
    for c in range(40):
        p = rng.random()
        rows.append({"unit": c, "parent": c, "is_placebo": False, "cluster": f"D{c}", "v": float(rng.random() < p)})
        for j in range(3):
            rows.append({"unit": 1000 + 10 * c + j, "parent": c, "is_placebo": True, "cluster": f"D{c}",
                         "v": float(rng.random() < 0.4)})
    ev = pd.DataFrame(rows)
    cl = pd.Index(sorted(ev.cluster.unique()))
    W = lv.boot_weights(len(cl), B=2000, seed=3)
    assert W.shape == (2000, len(cl)) and np.allclose(W.sum(axis=1), len(cl))
    a = lv.paired_effect(ev, "v", cl, W)
    dup = pd.concat([ev, ev.assign(unit=ev.unit + 100000, parent=np.where(ev.is_placebo, ev.parent, ev.parent + 100000))])
    dup.loc[dup.unit >= 100000, "parent"] = dup.loc[dup.unit >= 100000, "parent"].where(
        ~dup.loc[dup.unit >= 100000, "is_placebo"], dup.loc[dup.unit >= 100000, "parent"])
    b = lv.paired_effect(dup, "v", cl, W)
    # duplicating every event inside its day leaves a day-clustered bootstrap unchanged
    assert a["ci_lo"] == pytest.approx(b["ci_lo"]) and a["ci_hi"] == pytest.approx(b["ci_hi"])


def test_bh():
    adj, rej = lv.bh(np.array([0.001, 0.02, 0.04, 0.5]), 0.05)
    assert list(rej) == [True, True, False, False] or list(rej) == [True, True, True, False]
    assert (adj >= np.array([0.001, 0.02, 0.04, 0.5]) - 1e-12).all()


def test_sealed_assert_fires():
    with pytest.raises(AssertionError, match="SEALED"):
        lv.assert_sealed_ns(np.array([pd.Timestamp("2024-01-02", tz="UTC").value]), "x")
    lv.assert_sealed_ns(np.array([pd.Timestamp("2023-12-29", tz="UTC").value]), "x")
