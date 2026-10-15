"""
Tests for research/sigma_compare/run_sigma.py (synthetic data only).
    .venv/bin/python -m pytest research/sigma_compare -q
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_sigma as rs  # noqa: E402

from datafeed.features import vol_block as vb  # noqa: E402
from datafeed.ingest.xauusd_bars import _market_open  # noqa: E402

UTC = "UTC"
CFG = {"load_start": pd.Timestamp("2021-01-04", tz=UTC),
       "train_start": pd.Timestamp("2021-01-04", tz=UTC),
       "end": pd.Timestamp("2021-05-01", tz=UTC),
       "edges": pd.DatetimeIndex(["2021-03-22", "2021-04-05", "2021-04-19"], tz=UTC),
       "eras": None, "half": False}


def season(hour):
    return 1e-8 * (1 + 3 * np.exp(-(((hour - 14) % 24) ** 2) / 8.0))


def level(day):
    return np.exp(1.2 * np.sin(day / 6.0))


DAY0 = pd.Timestamp("2021-02-01", tz=UTC)


def true_var(idx: pd.DatetimeIndex) -> np.ndarray:
    day = ((idx - DAY0) // pd.Timedelta(days=1)).to_numpy()
    return season(idx.hour.to_numpy()) * level(day)


def synthetic(seed=5, drop=0.02, start="2021-01-04", end="2021-05-01"):
    """Market minutes only; variance = time-of-day shape × slowly swinging level."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="1min", tz=UTC, inclusive="left")
    idx = idx[rs.open_flags(idx)]
    idx = idx[rng.random(len(idx)) > drop]
    sd = np.sqrt(true_var(idx))
    close = 1800 * np.exp(np.cumsum(rng.normal(0, 1, len(idx)) * sd))
    return pd.DataFrame({"open": close, "high": close * (1 + 0.5 * sd),
                         "low": close * (1 - 0.5 * sd), "close": close}, index=idx)


@pytest.fixture(scope="module")
def syn():
    m = synthetic()
    return m, rs.build_context(m, CFG["end"])


# ---------------------------------------------------------------- calendar and windows

def test_open_flags_equal_market_open():
    rng = np.random.default_rng(12345)
    spans = [pd.date_range("2021-03-05", "2021-03-22", freq="1min", tz=UTC),     # US DST starts
             pd.date_range("2021-10-29", "2021-11-12", freq="1min", tz=UTC),     # US DST ends
             pd.DatetimeIndex(pd.to_datetime(rng.integers(pd.Timestamp("2007-01-01").value,
                                                          pd.Timestamp("2024-01-01").value, 20000)),
                              tz=UTC).floor("1min")]
    for idx in spans:
        want = np.array([_market_open(t) for t in idx.to_pydatetime()])
        np.testing.assert_array_equal(rs.open_flags(idx), want)


def at(ctx, t):
    return int(np.flatnonzero(ctx.labels == pd.Timestamp(t, tz=UTC))[0])


def test_windows_skip_closed_minutes(syn):
    _, ctx = syn
    # Wed 14:00 UTC (10:00 New York): 60 open minutes straight
    k = at(ctx, "2021-03-24 14:00")
    w = ctx.win[60]
    assert pd.Timestamp(w.E_ns[k], tz=UTC) == pd.Timestamp("2021-03-24 15:00", tz=UTC)
    assert not w.crossing[k]
    # Fri 19:00 UTC (15:00 NY): 120 minutes to the close, then Sunday 18:00 NY = 22:00 UTC
    k = at(ctx, "2021-03-26 19:00")
    w = ctx.win[240]
    assert pd.Timestamp(w.E_ns[k], tz=UTC) == pd.Timestamp("2021-03-29 00:00", tz=UTC)
    assert w.crossing[k]
    # Tue 20:00 UTC (16:00 NY): 60 minutes, the break 21:00-22:00 UTC, then 180 more
    k = at(ctx, "2021-03-23 20:00")
    assert pd.Timestamp(w.E_ns[k], tz=UTC) == pd.Timestamp("2021-03-24 01:00", tz=UTC)
    assert w.crossing[k]


