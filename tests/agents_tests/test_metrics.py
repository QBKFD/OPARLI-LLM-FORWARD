"""agents.technical.metrics and agents.features: the metric table, its
Wilder/EMA arithmetic against reference values, complete candles only, never
imputed; and the snapshot features (σ, L, S_hd, T)."""

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.config import SnapshotConfig
from agents.features import features_from_rows, sigma_model_sha256
from agents.schemas import Bar, Level, Snapshot
from agents.snapshot import build_snapshot
from agents.technical.metrics import NO_VOLUME, ema, metric, metric_table, wilder_atr, wilder_rsi

UTC = timezone.utc
T0 = datetime(2025, 6, 10, 0, 0, tzinfo=UTC)
PARQUET = Path(__file__).resolve().parents[2] / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
# The StockCharts RSI(14) worked example. Its table shows 70.53, 66.32, 66.55,
# 69.41, 66.36, 57.97 because it rounds the averages to 2 decimals; the exact
# Wilder values are below.
RSI_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61, 46.28,
              46.28, 46.00, 46.03, 46.41, 46.22, 45.64]
RSI_PUBLISHED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]


def bars(closes, tf="1h", spread=1.0, complete=True, start=T0):
    step = {"15m": 15, "1h": 60, "4h": 240, "1m": 1}[tf]
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Bar(ts=start + timedelta(minutes=step * i), open=prev, high=max(prev, c) + spread / 2,
                       low=min(prev, c) - spread / 2, close=c, complete=complete))
        prev = c
    return out


# ---- the arithmetic

def test_wilder_rsi_against_the_reference_example():
    got = [wilder_rsi(RSI_CLOSES[:k]) for k in range(15, 21)]
    assert [round(x, 2) for x in got] == [70.46, 66.25, 66.48, 69.35, 66.29, 57.92]
    assert all(abs(a - b) < 0.1 for a, b in zip(got, RSI_PUBLISHED))


def test_wilder_rsi_edges():
    assert wilder_rsi([1.0 + i for i in range(15)]) == 100.0                 # no losses
    assert wilder_rsi([20.0 - i for i in range(15)]) == 0.0                  # no gains
    assert wilder_rsi([5.0] * 15) == "flat: no gains and no losses"
    assert wilder_rsi([1.0] * 14) == "too few complete candles (14, need 15)"


def test_wilder_atr():
    flat = bars([100.0] * 20, spread=2.0)                                      # every TR is 2
    assert wilder_atr(flat) == pytest.approx(2.0)
    b = bars([10.0, 11.0, 13.0, 12.0, 15.0], spread=0.0)                       # n = 3 by hand
    tr = [1.0, 2.0, 1.0, 3.0]                                                  # |H - L| and the gaps
    want = (sum(tr[:3]) / 3 * 2 + tr[3]) / 3
    assert wilder_atr(b, n=3) == pytest.approx(want)
    assert wilder_atr(flat[:14]).startswith("too few complete candles (14, need 15)")


def test_ema():
    assert ema([1.0, 2.0, 3.0], 3) == pytest.approx(2.0)                       # the seed is the mean
    assert ema([1.0, 2.0, 3.0, 7.0], 3) == pytest.approx(0.5 * 7 + 0.5 * 2.0)  # alpha = 2 / 4
    assert ema([1.0], 3) == "too few complete candles (1, need 3)"


# ---- the table

FEATS = {"sigma_px_60": 2.0, "sigma_px_240": 4.0, "L": -9.1, "S_hd": 0.2, "T": -0.1}


def table(closes=None, feats=FEATS, reason=None, levels=(), forming=None):
    series = bars(closes or [100 + 0.5 * i for i in range(40)])
    if forming is not None:
        series.append(Bar(ts=series[-1].ts + timedelta(hours=1), open=1, high=1e6, low=1, close=forming,
                          complete=False))
    b = {"15m": bars([100 + 0.1 * i for i in range(40)], "15m"), "1h": series, "4h": bars([100.0] * 10, "4h"),
         "1m": bars([101.0, 102.0], "1m")}
    return metric_table(b, feats, reason, levels)


def test_the_names_and_their_order():
    names = [r.name for r in table(levels=(Level(id="pdh", type="PDH", value=96.0, active_from=T0),))]
    per_tf = [n for tf in ("15m", "1h", "4h")
              for n in (f"atr14_{tf}", f"atr14_{tf}_sigma", f"rsi14_{tf}", f"ema_spread_{tf}")]
    assert names == per_tf + ["sigma_px_60", "sigma_px_240", "L", "S_hd", "T", "dist_sigma_pdh", "volume", "vwap"]


def test_values_units_and_sigma_scaling():
    rows = table(levels=(Level(id="pdh", type="PDH", value=96.0, active_from=T0),))
    atr = metric(rows, "atr14_1h")
    assert atr.available and atr.unit == "$"
    assert metric(rows, "atr14_1h_sigma").value == pytest.approx(atr.value / 2.0)
    assert metric(rows, "rsi14_1h").value == 100.0                              # a steady climb
    assert metric(rows, "ema_spread_1h").value > 0 and metric(rows, "ema_spread_1h").unit == "sigma"
    assert metric(rows, "dist_sigma_pdh").value == pytest.approx((102.0 - 96.0) / 4.0)   # last 1m close
    assert (metric(rows, "L").value, metric(rows, "sigma_px_240").value) == (-9.1, 4.0)


