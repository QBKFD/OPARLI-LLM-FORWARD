"""Step 2: stream-mode warm-up from the datafeed store, IBKR history as fallback."""

import pandas as pd
import pytest

import deterministic_scanner as ds

from scanner_helpers import fill_store

T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


def test_open_minutes_is_market_open_vectorised():
    # both 2025 US DST changes, a weekend and the daily breaks
    for a, b in (("2025-03-07", "2025-03-12"), ("2025-10-31", "2025-11-05")):
        got = set(ds._open_minutes(T(a), T(b)))
        want = {m for m in pd.date_range(T(a), T(b), freq="1min", inclusive="left")
                if ds._market_open(m)}
        assert got == want


def test_open_minutes_counts_only_complete_minutes():
    # Mon 10:00 .. 10:03:30 -> 10:00, 10:01, 10:02 are complete, 10:03 is forming
    assert list(ds._open_minutes(T("2025-06-16 10:00"), T("2025-06-16 10:03:30"))) == \
        [T("2025-06-16 10:00"), T("2025-06-16 10:01"), T("2025-06-16 10:02")]
    assert len(ds._open_minutes(T("2025-06-16 10:00:20"), T("2025-06-16 10:02"))) == 1   # 10:01


@pytest.mark.parametrize("now, want", [
    ("2025-06-18 12:00", "2025-06-08 12:00"),   # Wed: 10 days already covers last week
    ("2025-07-04 12:00", "2025-06-23 00:00"),   # Fri: back to the previous ISO week's Monday
    ("2025-03-09 22:00", "2025-02-24 00:00"),   # Sun
    ("2025-06-19 00:00", "2025-06-09 00:00"),   # Thu 00:00: both rules agree
])
def test_warmup_start(now, want):
    assert ds.warmup_start(T(now)) == T(want)


def _ibkr(bars):
    def load():
        load.calls += 1
        return pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close"]
                            ).set_index("timestamp")
    load.calls = 0
    return load


def _ibkr_bars(now):
    # last two complete minutes + the forming one + a -1 "no quote" bar
    m = now.floor("min")
    return [(m - pd.Timedelta(minutes=3), 3400, 3401, -1.0, -1.0),
            (m - pd.Timedelta(minutes=2), 3400, 3401, 3399, 3400),
            (m - pd.Timedelta(minutes=1), 3400, 3401, 3399, 3400.5),
            (m, 3400.5, 3400.5, 3400.5, 3400.5)]


NOW = T("2025-06-18 12:00:30")    # Wednesday


def test_full_store_is_used_and_live_preferred(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW)
    fill_store(db, mins, kind="history")
    fill_store(db, mins[-30:], kind="live", price=3500.0)
    ibkr = _ibkr([])
    hist, path, detail = ds.stream_warmup(NOW, db=str(db), ibkr_history=ibkr)
    assert path == "store" and ibkr.calls == 0
    assert list(hist.index) == list(mins)
    assert list(hist.columns) == ["open", "high", "low", "close"]
    assert (hist["close"].iloc[-30:] >= 3500).all() and (hist["close"].iloc[:-30] < 3500).all()
    assert "history" in detail and "live 30" in detail
    assert hist.index[-1] == T("2025-06-18 11:59")       # the forming 12:00 minute is excluded


def test_no_store_falls_back_to_ibkr(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAFEED_DB", raising=False)
    ibkr = _ibkr(_ibkr_bars(NOW))
    hist, path, detail = ds.stream_warmup(NOW, db=None, ibkr_history=ibkr)
    assert path == "ibkr" and ibkr.calls == 1
    assert "store unavailable" in detail
    # -1 bar filtered, forming minute dropped
    assert list(hist.index) == [T("2025-06-18 11:58"), T("2025-06-18 11:59")]
    hist, path, _ = ds.stream_warmup(NOW, db=str(tmp_path / "missing.sqlite"), ibkr_history=ibkr)
    assert path == "ibkr"


def test_stale_store_falls_back(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW - pd.Timedelta(minutes=20))
    fill_store(db, mins)
    hist, path, detail = ds.stream_warmup(NOW, db=str(db), ibkr_history=_ibkr(_ibkr_bars(NOW)))
    assert path == "ibkr" and "store stale" in detail


def test_store_within_staleness_is_used(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW - pd.Timedelta(minutes=ds.WARMUP_MAX_STALE_MIN))
    fill_store(db, mins)
    _, path, _ = ds.stream_warmup(NOW, db=str(db), ibkr_history=_ibkr([]))
    assert path == "store"


def test_weekend_and_break_do_not_count_as_stale(tmp_path):
    now = T("2025-06-22 22:03:10")          # Sunday, 3 minutes after the 18:00 ET reopen
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(now), T("2025-06-20 21:00"))   # last bar Fri 20:59
    fill_store(db, mins)
    _, path, detail = ds.stream_warmup(now, db=str(db), ibkr_history=_ibkr([]))
    assert path == "store", detail
    assert "newest bar 3 market-open min old" in detail


def test_thin_store_falls_back(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW)
    fill_store(db, [m for i, m in enumerate(mins) if i % 10 < 7 or i >= len(mins) - 5])   # ~70%
    _, path, detail = ds.stream_warmup(NOW, db=str(db), ibkr_history=_ibkr(_ibkr_bars(NOW)))
    assert path == "ibkr" and "store too thin" in detail


def test_replay_bars_are_not_a_fallback(tmp_path):
    db = tmp_path / "store.sqlite"
    fill_store(db, ds._open_minutes(ds.warmup_start(NOW), NOW), kind="replay")
    _, path, detail = ds.stream_warmup(NOW, db=str(db), ibkr_history=_ibkr(_ibkr_bars(NOW)))
    assert path == "ibkr" and "no bars" in detail
    hist, path, _ = ds.stream_warmup(NOW, db=str(db), kinds=("replay",), ibkr_history=_ibkr([]))
    assert path == "store" and len(hist) > 10_000


def test_unusable_store_without_fallback_raises(tmp_path):
    with pytest.raises(RuntimeError, match="no IBKR fallback"):
        ds.stream_warmup(NOW, db=str(tmp_path / "missing.sqlite"))


def test_ibkr_fallback_failure_propagates(tmp_path):
    def boom():
        raise ConnectionError("gateway down")
    with pytest.raises(ConnectionError):
        ds.stream_warmup(NOW, db=str(tmp_path / "missing.sqlite"), ibkr_history=boom)