def test_target_and_path_match_a_direct_computation(syn):
    m, ctx = syn
    r = vb._returns(m)
    for t in ["2021-03-24 14:00", "2021-03-26 19:00", "2021-03-23 20:00", "2021-04-07 03:00"]:
        k = at(ctx, t)
        for H in rs.HORIZONS:
            w = ctx.win[H]
            minutes, u = [], pd.Timestamp(t, tz=UTC)
            while len(minutes) < H:
                if _market_open(u.to_pydatetime()):
                    minutes.append(u)
                u += pd.Timedelta(minutes=1)
            inwin = m.index.isin(pd.DatetimeIndex(minutes))
            assert w.valid[k]
            assert w.rv[k] == pytest.approx(float(np.nansum(r[inwin] ** 2)), rel=1e-12)
            assert w.hi[k] == m.high[inwin].max() and w.lo[k] == m.low[inwin].min()
            assert w.close_ref[k] == m.close[m.index < pd.Timestamp(t, tz=UTC)].iat[-1]


# ---------------------------------------------------------------- candidates

def test_ewma_equals_naive_loop():
    x = np.random.default_rng(12345).exponential(1.0, 5000)
    for hl in (30, 960, 15360):
        lam = 2.0 ** (-1.0 / hl)
        np.testing.assert_allclose(rs.ewma(x, lam), rs.ewma_naive(x, lam), rtol=1e-12)
        np.testing.assert_allclose(rs.ewma(x, lam, 3.0), rs.ewma_naive(x, lam, 3.0), rtol=1e-12)


def test_qlike():
    assert rs.qlike(2.0, 2.0) == 0.0
    assert rs.qlike(2.0, 1.0) > 0 and rs.qlike(1.0, 2.0) > 0


def test_plugin_equals_sigma_over(syn):
    _, ctx = syn
    fit = rs.fit_fold(ctx, CFG, CFG["edges"][0])
    rows = rs.common_rows(rs.fold_rows(ctx, fit, 60, CFG["edges"][1], 1, "x", "all"))
    n = rs.check_plugin(ctx, fit, rows, 60, 30, np.random.default_rng(12345))
    rows = rs.common_rows(rs.fold_rows(ctx, fit, 240, CFG["edges"][1], 1, "x", "all"))
    n += rs.check_plugin(ctx, fit, rows, 240, 30, np.random.default_rng(12345))
    assert n >= 50


def test_har_nests_the_plugin(syn):
    _, ctx = syn
    fit = rs.fit_fold(ctx, CFG, CFG["edges"][0])
    fit.model[60]["beta"] = np.array([0.0, 1.0, 0.0, 0.0])
    fit.model[60]["c"] = 1.0
    h = rs.forecasts(fit, 60)
    ok = fit.ok_x
    assert ok.sum() > 1000
    np.testing.assert_allclose(h["b"][ok], h["a"][ok], rtol=1e-14)


# ---------------------------------------------------------------- causality and folds

def perturb_from(m, t, seed=1):
    m2 = m.copy()
    later = m2.index >= t
    m2.loc[later, ["open", "high", "low", "close"]] *= np.exp(
        np.random.default_rng(seed).normal(0, 0.01, (int(later.sum()), 1)))
    return m2


def test_no_look_ahead_in_any_forecast(syn):
    m, ctx = syn
    s_k = CFG["edges"][0]
    t = pd.Timestamp("2021-03-29 13:00", tz=UTC)            # inside the first test block
    ctx2 = rs.build_context(perturb_from(m, t), CFG["end"])
    a, b = rs.fit_fold(ctx, CFG, s_k), rs.fit_fold(ctx2, CFG, s_k)
    upto = np.asarray(ctx.labels <= t)
    after = np.asarray(ctx.labels > t + pd.Timedelta(hours=2))
    for H in rs.HORIZONS:
        fa, fb = rs.forecasts(a, H), rs.forecasts(b, H)
        for c in rs.CANDS:
            np.testing.assert_array_equal(fa[c][upto], fb[c][upto], err_msg=f"{c} H={H}")
            # teeth: forecasts after t do move
            assert not np.allclose(fa[c][after], fb[c][after], equal_nan=True), (c, H)


