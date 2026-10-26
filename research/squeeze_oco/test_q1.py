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
    thr = {y: v for (y, h), v in q1.thresholds(s, 0.25, [2012, 2013, 2014], None).items()}   # pooled: hour -1
    assert math.isnan(thr[2012])                                       # no earlier year
    assert thr[2013] == np.quantile([1, 2, 3, 4, 5], 0.25)
    assert thr[2014] == np.quantile([1, 2, 3, 4, 5, 10, 20, 30, 40, 50], 0.25)
    s2 = s.copy()
    s2[s2.index.year == 2013] = -100.0                                  # the scored year changes ...
    assert q1.thresholds(s2, 0.25, [2012, 2013, 2014], None)[(2013, -1)] == thr[2013]   # ... its threshold doesn't
    # a year before the first one (outside the dev period) is never used
    assert q1.thresholds(_scores({2011: [0.0] * 5, 2012: [1, 2, 3, 4, 5]}), 0.25, [2012, 2013], None)[(2013, -1)] == thr[2013]


def test_runs_at_labels():
    T = pd.DatetimeIndex(["2013-03-05 10:00", "2013-03-05 11:00", "2013-03-05 12:00", "2012-06-05 10:00"], tz="UTC")
    rows = {}
    for t in T:
        for k in range(1, 5):
            rows[t - pd.Timedelta(minutes=15 * k)] = 0.5
    rows[T[1] - pd.Timedelta(minutes=30)] = 2.0                        # one candle not small
    del rows[T[2] - pd.Timedelta(minutes=60)]                           # one candle missing
    sc = pd.DataFrame({"score": pd.Series(rows)}).sort_index()
    thr = {(2013, -1): 1.0}                                             # 2012: no threshold
    got = q1.runs_at_labels(sc, T, thr, 15, 4, None)
    assert got.tolist() == [True, False, False, False]
    assert q1.runs_at_labels(sc, T, thr, 15, 3, None).tolist() == [True, False, True, False]   # the last 45 min only
    with pytest.raises(ValueError):
        q1.runs_at_labels(sc, T, thr, 15, 5, None)
    # per New York hour: the 09:00-10:00 UTC candles are NY hour 4 (EST) on 2013-03-05
    ny = {(2013, h): (1.0 if h == 4 else 0.1) for h in range(24)}
    assert q1.runs_at_labels(sc, T, ny, 15, 4, "America/New_York").tolist() == [True, False, False, False]
    ny[(2013, 4)] = 0.1
    assert not q1.runs_at_labels(sc, T, ny, 15, 4, "America/New_York").any()


def test_sliding_runs_overlap():
    idx = pd.date_range("2013-03-05 10:00", periods=6, freq="15min", tz="UTC")
    sc = pd.DataFrame({"score": [0.5] * 6}, index=idx)
    assert q1.sliding_runs(sc, {(2013, -1): 1.0}, 15, 4, None) == 3
    assert q1.sliding_runs(sc, {(2013, -1): 1.0}, 15, 3, None) == 4


def test_clustering():
    t = pd.DatetimeIndex(["2013-03-05 10:00", "2013-03-05 11:00", "2013-03-05 12:00", "2013-03-05 20:00",
                          "2013-03-07 10:00"], tz="UTC")
    td = pd.DatetimeIndex(["2013-03-05"] * 4 + ["2013-03-07"])
    c = q1.clustering(t, td, (240,), (0.5,))
    assert c["runs"] == 5 and c["longest_chain_hours"] == 3
    assert c["share_previous_label_also_run"] == pytest.approx(2 / 4)
    assert c["trade_dates_with_runs"] == 2 and c["runs_per_trade_date_max"] == 4
    assert c["greedy_spaced_240min"] == 3                               # 10:00, 20:00, day 3
    assert c["runs_per_utc_hour_of_label"] == {10: 2, 11: 1, 12: 1, 20: 1}


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
    ec = q1.event_counts(sc, grid, [2018, 2019], (0.20, 0.25), (3, 4), 15, (60, 240), (0.5,), "America/New_York")
    for key, yrs in ec["per_year"].items():
        assert yrs[2018]["runs"] == 0                                     # the first year has no threshold
        assert ec["totals"][key]["runs"] == sum(v["runs"] for v in yrs.values()) == ec["clustering"][key]["runs"]
        for v in yrs.values():
            assert v["runs_noncrossing_240"] <= v["runs_noncrossing_60"] <= v["runs"]
    assert ec["totals"][(0.25, 3)]["runs"] >= ec["totals"][(0.20, 3)]["runs"] >= ec["totals"][(0.20, 4)]["runs"]
    assert ec["totals"][(0.25, 3)]["runs"] > 0
    for key, ph in ec["per_hour"].items():
        assert sum(v["runs"] for v in ph.values()) == ec["totals"][key]["runs"]
        assert sum(v["runs_noncrossing_240"] for v in ph.values()) == ec["totals"][key]["runs_noncrossing_240"]
    assert {h for (y, h) in ec["thresholds"][0.20]} <= set(range(24))


