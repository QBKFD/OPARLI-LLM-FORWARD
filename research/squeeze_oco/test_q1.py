"""research/squeeze_oco/q1.py on synthetic bars and a synthetic model with a
known seasonal profile (no data needed).
python -m pytest research/squeeze_oco -q"""
import inspect
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import q1  # noqa: E402
import stage1  # noqa: E402
from test_stage1 import synthetic  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402
from backend.datafeed.ingest.xauusd_bars import _market_open  # noqa: E402

FIRST = "2019-03-11"                      # US DST from 2019-03-10: one season throughout
END = pd.Timestamp("2019-03-23", tz="UTC")
BASE = 0.0003 ** 2                        # quiet 5-min bucket: mean r² per minute
BUSY_HOUR = 13                            # UTC 13:00-13:59 (both DST flags): 9 × BASE


def known_profile() -> np.ndarray:
    prof = np.full(vb.N_BUCKETS, BASE)
    for dst in (0, 1):
        prof[dst * 288 + BUSY_HOUR * 12: dst * 288 + (BUSY_HOUR + 1) * 12] = 9 * BASE
    return prof


def known_model(b0: float) -> vb.SigmaModel:
    """HAR with intercept only (level factor exp(b0)·c, c = 1) and the known profile."""
    params = vb.VolParams(profile=tuple(known_profile()), s_hour_mean=(0.0,) * 24,
                          train_start=datetime(2019, 1, 1, tzinfo=timezone.utc),
                          train_end=datetime(2019, 3, 1, tzinfo=timezone.utc))
    return vb.SigmaModel(name="known", version=1, sha256="-", params=params,
                         horizons={H: ((b0, 0.0, 0.0, 0.0), 1.0) for H in (60, 240)})


def seasonal_bars(days=12, seed=5, flat_range=False):
    """Made-up 1-min bars whose returns follow the known profile. flat_range:
    every bar's high/low is 1300 ± 0.5, so every complete candle has range 1.0."""
    prof, rng, p, rows = known_profile(), random.Random(seed), 1300.0, []
    t = pd.Timestamp(FIRST, tz="UTC")
    for _ in range(days * 1440):
        if _market_open(t.to_pydatetime()):
            sd = math.sqrt(prof[vb._buckets(pd.DatetimeIndex([t]))[0]])
            o = p
            if flat_range:
                p = 1300.0 + rng.uniform(-0.2, 0.2)
                rows.append((t, o, 1300.5, 1299.5, p))
            else:
                p = o * math.exp(rng.gauss(0, sd))
                rows.append((t, o, max(o, p) + 0.1, min(o, p) - 0.1, p))
        t += pd.Timedelta(minutes=1)
    return pd.DataFrame([r[1:] for r in rows], columns=["open", "high", "low", "close"],
                        index=pd.DatetimeIndex([r[0] for r in rows], name="timestamp"))


def scores_for(m, model):
    ctx = stage1.context(m, END, (60, 240), 0.90)
    return q1.candle_scores(m, ctx, model, 15, 60)


def test_no_public_function_has_a_default_value():
    for name, f in inspect.getmembers(q1, inspect.isfunction):
        if f.__module__ != "q1" or name.startswith("_"):
            continue
        defaults = [p for p in inspect.signature(f).parameters.values() if p.default is not p.empty]
        assert not defaults, (name, defaults)


def test_known_profile_gives_the_expected_sigma_tf():
    b0 = 0.3
    m = seasonal_bars()
    sc = scores_for(m, known_model(b0)).dropna(subset=["score"])
    assert len(sc) > 300                                    # ~4.6 trading days after the 120-hour warm-up
    busy = sc.index.hour == BUSY_HOUR
    assert busy.any() and (~busy).any()
    # sigma_tf² = 15 minutes × the slot's profile × exp(b0) × c, exactly
    want = np.sqrt(15 * np.where(busy, 9 * BASE, BASE) * math.exp(b0))
    np.testing.assert_allclose(sc["sigma_tf_ret"], want, rtol=1e-12)
    assert (sc["closed_minutes"] == 0).all()
    # range / (close_ref × sigma_tf)
    one = sc.iloc[100]
    i = m.index.searchsorted(one.name) - 1
    assert one["score"] == pytest.approx(one["range"] / (m["close"].iat[i] * one["sigma_tf_ret"]), rel=1e-12)