def test_folds_never_train_on_their_test_window(syn):
    m, ctx = syn
    for k in range(len(CFG["edges"]) - 1):
        s_k = CFG["edges"][k]
        fit = rs.fit_fold(ctx, CFG, s_k)
        assert pd.Timestamp(fit.params.train_end) <= s_k - rs.EMBARGO
        for H in rs.HORIZONS:
            tr = fit.model[H]["train"]
            assert tr.sum() > 100
            assert (ctx.labels[tr] <= s_k - rs.EMBARGO).all()
            assert (ctx.win[H].E_ns[tr] <= s_k.value).all()
        # anything from the test start on can change: the fitted fold must not
        other = rs.fit_fold(rs.build_context(perturb_from(m, s_k), CFG["end"]), CFG, s_k)
        assert other.params.to_json() == fit.params.to_json()
        for H in rs.HORIZONS:
            for key in ("c", "half_life", "qlike_train"):
                assert other.model[H][key] == fit.model[H][key]
            np.testing.assert_array_equal(other.model[H]["beta"], fit.model[H]["beta"])


def test_seal_asserts_fire():
    idx = pd.DatetimeIndex(["2020-12-31 23:59", "2021-01-01 00:00"], tz=UTC)
    with pytest.raises(AssertionError, match="SEALED"):
        rs.assert_sealed(idx, pd.Timestamp("2021-01-01", tz=UTC), "x")
    with pytest.raises(AssertionError, match="SEALED"):
        rs.assert_sealed(pd.DatetimeIndex(["2024-01-01"], tz=UTC), pd.Timestamp("2030-01-01", tz=UTC), "x")
    with pytest.raises(AssertionError, match="SEALED"):
        rs.assert_sealed(pd.DatetimeIndex(["2026-10-01"], tz=UTC), pd.Timestamp("2030-01-01", tz=UTC), "x")
    rs.assert_sealed(pd.DatetimeIndex(["2020-12-31 23:59"], tz=UTC), pd.Timestamp("2021-01-01", tz=UTC), "x")


# ---------------------------------------------------------------- inference

def test_bh():
    got = rs.bh({"a": 0.01, "b": 0.04, "c": 0.03, "d": 0.20}, q=0.05)
    assert {k: round(v["p_adj"], 6) for k, v in got.items()} == \
        {"a": 0.04, "b": 0.053333, "c": 0.053333, "d": 0.2}
    assert [k for k, v in got.items() if v["reject"]] == ["a"]


def test_oracle_beats_plugin_and_dm_detects_it(syn):
    _, ctx = syn
    res = rs.run_period("SYN", CFG, ctx.m)
    grid = pd.date_range(ctx.grid_start, periods=len(ctx.grid_open), freq="1min")
    cv = np.concatenate([[0.0], np.cumsum(np.where(ctx.grid_open, true_var(grid), 0.0))])
    for H in rs.HORIZONS:
        g = res["rows"][H]
        k = np.searchsorted(ctx.labels.asi8, g.index.asi8)
        g = g.assign(h_o=cv[ctx.win[H].j[k]] - cv[ctx.win[H].g0[k]])
        d = rs.dm(g, "o", "a")
        assert d["mean_d"] < 0 and d["p"] < 0.01, (H, d)


def rows_for_rule(seed, b_better):
    rng = np.random.default_rng(seed)
    n = 3000
    rv = rng.lognormal(0, 1, n)
    h_a = rv * rng.lognormal(0, 0.8, n)
    h_b = rv * rng.lognormal(0, 0.3, n) if b_better else h_a.copy()
    idx = pd.date_range("2010-01-01", periods=n, freq="1h", tz=UTC)
    return pd.DataFrame({"rv": rv, "h_a": h_a, "h_b": h_b, "h_c": h_a.copy(),
                         "crossing": False}, index=idx)


def test_rule_needs_both_horizons():
    win = rs.verdict({60: rows_for_rule(1, True), 240: rows_for_rule(2, True)})
    assert win["choice"] == "b"
    one = rs.verdict({60: rows_for_rule(1, True), 240: rows_for_rule(2, False)})
    assert one["choice"] == "a" and one["tests"]["b|H60"]["significant"]
    tie = rs.verdict({60: rows_for_rule(1, False), 240: rows_for_rule(2, False)})
    assert tie["choice"] == "a"                          # ties go to the plug-in


# ---------------------------------------------------------------- step 3: model file and proposed sigma

import har_sigma as hs  # noqa: E402

MODEL = HERE / "model" / "sigma_har_v1.json"