def test_hours_follow_new_york_time_through_dst():
    idx = pd.DatetimeIndex(["2019-01-15 13:00", "2019-07-15 13:00"], tz="UTC")
    assert q1.hours(idx, "America/New_York").tolist() == [8, 9]          # EST, EDT
    assert q1.hours(idx, None).tolist() == [-1, -1]


def test_thresholds_per_hour_use_only_that_hour_and_past_years():
    # 2013: NY hour 3 (08:00 UTC, EST) scores 1..8, NY hour 9 (14:00 UTC) scores 10..80
    idx = list(pd.date_range("2013-02-05 08:00", periods=4, freq="15min", tz="UTC")) + \
        list(pd.date_range("2013-02-06 08:00", periods=4, freq="15min", tz="UTC")) + \
        list(pd.date_range("2013-02-05 14:00", periods=4, freq="15min", tz="UTC")) + \
        list(pd.date_range("2013-02-06 14:00", periods=4, freq="15min", tz="UTC"))
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 10, 20, 30, 40, 50, 60, 70, 80], index=pd.DatetimeIndex(idx), dtype=float)
    thr = q1.thresholds(s, 0.20, [2013, 2014], "America/New_York")
    assert thr[(2014, 3)] == np.quantile(np.arange(1, 9), 0.20)
    assert thr[(2014, 9)] == np.quantile(np.arange(10, 90, 10), 0.20)
    assert math.isnan(thr[(2013, 3)]) and math.isnan(thr[(2013, 9)])
    sc = pd.DataFrame({"score": [2.0, 2.0]}, index=pd.DatetimeIndex(["2014-02-05 08:00", "2014-02-05 14:00"], tz="UTC"))
    # thresholds 2.4 (NY hour 3) and 24 (NY hour 9): 2.0 is small at both, 5.0 only at hour 9
    assert q1.small(sc, thr, pd.DatetimeIndex(sc.index), "America/New_York").tolist() == [True, True]
    sc["score"] = [5.0, 5.0]
    assert q1.small(sc, thr, pd.DatetimeIndex(sc.index), "America/New_York").tolist() == [False, True]


# --- the Q1 test, on synthetic rows (no data) ---------------------------------------

def _rows(seed=1, years=range(2013, 2021), ratio=2.0):
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex([t for y in years for t in pd.date_range(f"{y}-01-02", f"{y}-12-28", freq="7h", tz="UTC")])
    h = rng.uniform(0.5, 1.5, len(idx))
    ind = (rng.uniform(size=len(idx)) < 0.05).astype(int)
    rv = h * rng.exponential(1.0, len(idx)) * np.where(ind == 1, ratio, 1.0)
    return pd.DataFrame({"rv": rv, "h": h, "R": np.sqrt(rv), "E": idx + pd.Timedelta(hours=4), "I": ind}, index=idx)


def test_gamma_is_the_qlike_minimiser():
    r = _rows()
    g = q1.gamma(r)
    p = r[r["I"] == 1]
    assert g == pytest.approx(math.log(np.mean(p.rv / p.h)))
    from scipy.optimize import minimize_scalar
    import run_sigma as rs_
    best = minimize_scalar(lambda x: np.mean(rs_.qlike(p.rv, p.h * math.exp(x))), bounds=(-3, 3), method="bounded").x
    assert g == pytest.approx(best, abs=1e-4)
    assert math.isnan(q1.gamma(r[r["I"] == 0]))


def test_folds_use_only_past_rows_with_the_embargo():
    r = _rows()
    first, emb = pd.Timestamp("2013-01-01", tz="UTC"), pd.Timedelta(hours=24)
    recs, te = q1.folds(r, list(range(2014, 2021)), first, emb)
    assert [x["fold"] for x in recs] == list(range(2014, 2021)) and te.index.is_monotonic_increasing
    for x in recs:
        s = pd.Timestamp(f"{x['fold']}-01-01", tz="UTC")
        tr = r[(r.index >= first) & (r.index <= s - emb) & (r["E"] <= s)]
        assert x["gamma"] == q1.gamma(tr) and x["n_train"] == len(tr)
        # changing every row from s on leaves the fold's gamma alone
        r2 = r.copy()
        r2.loc[r2.index >= s - emb + pd.Timedelta(seconds=1), "rv"] *= 100
        r2.loc[(r2.index <= s - emb) & (r2["E"] > s), "rv"] *= 100           # windows reaching the block
        assert q1.folds(r2, [x["fold"]], first, emb)[0][0]["gamma"] == pytest.approx(x["gamma"])
    assert (te.loc[te.I == 0, "h_aug"] == te.loc[te.I == 0, "h"]).all()
    assert te.index.min() >= pd.Timestamp("2014-01-01", tz="UTC")
    assert np.mean([x["gamma"] for x in recs]) == pytest.approx(math.log(2), abs=0.25)   # the planted ratio


