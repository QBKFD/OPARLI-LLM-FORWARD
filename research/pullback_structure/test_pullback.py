"""research/pullback_structure: the pipeline on synthetic bars only (no real data).

    python -m pytest research/pullback_structure -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import params as P  # noqa: E402
import pullback as pb  # noqa: E402

# the state-machine tests use 3 swings on paths built for it; test_two_swings covers P.N_SWINGS = 2
RUN = dict(k=P.K, x_sigma=P.X_SIGMA, w=P.W, horizon=P.HORIZON, w_alt=P.W_ALT, n_swings=3)


def path(points, steps=4):
    """Candle mids along straight legs between the points."""
    out = [float(points[0])]
    for a, b in zip(points[:-1], points[1:]):
        out += list(np.linspace(a, b, steps + 1)[1:])
    return np.array(out)


def series_from_mids(mids, sig=1.0, half=0.2, per=4, bull=False):
    """Candles with open = close = mid, high = mid + half, low = mid - half. Each
    candle has `per` 1-min bars: the first reaches the high, the second the low."""
    n = len(mids)
    o = c = np.asarray(mids, float)
    h, lo = o + half, o - half
    t0 = pd.Timestamp("2016-02-01", tz="UTC").value
    cstart = t0 + np.arange(n) * 4 * 3600 * 10**9
    cend = cstart + 4 * 3600 * 10**9
    i0 = np.arange(n) * per
    i1 = i0 + per
    mt = (cstart[:, None] + np.arange(per)[None, :] * pb.NS_MIN).ravel()
    mh = np.repeat(o, per)
    ml = np.repeat(o, per)
    mh[i0] = h                     # bar 0 holds the high
    ml[i0 + 1] = lo                # bar 1 holds the low
    cd = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c,
                       "start": pd.to_datetime(cstart, utc=True), "end": pd.to_datetime(cend, utc=True),
                       "i0": i0, "i1": i1})
    return cd, mt, mh, ml, np.full(n, sig)


BEAR_PREFIX = [37, 40, 36, 39, 35, 38, 34]     # swing highs 40 > 39 > 38, swing lows 36 > 35 > 34


def bear_events(tail, **kw):
    mids = path(BEAR_PREFIX + tail)
    cd, mt, mh, ml, sig = series_from_mids(mids, **kw)
    s = pb.series(cd, mt, mh, ml, sig, bull=False)
    ev, cnt = pb.run_events(s, events_from_ns=0, **RUN)
    return ev, cnt, mids


# ----------------------------------------------------------------- swings


def test_swings_strict_and_need_k_each_side():
    x = np.array([5, 4, 3, 2, 3, 4, 5, 1, 2])
    assert list(pb.swings(x, 3, low=True)) == [3]          # index 7 has only 1 candle after it
    assert list(pb.swings(np.array([3, 2, 1, 1, 2, 3, 4]), 3, low=True)) == []   # a tie makes no swing


# ----------------------------------------------------------------- outcomes


def test_outcome_A_and_baseline():
    ev, _, mids = bear_events([37, 31])
    e = [x for x in ev if x.kind != "truncated"][0]
    assert (e.sl, e.lh) == (pytest.approx(33.8), pytest.approx(38.2))
    assert e.turn == e.i + 5                               # the first close below the previous low, after i + k
    assert e.ph == pytest.approx(37.2)
    assert e.p == pytest.approx((37.2 - mids[e.turn]) / (37.2 - 33.8))
    assert e.depth == pytest.approx(3.4)
    assert e.a == 1 and e.label == "A"


def test_outcome_B_break_in_pullback():
    ev, _, _ = bear_events([42, 44])                      # straight up through LH, then +1 sigma
    e = ev[0]
    assert e.turn == -1 and e.brk_from == "pullback"
    assert e.bc == "B" and e.label == "B"


def test_outcome_C_then_q3():
    ev, _, mids = bear_events([39, 39, 39, 39, 39, 39, 39, 30], )
    e = ev[0]
    assert e.bc == "C" and e.c_fix == e.brk + P.W
    assert e.h3 == pytest.approx(39.2)
    assert e.p3 == pytest.approx((39.2 - 39.0) / (39.2 - 33.8))
    assert e.a3 == 1


def test_break_during_confirmation_counts():
    ev, _, _ = bear_events([44] * 8)                     # 34 -> 44: closes above LH = 38.2 at i + 2
    e = ev[0]
    assert e.brk == e.i + 2 and e.brk <= e.conf and e.brk_from == "pullback" and e.bc == "B"


def test_w_sensitivity_recorded():
    ev, _, _ = bear_events([39, 39, 39, 39, 39, 39, 39, 30])
    assert set(ev[0].bc_w) == set(P.W_ALT)


def test_bull_is_the_mirror():
    mids = path(BEAR_PREFIX + [37, 31])
    cd, mt, mh, ml, sig = series_from_mids(-mids + 100)
    s = pb.series(cd, mt, mh, ml, sig, bull=True)
    ev, _ = pb.run_events(s, events_from_ns=0, **RUN)
    e = [x for x in ev if x.kind != "truncated"][0]
    assert e.sign == -1 and e.a == 1
    assert e.p == pytest.approx((37.2 - mids[e.turn]) / (37.2 - 33.8))


def test_truncated_when_data_ends():
    ev, _, _ = bear_events([37, 36])                      # turns, then the data ends unresolved
    assert ev[0].kind == "truncated"


def test_sigma_missing_refuses_the_event():
    mids = path(BEAR_PREFIX + [37, 31])
    cd, mt, mh, ml, sig = series_from_mids(mids)
    sig[:] = np.nan
    ev, cnt = pb.run_events(pb.series(cd, mt, mh, ml, sig, False), events_from_ns=0, **RUN)
    assert ev == [] and cnt["no_sigma"] == 1


def test_events_from_excludes_earlier_starts():
    mids = path(BEAR_PREFIX + [37, 31])
    cd, mt, mh, ml, sig = series_from_mids(mids)
    ev, cnt = pb.run_events(pb.series(cd, mt, mh, ml, sig, False), events_from_ns=2**62, **RUN)
    assert ev == [] and cnt["before_events_start"] >= 1


# ----------------------------------------------------------------- first passage (P12, P13)


def _s(mh, ml):
    z = np.zeros(1)
    return pb.Series(z, z, z, z, z, z, z, z, np.arange(len(mh)), np.asarray(mh, float), np.asarray(ml, float), z, 1)


def test_first_passage_order_and_tie():
    assert pb.first_passage(_s([5, 5, 5], [4, 0, 4]), 0, 3, 1, 9) == ("down", 1)
    assert pb.first_passage(_s([5, 10, 5], [4, 4, 0]), 0, 3, 1, 9) == ("up", 1)
    assert pb.first_passage(_s([10], [0]), 0, 1, 1, 9) == ("up", 0)       # one bar holds both: not A
    assert pb.first_passage(_s([5], [4]), 0, 1, 1, 9) == ("none", -1)
    assert pb.first_passage(_s([9], [1]), 0, 1, 1, 9) == ("none", -1)     # touching is not passing


# ----------------------------------------------------------------- candles (P1-P3)


def test_candles_ny_anchor_dst_and_drops():
    idx = pd.date_range("2021-03-12 00:00", "2021-03-16 23:59", freq="1min", tz="UTC")
    m = pd.DataFrame({"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.0}, index=idx)
    cd, info = pb.candles(m, P.CANDLE_EDGES_NY, P.CANDLE_END_NY, P.MIN_SHARE)
    ny = cd["start"].dt.tz_convert("America/New_York")
    assert set(ny.dt.hour) <= {18, 22, 2, 3, 6, 10, 14}
    assert (cd["scheduled"] <= 300).all() and (cd.loc[cd["slot"] == 5, "end"].dt.tz_convert("America/New_York").dt.hour == 17).all()
    assert info["bars_in_break_hour"] > 0                  # bars in 17:00-18:00 NY are not in any candle
    spring = cd[(cd["start"].dt.tz_convert("America/New_York").dt.date == pd.Timestamp("2021-03-14").date())
                & (cd["slot"] == 2)]
    assert int(spring["scheduled"].iloc[0]) == 180          # 02:00 NY doesn't exist that day
    # the first candle has only 180 of 240 minutes (> 50 %): kept; a 100-minute candle is dropped
    # 02:00-06:00 UTC on 16 March is the 22-02 NY candle: 150 of its 240 minutes gone -> dropped
    m2 = m[(m.index < pd.Timestamp("2021-03-16 02:00", tz="UTC")) | (m.index >= pd.Timestamp("2021-03-16 04:30", tz="UTC"))]
    cd2, info2 = pb.candles(m2, P.CANDLE_EDGES_NY, P.CANDLE_END_NY, P.MIN_SHARE)
    assert info2["candles_dropped_incomplete"] == 1 and len(cd2) == len(cd) - 1


# ----------------------------------------------------------------- statistics


def test_excess_test_and_bh():
    rng = np.random.default_rng(0)
    month = np.repeat(np.arange(60), 10)
    x = rng.normal(0.2, 0.1, size=600)
    r = pb.excess_test(month, x, 2000, 1)
    assert r["ci95"][0] > 0 and r["p_one_sided"] < 0.01
    r0 = pb.excess_test(month, x - x.mean(), 2000, 1)
    assert 0.3 < r0["p_one_sided"] < 0.7
    sys.path.insert(0, str(HERE.parent / "sigma_compare"))
    import run_sigma as rs
    p = {"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.5}
    assert {k: v["p_adj"] for k, v in pb.bh(p, 0.05).items()} == {k: v["p_adj"] for k, v in rs.bh(p, 0.05).items()}


def test_share_test_two_sided():
    month = np.repeat(np.arange(40), 5)
    is_b = np.tile([1, 0, 0, 0, 0], 40).astype(bool)
    assert pb.share_test(month, is_b, 0.2, 2000, 1)["p_two_sided"] > 0.5
    assert pb.share_test(month, is_b, 0.6, 2000, 1)["p_two_sided"] < 0.01


def test_simulated_bars_are_consistent():
    o, h, lo, c = pb.simulate_minutes(100.0, np.full(1000, 1e-3), np.random.default_rng(3))
    assert np.all(h >= np.maximum(o, c)) and np.all(lo <= np.minimum(o, c)) and o[0] == 100.0
    assert np.allclose(o[1:], c[:-1])


# ----------------------------------------------------------------- amendment 1: verdicts and terciles


def test_verdicts():
    import common
    bh_ok, bh_no = {"Q1": {"p_adj": 0.01}}, {"Q1": {"p_adj": 0.2}}
    assert common.verdict({"excess": 0.08, "ci95": [0.01, 0.15]}, bh_ok) == "PASS"
    assert common.verdict({"excess": 0.08, "ci95": [0.01, 0.15]}, bh_no) == "INCONCLUSIVE"
    assert common.verdict({"excess": 0.01, "ci95": [-0.03, 0.04]}, bh_no) == "FAIL"
    assert common.verdict({"excess": 0.03, "ci95": [-0.02, 0.08]}, bh_no) == "INCONCLUSIVE"
    assert common.verdict({"too_few": True}, bh_no).startswith("INCONCLUSIVE")


def test_terciles():
    assert list(pb.tercile_of(np.array([1.0, 2.0, 3.0, 4.0]), (2.0, 3.5))) == [0, 1, 1, 2]


# ----------------------------------------------------------------- the gallery (P37), synthetic bars


def test_gallery_samples_and_renders(tmp_path):
    import gallery
    evs, series = [], {}
    for tail, sign in (([37, 31], 1), ([42, 44], 1), ([39] * 7 + [30], 1)):
        mids = path(BEAR_PREFIX + tail)
        cd, mt, mh, ml, sig = series_from_mids(mids)
        s = pb.series(cd, mt, mh, ml, sig, bull=False)
        ev, _ = pb.run_events(s, events_from_ns=0, **RUN)
        series[1] = s
        evs += [(e, s) for e in ev]
    labels = sorted(e.label for e, _ in evs)
    assert labels == ["A", "B", "C"]
    picks = gallery.sample([e for e, _ in evs], {1: series[1], -1: series[1]}, 7, 20, 10)
    assert sorted(g for g, _ in picks if not g.startswith("audit")) == ["bear A", "bear B", "bear C"]
    assert gallery.sample([e for e, _ in evs], {1: series[1], -1: series[1]}, 7, 20, 10) == picks   # seeded
    for e, s in evs:
        page = gallery.render([("t", e)], {1: s}, tmp_path / e.label)
        assert (tmp_path / e.label / "event_000.png").stat().st_size > 1000
        assert "event_000.png" in page.read_text()


def test_sim_candles_match_candles():
    import run_rw
    idx = pd.date_range("2021-03-10 00:00", "2021-03-17 23:59", freq="1min", tz="UTC")
    o, h, lo, c = pb.simulate_minutes(1800.0, np.full(len(idx), 5e-4), np.random.default_rng(1))
    m = pd.DataFrame({"open": o, "high": h, "low": lo, "close": c}, index=idx)
    m = m[(m.index < pd.Timestamp("2021-03-16 02:00", tz="UTC")) | (m.index >= pd.Timestamp("2021-03-16 04:30", tz="UTC"))]
    cd, _ = pb.candles(m, P.CANDLE_EDGES_NY, P.CANDLE_END_NY, P.MIN_SHARE)
    cs = run_rw.sim_candles(cd, *(m[x].to_numpy() for x in ("open", "high", "low", "close")))
    for col in ("open", "high", "low", "close"):
        assert np.allclose(cs[col].to_numpy(), cd[col].to_numpy())


def test_hourly_candles_and_drops():
    idx = pd.date_range("2021-03-12 00:00", "2021-03-12 05:59", freq="1min", tz="UTC")
    rng = np.random.default_rng(2)
    c = 1800 + np.cumsum(rng.normal(0, 0.1, len(idx)))
    m = pd.DataFrame({"open": c, "high": c + 0.05, "low": c - 0.05, "close": c}, index=idx)
    m = m[(m.index < pd.Timestamp("2021-03-12 02:00", tz="UTC")) | (m.index >= pd.Timestamp("2021-03-12 02:40", tz="UTC"))]
    cd, info = pb.candles_hourly(m, P.MIN_SHARE)
    assert info == {"candles_kept": 5, "candles_dropped_incomplete": 1}       # 02:00 has 20 of 60 minutes
    first = m[m.index < pd.Timestamp("2021-03-12 01:00", tz="UTC")]
    assert cd.loc[0, "high"] == first["high"].max() and cd.loc[0, "close"] == first["close"].iloc[-1]
    assert (cd["end"] - cd["start"] == pd.Timedelta(hours=1)).all()


def test_params_are_amendment_2():
    assert (P.TIMEFRAME, P.K, P.N_SWINGS, P.X_SIGMA, P.W, P.HORIZON) == ("1h", 3, 2, 1.0, 5, 30)


def test_two_swings():
    """Amendment 2: 2 swings (1 lower high, 1 lower low) start an event at the
    second trough already; 3 swings need the third."""
    mids = path([37, 40, 36, 39, 35, 38, 31])
    cd, mt, mh, ml, sig = series_from_mids(mids)
    s = pb.series(cd, mt, mh, ml, sig, bull=False)
    ev2, _ = pb.run_events(s, events_from_ns=0, **{**RUN, "n_swings": 2})
    ev3, _ = pb.run_events(s, events_from_ns=0, **RUN)
    assert [e.i for e in ev2][:1] == [16] and ev3 == []   # the second trough (35); the first has 1 swing high before it
    assert ev2[0].lh == pytest.approx(39.2)        # LH = the newer of the 2 swing highs