@pytest.mark.parametrize("name", ["sigma_har_v1.json", "sigma_har_parquet_v1.json"])
def test_model_file_verifies_and_detects_tampering(tmp_path, name):
    path = HERE / "model" / name
    if not path.exists():
        pytest.skip("model file not written yet")
    d = hs.verify(path)
    model = hs.from_payload(d)
    assert model.version == 1 and set(model.horizons) == {60, 240}
    assert all(len(beta) == 4 for beta, _ in model.horizons.values())
    assert pd.Timestamp(model.params.train_end) <= pd.Timestamp("2024-01-01", tz=UTC)
    d.pop("sha256")
    d["horizons"]["60"]["beta"][1] += 1e-9
    bad = tmp_path / "bad.json"
    bad.write_text(__import__("json").dumps({**d, "sha256": hs.verify(path)["sha256"]}))
    with pytest.raises(ValueError, match="sha256"):
        hs.verify(bad)


def model_from_fit(fit):
    return hs.HarSigmaModel(version=0, sha256="", params=fit.params,
                            horizons={H: (tuple(fit.model[H]["beta"]), fit.model[H]["c"])
                                      for H in rs.HORIZONS})


def test_proposed_sigma_equals_study_har_forecast(syn):
    m, ctx = syn
    fit = rs.fit_fold(ctx, CFG, CFG["edges"][0])
    model = model_from_fit(fit)
    rng = np.random.default_rng(12345)
    for H in rs.HORIZONS:
        rows = rs.common_rows(rs.fold_rows(ctx, fit, H, CFG["edges"][1], 1, "x", "all"))
        assert rows.crossing.any() and (~rows.crossing).any()
        for t in rows.index[rng.choice(len(rows), 40, replace=False)]:
            st = vb.state_at(fit.params, m[(m.index >= t - pd.Timedelta(days=21)) & (m.index < t)], t)
            assert st.reason is None
            s = hs.sigma_har(model, st, H)
            assert s.sigma_ret ** 2 == pytest.approx(rows.at[t, "h_b"], rel=1e-9)
            assert s.crosses_close == rows.at[t, "crossing"]


def test_proposed_sigma_refuses_unfitted_horizon(syn):
    _, ctx = syn
    fit = rs.fit_fold(ctx, CFG, CFG["edges"][0])
    st = vb.VolState(pd.Timestamp("2021-03-24 10:00", tz=UTC).to_pydatetime(), None,
                     0.0, 0.0, 0.0, 1800.0, None, None)
    with pytest.raises(ValueError, match="not fitted"):
        hs.sigma_har(model_from_fit(fit), st, 120)


# ---------------------------------------------------------------- post-hoc helpers

def test_posthoc_rows_match_the_study_forecasts(syn):
    import posthoc as ph
    _, ctx = syn
    fit = rs.fit_fold(ctx, CFG, CFG["edges"][0])
    X, ok_x, P = ph.refeature(ctx, fit.params)
    np.testing.assert_array_equal(X, fit.X)
    for H in rs.HORIZONS:
        np.testing.assert_array_equal(P[H], fit.P[H])
    rows = ph.rows_from(ctx, X, ok_x, P, fit.model, CFG["edges"][0], CFG["edges"][1])
    for H in rs.HORIZONS:
        ref = rs.common_rows(rs.fold_rows(ctx, fit, H, CFG["edges"][1], 1, "x", "all"))
        ref = ref[~ref.crossing]
        assert rows[H].index.equals(ref.index)
        np.testing.assert_array_equal(rows[H].h_a, ref.h_a)
        np.testing.assert_array_equal(rows[H].h_b, ref.h_b)


def test_noncross_only_fit_drops_exactly_the_crossing_training_rows(syn):
    _, ctx = syn
    s_k = CFG["edges"][0]
    full, nc = rs.fit_fold(ctx, CFG, s_k), rs.fit_fold(ctx, CFG, s_k, noncross_only=True)
    for H in rs.HORIZONS:
        crossing = ctx.win[H].crossing
        assert (full.model[H]["train"] & crossing).any()
        np.testing.assert_array_equal(nc.model[H]["train"], full.model[H]["train"] & ~crossing)
    assert nc.params.to_json() == full.params.to_json()          # VolParams unchanged