def test_a_fold_without_a_training_pattern_row_raises():
    r = _rows()
    r.loc[r.index < pd.Timestamp("2014-01-01", tz="UTC"), "I"] = 0
    with pytest.raises(ValueError, match="gamma undefined"):
        q1.folds(r, [2014], pd.Timestamp("2013-01-01", tz="UTC"), pd.Timedelta(hours=24))


def test_dm_two_sided():
    rng = np.random.default_rng(3)
    a = rng.uniform(1, 2, 2000)
    b = a - 0.05 + rng.normal(0, 0.1, 2000)
    up, down = q1.dm_two_sided(a, b, 8), q1.dm_two_sided(b, a, 8)
    assert up["stat"] > 0 and up["stat"] == pytest.approx(-down["stat"]) and up["p"] == pytest.approx(down["p"])
    assert up["p"] < 1e-6
    assert q1.dm_two_sided(a, a, 8) == {"stat": 0.0, "p": 1.0, "mean_d": 0.0, "n": 2000}


def test_q2_gate():
    years_ok = [0.1, 0.2, -0.1, 0.3, 0.1, -0.2, 0.05]                    # 5 of 7 positive
    assert q1.q2_gate(True, 0.1, years_ok, 5)
    assert not q1.q2_gate(False, 0.1, years_ok, 5)
    assert not q1.q2_gate(True, -0.01, years_ok, 5)
    assert not q1.q2_gate(True, 0.1, [0.1, 0.2, -0.1, 0.3, -0.1, -0.2, 0.05], 5)


def test_scored_rows_equal_posthoc_rows_plus_window_end_and_indicator():
    m = synthetic(first="2019-03-04", days=12)
    model = vb.load_sigma_model("parquet2021_2023")
    start, end = pd.Timestamp("2019-03-01", tz="UTC"), pd.Timestamp("2019-03-16", tz="UTC")
    ctx = stage1.context(m, end, (60, 240), 0.90)
    grid = stage1.state_table(ctx, model, start, end)
    run = pd.Series(False, index=grid.index)
    run.iloc[::5] = True
    rows = q1.scored_rows(ctx, model, start, end, run)
    for H, g in rows.items():
        ok = grid[grid[f"calib_row_{H}"]]
        assert set(g.index) <= set(ok.index) and len(g) > 30
        np.testing.assert_allclose(g["h"], ok.loc[g.index, f"sigma_ret_{H}"] ** 2, rtol=1e-9)
        assert (g["I"] == run.reindex(g.index).astype(int)).all()
        assert ((g["E"] - g.index) >= pd.Timedelta(minutes=H)).all()


def test_q1_test_run_reporting_on_synthetic_rows():
    """The reporting path of q1_test_run.py after the attempt is counted, on synthetic rows."""
    import importlib.util
    import json as json_
    spec = importlib.util.spec_from_file_location("q1_test_run", Path(__file__).resolve().parent / "q1_test_run.py")
    tr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tr)
    import run_sigma as rs_
    res = {}
    for H in (60, 240):
        recs, te = q1.folds(_rows(seed=H), list(range(2014, 2021)), pd.Timestamp("2013-01-01", tz="UTC"), pd.Timedelta(hours=24))
        la, lb = q1.losses(te)
        res[H] = {"recs": recs, "te": te, "la": la, "lb": lb, "pat": te["I"].to_numpy() == 1, "dm": q1.dm_two_sided(la, lb, 8)}
    d = tr.describe(res[240], 240, "America/New_York")
    assert d["n_test_pattern_rows"] == int(res[240]["pat"].sum()) and len(d["gamma_per_fold"]) == 7
    assert d["dm"]["stat"] > 0                                          # the planted ratio 2 is found
    bh = rs_.bh({H: res[H]["dm"]["p"] for H in res}, 0.05)
    assert q1.q2_gate(bool(bh[240]["reject"]), d["gamma_pooled_oos"], list(d["gamma_per_test_year_oos"].values()), 5)
    json_.dumps({"bh": {str(H): v for H, v in bh.items()}, "d": d},
                default=lambda x: x.item() if isinstance(x, np.generic) else str(x))
