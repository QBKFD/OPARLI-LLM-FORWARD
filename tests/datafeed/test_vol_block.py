"""datafeed.features.vol_block: same outputs as research/vol_block, as-of-t
state, sigma over H minutes."""

import importlib.util
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from datafeed.features import vol_block as vb
from datafeed.ingest.xauusd_bars import Bar, _market_open, bar_message
from datafeed.store import schema

UTC = timezone.utc
RESEARCH = Path(__file__).resolve().parents[2] / "research"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ResearchVolBlock():
    return _load("research_vol_block", RESEARCH / "vol_block" / "vol_block.py").VolBlock


def synthetic(start="2021-02-21", end="2021-04-06", seed=7, drop=0.03):
    """1-min bars on market minutes only (crosses the 2021-03-14 US DST change),
    a few minutes missing, volatility with a time-of-day shape and slow drift."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="1min", tz="UTC", inclusive="left")
    idx = idx[[_market_open(t.to_pydatetime()) for t in idx]]
    idx = idx[rng.random(len(idx)) > drop]
    hour = idx.hour.to_numpy()
    vol = 2e-4 * (1 + 0.8 * np.sin(2 * np.pi * hour / 24) ** 2) \
        * np.exp(0.3 * np.sin(np.arange(len(idx)) / 5000))
    close = 1800 * np.exp(np.cumsum(rng.normal(0, 1, len(idx)) * vol))
    return pd.DataFrame({"open": close, "high": close * 1.0001, "low": close * 0.9999,
                         "close": close}, index=idx)


@pytest.fixture(scope="module")
def syn():
    m = synthetic()
    train = m[m.index < pd.Timestamp("2021-03-15", tz="UTC")]
    return m, train, vb.fit(train)


def assert_same_as_research(params, research, m, bar_times=None):
    np.testing.assert_array_equal(np.asarray(params.profile), research.profile_)
    np.testing.assert_array_equal(np.asarray(params.s_hour_mean),
                                  research.s_hour_mean_.reindex(range(24)).to_numpy(float))
    assert pd.Timestamp(params.train_start) == research.train_start_
    assert pd.Timestamp(params.train_end) == research.train_end_
    got, ref = vb.transform(params, m, bar_times), research.transform(m, bar_times)
    pd.testing.assert_index_equal(got.index, ref.index)
    for c in ["L", "S", "S_hd", "T"]:          # bit-identical, NaN in the same places
        np.testing.assert_array_equal(got[c].to_numpy(float), ref[c].to_numpy(float), err_msg=c)
    return got


# ---------------------------------------------------------------- equality with research

def test_matches_research_on_research_data(ResearchVolBlock):
    pytest.importorskip("scipy")                       # research loader dependency
    r1 = _load("research_run_study", RESEARCH / "vol_features" / "run_study.py")
    if not r1.DATA.exists():
        pytest.skip("research parquet not available")
    m, _ = r1.load_minutes(start=pd.Timestamp("2020-12-14", tz="UTC"),
                           end=pd.Timestamp("2021-06-01", tz="UTC"))
    # the training window of research/vol_block/test_vol_block.py
    t0 = m.index[m.index >= pd.Timestamp("2021-01-03 22:00", tz="UTC")][0]
    train = m[(m.index >= t0) & (m.index < pd.Timestamp("2021-03-01", tz="UTC"))]
    params, research = vb.fit(train), ResearchVolBlock().fit(train)
    got = assert_same_as_research(params, research, m)
    assert got[["L", "S_hd", "T"]].notna().sum().min() > 2000
    hourly = pd.date_range("2021-01-01", "2021-06-01", freq="1h", tz="UTC")
    assert_same_as_research(params, research, m, hourly)


def test_matches_research_on_synthetic_data(ResearchVolBlock, syn):
    m, train, params = syn
    got = assert_same_as_research(params, ResearchVolBlock().fit(train), m)
    assert got[["L", "S_hd", "T"]].notna().sum().min() > 500


def test_params_json_round_trip_is_exact(syn):
    _, _, p = syn
    q = vb.VolParams.from_json(p.to_json())
    assert q.profile == p.profile                                  # exact floats
    np.testing.assert_array_equal(q.s_hour_mean, p.s_hour_mean)    # NaN-aware
    assert (q.train_start, q.train_end) == (p.train_start, p.train_end)
    assert q.to_json() == p.to_json()


def test_fit_refuses_sealed_training_window():
    m = synthetic("2026-09-14", "2026-10-02", seed=1)
    with pytest.raises(ValueError, match="seal"):
        vb.fit(m)
    ok = vb.fit(m[m.index < pd.Timestamp("2026-09-30 21:00", tz="UTC")])
    with pytest.raises(ValueError, match="seal"):
        vb.VolParams(ok.profile, ok.s_hour_mean, ok.train_start,
                     datetime(2026, 10, 1, 0, 1, tzinfo=UTC))


def test_input_validation():
    idx = pd.date_range("2021-01-04", periods=10, freq="1min")          # naive
    with pytest.raises(ValueError):
        vb.fit(pd.DataFrame({"close": 1.0}, index=idx))
    with pytest.raises(ValueError, match="UTC"):
        vb.fit(pd.DataFrame({"close": 1.0}, index=idx.tz_localize("America/New_York")))
    with pytest.raises(ValueError):
        vb.fit(pd.DataFrame({"close": []}, index=pd.DatetimeIndex([], tz="UTC")))


# ---------------------------------------------------------------- as of t

def test_state_at_equals_full_transform_at_labels(syn):
    m, _, params = syn
    full = vb.transform(params, m)
    ok = full.index[full[["L", "S_hd", "T"]].notna().all(axis=1)]
    rng = np.random.default_rng(3)
    for t in pd.DatetimeIndex(rng.choice(ok, 20, replace=False)):
        s = vb.state_at(params, m, t)
        assert s.reason is None and pd.Timestamp(s.label) == t
        assert (s.L, s.S_hd, s.T) == tuple(full.loc[t, ["L", "S_hd", "T"]])
        assert s.close_ref == m["close"][m.index < t].iat[-1]


def test_state_at_mid_hour_uses_previous_label(syn):
    m, _, params = syn
    t = pd.Timestamp("2021-03-24 10:37:30", tz="UTC")
    s = vb.state_at(params, m, t)
    assert pd.Timestamp(s.label) == pd.Timestamp("2021-03-24 10:00", tz="UTC")
    assert s.close_ref == m["close"][m.index + pd.Timedelta(minutes=1) <= t].iat[-1]
    assert pd.Timestamp(s.last_bar_ts) <= t - pd.Timedelta(minutes=1)


def test_state_reasons(syn):
    m, _, params = syn
    assert vb.state_at(params, m, "2021-02-01T00:00:00+00:00").reason == "no_bars_before_t"
    early = vb.state_at(params, m, pd.Timestamp("2021-02-24 12:00", tz="UTC"))
    assert early.reason == "insufficient_history" and math.isnan(early.L)
    with pytest.raises(ValueError, match="naive"):
        vb.state_at(params, m, datetime(2021, 3, 24, 10))


def write_replay(path, m):
    con = schema.connect(path)
    con.execute("BEGIN")
    for row in m.itertuples():
        b = Bar("XAUUSD", row.Index.to_pydatetime(), row.open, row.high, row.low, row.close, None)
        schema.insert_bar(con, bar_message(b, source="replay", history=False,
                                           ingest_ts=b.bar_ts + timedelta(minutes=1)))
    con.execute("COMMIT")
    return con


def test_read_state_from_store(tmp_path, syn):
    m, _, params = syn
    path = str(tmp_path / "store.sqlite")
    write_replay(path, m).close()
    for t in pd.date_range("2021-03-22 08:00", periods=6, freq="7h", tz="UTC"):
        s_db = vb.read_state(params, "XAUUSD", t, db=path, kinds=("replay",))
        s_mem = vb.state_at(params, m, t)
        assert s_db.reason is None and s_db.label == s_mem.label
        assert s_db.close_ref == s_mem.close_ref
        # the store read starts 14 days back, so the running sums start elsewhere
        np.testing.assert_allclose([s_db.L, s_db.S_hd, s_db.T], [s_mem.L, s_mem.S_hd, s_mem.T],
                                   rtol=1e-12, atol=1e-12)


# ---------------------------------------------------------------- sigma

def flat_state(t, L=math.log(2.0), close=2000.0):
    return vb.VolState(pd.Timestamp(t).to_pydatetime(), None, L, 0.0, 0.0, close, None, None)


def flat_params(p=1e-8):
    return vb.VolParams(profile=(p,) * vb.N_BUCKETS, s_hour_mean=(0.0,) * 24,
                        train_start=datetime(2021, 1, 4, tzinfo=UTC),
                        train_end=datetime(2021, 3, 1, tzinfo=UTC))


def test_sigma_hand_computed_inside_market_hours():
    s = vb.sigma_over(flat_params(1e-8), flat_state("2021-03-24 10:00+00:00"), 240)
    assert (s.H, s.open_minutes, s.crosses_close) == (240, 240, False)
    assert s.sigma_ret == pytest.approx(math.sqrt(2.0 * 1e-8 * 240), rel=1e-12)
    assert s.sigma_px == pytest.approx(2000.0 * s.sigma_ret, rel=1e-15)


def test_sigma_uses_the_profile_of_each_minute():
    prof = np.full(vb.N_BUCKETS, 1e-8)
    prof[288 + (10 * 60) // 5] = 5e-8          # DST bucket of 10:00-10:04 UTC
    params = vb.VolParams(tuple(prof), (0.0,) * 24, datetime(2021, 1, 4, tzinfo=UTC),
                          datetime(2021, 3, 1, tzinfo=UTC))
    s = vb.sigma_over(params, flat_state("2021-06-15 10:00+00:00", L=0.0), 10)
    assert s.sigma_ret ** 2 == pytest.approx(5 * 5e-8 + 5 * 1e-8, rel=1e-12)


def test_sigma_daily_break_and_weekend_count_zero():
    p = flat_params()
    # 2021-03-24 is EDT: the break is 17:00-18:00 NY = 21:00-22:00 UTC
    s = vb.sigma_over(p, flat_state("2021-03-24 20:00+00:00"), 180)
    assert (s.open_minutes, s.crosses_close) == (120, True)
    sat = vb.sigma_over(p, flat_state("2021-03-27 12:00+00:00"), 60)
    assert (sat.open_minutes, sat.sigma_ret, sat.crosses_close) == (0, 0.0, True)


def test_sigma_mid_minute_starts_at_next_minute():
    p = flat_params()
    s = vb.sigma_over(p, flat_state("2021-03-24 20:58:30+00:00"), 3)
    assert s.open_minutes == 1                 # 20:59 open; 21:00, 21:01 in the break


def test_sigma_input_errors():
    p, st = flat_params(), flat_state("2021-03-24 10:00+00:00")
    for H in (0, -5, 1.5, True, None):
        with pytest.raises(ValueError):
            vb.sigma_over(p, st, H)
    bad = vb.VolState(st.t, None, math.nan, math.nan, math.nan, 2000.0, None, "insufficient_history")
    with pytest.raises(ValueError, match="insufficient_history"):
        vb.sigma_over(p, bad, 60)


def test_sigma_from_real_state(syn):
    m, _, params = syn
    st = vb.state_at(params, m, pd.Timestamp("2021-03-24 10:00", tz="UTC"))
    s = vb.sigma_over(params, st, 60)
    assert s.sigma_ret > 0 and s.sigma_px == st.close_ref * s.sigma_ret and not s.crosses_close