def test_busy_slot_scores_lower_than_the_same_range_at_a_quiet_slot():
    m = seasonal_bars(flat_range=True)
    sc = scores_for(m, known_model(0.0)).dropna(subset=["score"])
    assert (sc["range"] == 1.0).all()                       # the same range everywhere
    day = sc.index.normalize() == pd.Timestamp("2019-03-21", tz="UTC")
    busy = sc[day & (sc.index.hour == BUSY_HOUR) & (sc.index.minute == 0)]["score"].iat[0]
    quiet = sc[day & (sc.index.hour == 3) & (sc.index.minute == 0)]["score"].iat[0]
    assert busy < quiet
    assert busy / quiet == pytest.approx(1 / 3, rel=1e-3)   # sqrt(BASE / 9·BASE); close_ref within ±0.2 of 1300


def test_sigma_tf_is_var_60_times_the_candles_share_of_p_60():
    """On the pinned model: sigma_tf² = sigma_over(state_at(start), 60)² × P_candle / P(start, 60)."""
    m = synthetic(first="2019-03-04", days=12)
    model = vb.load_sigma_model("parquet2021_2023")
    end = pd.Timestamp("2019-03-16", tz="UTC")
    ctx = stage1.context(m, end, (60, 240), 0.90)
    sc = q1.candle_scores(m, ctx, model, 15, 60)
    ok = sc.dropna(subset=["score"])
    assert len(ok) > 200 and sc["score"].isna().any()      # the warm-up has none
    prof = np.asarray(model.params.profile)
    for s in ok.index[::37]:
        st = vb.state_at(model.params, m, s)
        assert st.reason is None and pd.Timestamp(st.label) == ok.at[s, "t0"]
        var60 = vb.sigma_over(model, st, 60).sigma_ret ** 2
        p60 = prof[vb._buckets(pd.DatetimeIndex(vb.open_window(s, 60)[0]))].sum()
        cand = pd.date_range(s, periods=15, freq="1min")
        pk = prof[vb._buckets(cand[[_market_open(u.to_pydatetime()) for u in cand]])].sum()
        want = math.sqrt(var60 * pk / p60)
        assert ok.at[s, "sigma_tf_ret"] == pytest.approx(want, rel=1e-9)
        assert ok.at[s, "sigma_tf_px"] == pytest.approx(st.close_ref * want, rel=1e-9)


def test_scores_hold_no_price_row():
    m = synthetic(first="2019-03-04", days=8)
    model = vb.load_sigma_model("parquet2021_2023")
    sc = q1.candle_scores(m, stage1.context(m, END, (60, 240), 0.90), model, 15, 60)
    assert not {"open", "high", "low", "close", "close_ref"} & set(sc.columns)


def test_candles_are_complete_and_aligned():
    m = synthetic(first="2019-03-04", days=3).drop(pd.Timestamp("2019-03-05 10:07", tz="UTC"))
    c = q1.candles(m, 15)
    assert pd.Timestamp("2019-03-05 10:00", tz="UTC") not in c.index
    assert pd.Timestamp("2019-03-05 10:15", tz="UTC") in c.index
    assert (c.index.minute % 15 == 0).all()
    with pytest.raises(ValueError):
        q1.candles(m, 7)


def _scores(years_vals: dict) -> pd.Series:
    idx, vals = [], []
    for y, v in years_vals.items():
        idx += list(pd.date_range(f"{y}-02-01", periods=len(v), freq="15min", tz="UTC"))
        vals += v
    return pd.Series(vals, index=pd.DatetimeIndex(idx), dtype=float)


def test_thresholds_are_walk_forward_and_never_use_the_scored_year():
    s = _scores({2012: [1, 2, 3, 4, 5], 2013: [10, 20, 30, 40, 50], 2014: [7.0] * 5})
    thr = q1.thresholds(s, 0.25, [2012, 2013, 2014])
    assert math.isnan(thr[2012])                                       # no earlier year
    assert thr[2013] == np.quantile([1, 2, 3, 4, 5], 0.25)
    assert thr[2014] == np.quantile([1, 2, 3, 4, 5, 10, 20, 30, 40, 50], 0.25)
    s2 = s.copy()
    s2[s2.index.year == 2013] = -100.0                                  # the scored year changes ...
    assert q1.thresholds(s2, 0.25, [2012, 2013, 2014])[2013] == thr[2013]   # ... its threshold doesn't
    # a year before the first one (outside the dev period) is never used
    assert q1.thresholds(_scores({2011: [0.0] * 5, 2012: [1, 2, 3, 4, 5]}), 0.25, [2012, 2013])[2013] == thr[2013]