def test_too_few_candles_and_flat_series_are_unavailable_not_imputed():
    rows = table()
    for name, why in (("atr14_4h", "too few complete candles (10, need 15)"),
                      ("rsi14_4h", "too few complete candles (10, need 15)"),
                      ("ema_spread_4h", "too few complete candles (10, need 21)")):
        r = metric(rows, name)
        assert (r.available, r.value, r.reason) == (False, None, why)


def test_no_features_leaves_the_price_metrics():
    rows = table(feats={}, reason="vol state: insufficient_history")
    assert metric(rows, "atr14_1h").available and metric(rows, "rsi14_1h").available
    for name in ("atr14_1h_sigma", "ema_spread_1h", "sigma_px_60", "L", "T"):
        r = metric(rows, name)
        assert not r.available and r.reason == "no features: vol state: insufficient_history"
    assert metric(table(feats={"sigma_px_60": 2.0}), "L").reason == "feature L missing"


def test_volume_and_vwap_are_always_unavailable():
    for name in ("volume", "vwap"):
        r = metric(table(), name)
        assert (r.available, r.value, r.reason) == (False, None, NO_VOLUME)


def test_the_forming_candle_never_feeds_a_metric():
    assert table(forming=1e5) == table()


def test_scale_invariance():
    """Prices × k: ATR × k, RSI the same, σ-relative metrics the same when σ × k."""
    k = 10.0
    a = table()
    closes = [(100 + 0.5 * i) * k for i in range(40)]
    b = metric_table({"1h": bars(closes, spread=k)}, {n: v * k if n.startswith("sigma") else v
                                                      for n, v in FEATS.items()}, None, ())
    assert metric(b, "atr14_1h").value == pytest.approx(metric(a, "atr14_1h").value * k)
    for name in ("rsi14_1h", "atr14_1h_sigma", "ema_spread_1h"):
        assert metric(b, name).value == pytest.approx(metric(a, name).value), name


def test_missing_timeframes_are_named():
    rows = metric_table({}, FEATS, None, ())
    assert metric(rows, "atr14_15m").reason == "no 15m bars in the snapshot"
    assert metric(rows, "rsi14_4h").reason == "no 4h bars in the snapshot"


# ---- features

def test_features_without_a_model_say_so():
    f = features_from_rows([], T0, None)
    assert (f.values, f.reason) == ({}, "no sigma model configured (snapshot.sigma_model)")


def test_features_with_an_unknown_model_say_so():
    f = features_from_rows([], T0, "best_model")
    assert f.reason.startswith("sigma model 'best_model': ValueError: unknown sigma model")


def test_features_with_too_little_history_keep_the_model(replay_db):
    s = build_snapshot(datetime(2025, 6, 11, 14, 30, tzinfo=UTC), SnapshotConfig(mode="replay",
                       sigma_model="parquet2021_2023"), replay_db)
    assert s.features == {} and s.unavailable["features"] == "vol state: insufficient_history"
    assert s.sigma_model == "parquet2021_2023" and s.sigma_model_sha256 == sigma_model_sha256("parquet2021_2023")
    assert metric(s.metrics, "rsi14_15m").available and not metric(s.metrics, "sigma_px_60").available


def test_sigma_model_sha256():
    assert sigma_model_sha256(None) is None and sigma_model_sha256("nope") is None
    assert len(sigma_model_sha256("parquet2021_2023")) == 64


@pytest.fixture(scope="module")
def dev_db(tmp_path_factory):
    """Two development-period weeks of real bars (June 2020) through the replay path."""
    if not PARQUET.exists():
        pytest.skip("historical parquet not present (untracked)")
    from datafeed.bus import InMemoryBus
    from datafeed.ingest.xauusd_bars import STREAM, ReplaySource
    from datafeed.recorder.recorder import Recorder
    from datafeed.store import schema

    bus = InMemoryBus()
    ReplaySource(str(PARQUET), start="2020-06-01", end="2020-06-17").run(lambda m: bus.publish(STREAM, m))
    db = str(tmp_path_factory.mktemp("dev") / "datafeed.sqlite")
    con = schema.connect(db)
    Recorder(bus, con, STREAM, block_ms=None, sleep=lambda s: None).run(stop_when_idle=True)
    con.close()
    return db


def test_real_bars_give_features_and_a_full_table(dev_db):
    t = datetime(2020, 6, 16, 14, 30, tzinfo=UTC)
    t0 = time.perf_counter()
    s = build_snapshot(t, SnapshotConfig(mode="replay", sigma_model="parquet2021_2023"), dev_db)
    elapsed = time.perf_counter() - t0
    assert "features" not in s.unavailable and set(s.features) >= {"sigma_px_60", "sigma_px_240", "L", "S_hd", "T"}
    assert 0 < s.features["sigma_px_60"] < s.features["sigma_px_240"]                  # σ grows with the horizon
    avail = {r.name for r in s.metrics if r.available}
    assert {f"{m}_{tf}" for m in ("atr14", "rsi14", "ema_spread") for tf in ("15m", "1h", "4h")} <= avail
    assert {"atr14_15m_sigma", "sigma_px_60", "sigma_px_240", "L", "S_hd", "T"} <= avail
    assert {"volume", "vwap"}.isdisjoint(avail)
    assert elapsed < 10, f"snapshot with features took {elapsed:.1f}s"
    assert isinstance(s, Snapshot)