def test_runs_at_labels():
    T = pd.DatetimeIndex(["2013-03-05 10:00", "2013-03-05 11:00", "2013-03-05 12:00", "2012-06-05 10:00"], tz="UTC")
    rows = {}
    for t in T:
        for k in range(1, 5):
            rows[t - pd.Timedelta(minutes=15 * k)] = 0.5
    rows[T[1] - pd.Timedelta(minutes=30)] = 2.0                        # one candle not small
    del rows[T[2] - pd.Timedelta(minutes=60)]                           # one candle missing
    sc = pd.DataFrame({"score": pd.Series(rows)}).sort_index()
    thr = {2013: 1.0}                                                   # 2012: no threshold
    got = q1.runs_at_labels(sc, T, thr, 15, 4)
    assert got.tolist() == [True, False, False, False]
    assert q1.runs_at_labels(sc, T, thr, 15, 3).tolist() == [True, False, True, False]   # the last 45 min only
    with pytest.raises(ValueError):
        q1.runs_at_labels(sc, T, thr, 15, 5)


def test_sliding_runs_overlap():
    idx = pd.date_range("2013-03-05 10:00", periods=6, freq="15min", tz="UTC")
    sc = pd.DataFrame({"score": [0.5] * 6}, index=idx)
    assert q1.sliding_runs(sc, {2013: 1.0}, 15, 4) == 3
    assert q1.sliding_runs(sc, {2013: 1.0}, 15, 3) == 4


def test_clustering():
    t = pd.DatetimeIndex(["2013-03-05 10:00", "2013-03-05 11:00", "2013-03-05 12:00", "2013-03-05 20:00",
                          "2013-03-07 10:00"], tz="UTC")
    td = pd.DatetimeIndex(["2013-03-05"] * 4 + ["2013-03-07"])
    c = q1.clustering(t, td, (240,))
    assert c["runs"] == 5 and c["longest_chain_hours"] == 3
    assert c["share_previous_label_also_run"] == pytest.approx(2 / 4)
    assert c["trade_dates_with_runs"] == 2 and c["runs_per_trade_date_max"] == 4
    assert c["greedy_spaced_240min"] == 3                               # 10:00, 20:00, day 3


def test_choose_follows_the_owners_rule():
    rule = dict(r_order=(4, 3), q_order=(0.20, 0.25), min_runs=300)
    assert q1.choose({(0.20, 4): 300, (0.25, 4): 900, (0.20, 3): 1, (0.25, 3): 1}, **rule) == {"q": 0.20, "r": 4, "n": 300}
    assert q1.choose({(0.20, 4): 299, (0.25, 4): 300, (0.20, 3): 999, (0.25, 3): 999}, **rule)["q"] == 0.25
    assert q1.choose({(0.20, 4): 1, (0.25, 4): 299, (0.20, 3): 301, (0.25, 3): 999}, **rule) == {"q": 0.20, "r": 3, "n": 301}
    assert q1.choose({(0.20, 4): 1, (0.25, 4): 2, (0.20, 3): 3, (0.25, 3): 299}, **rule)["r"] is None


def test_event_counts_on_synthetic_bars_across_a_year_end():
    m = synthetic(first="2018-12-10", days=30)
    model = vb.load_sigma_model("parquet2021_2023")
    start, end = pd.Timestamp("2018-01-01", tz="UTC"), pd.Timestamp("2019-01-09", tz="UTC")
    ctx = stage1.context(m, end, (60, 240), 0.90)
    sc = q1.candle_scores(m, ctx, model, 15, 60)
    grid = stage1.state_table(ctx, model, start, end)
    ec = q1.event_counts(sc, grid, [2018, 2019], (0.20, 0.25), (3, 4), 15, (60, 240))
    for key, yrs in ec["per_year"].items():
        assert yrs[2018]["runs"] == 0                                     # the first year has no threshold
        assert ec["totals"][key]["runs"] == sum(v["runs"] for v in yrs.values()) == ec["clustering"][key]["runs"]
        for v in yrs.values():
            assert v["runs_noncrossing_240"] <= v["runs_noncrossing_60"] <= v["runs"]
    assert ec["totals"][(0.25, 3)]["runs"] >= ec["totals"][(0.20, 3)]["runs"] >= ec["totals"][(0.20, 4)]["runs"]
    assert ec["totals"][(0.25, 3)]["runs"] > 0
