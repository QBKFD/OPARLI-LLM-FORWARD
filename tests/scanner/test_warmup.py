"""Step 2: stream-mode warm-up from the datafeed store, IBKR history as fallback."""

import pandas as pd
import pytest

import deterministic_scanner as ds

from scanner_helpers import Log, fill_store

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


NOW = T("2025-06-18 12:00:30")    # Wednesday


def test_full_store_is_used_and_live_preferred(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW)
    fill_store(db, mins, kind="history")
    fill_store(db, mins[-30:], kind="live", price=3500.0)
    hist, detail = ds.stream_warmup(NOW, db=str(db))
    assert list(hist.index) == list(mins)
    assert list(hist.columns) == ["open", "high", "low", "close"]
    assert (hist["close"].iloc[-30:] >= 3500).all() and (hist["close"].iloc[:-30] < 3500).all()
    assert "history" in detail and "live 30" in detail
    assert hist.index[-1] == T("2025-06-18 11:59")       # the forming 12:00 minute is excluded


def test_invalid_store_bars_are_dropped(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW)
    fill_store(db, mins[:-1])
    fill_store(db, mins[-1:], price=-1.0)                # a -1 "no quote" bar
    hist, _ = ds.stream_warmup(NOW, db=str(db))
    assert hist.index[-1] == mins[-2]


def test_no_store_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("DATAFEED_DB", raising=False)
    with pytest.raises(ds.StoreNotReady, match="store unavailable"):
        ds.stream_warmup(NOW, db=None)
    with pytest.raises(ds.StoreNotReady, match="store unavailable"):
        ds.stream_warmup(NOW, db=str(tmp_path / "missing.sqlite"))


def test_stale_store_is_not_ready(tmp_path):
    db = tmp_path / "store.sqlite"
    fill_store(db, ds._open_minutes(ds.warmup_start(NOW), NOW - pd.Timedelta(minutes=20)))
    with pytest.raises(ds.StoreNotReady, match="store stale"):
        ds.stream_warmup(NOW, db=str(db))


def test_store_within_staleness_is_used(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW - pd.Timedelta(minutes=ds.WARMUP_MAX_STALE_MIN))
    fill_store(db, mins)
    hist, _ = ds.stream_warmup(NOW, db=str(db))
    assert len(hist) == len(mins)


def test_weekend_and_break_do_not_count_as_stale(tmp_path):
    now = T("2025-06-22 22:03:10")          # Sunday, 3 minutes after the 18:00 ET reopen
    db = tmp_path / "store.sqlite"
    fill_store(db, ds._open_minutes(ds.warmup_start(now), T("2025-06-20 21:00")))  # last bar Fri 20:59
    _, detail = ds.stream_warmup(now, db=str(db))
    assert "newest bar 3 market-open min old" in detail


def test_thin_store_is_not_ready(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW)
    fill_store(db, [m for i, m in enumerate(mins) if i % 10 < 7 or i >= len(mins) - 5])   # ~70%
    with pytest.raises(ds.StoreNotReady, match="store too thin"):
        ds.stream_warmup(NOW, db=str(db))


def test_replay_bars_are_not_a_fallback(tmp_path):
    db = tmp_path / "store.sqlite"
    fill_store(db, ds._open_minutes(ds.warmup_start(NOW), NOW), kind="replay")
    with pytest.raises(ds.StoreNotReady, match="no bars"):
        ds.stream_warmup(NOW, db=str(db))
    hist, _ = ds.stream_warmup(NOW, db=str(db), kinds=("replay",))
    assert len(hist) > 10_000


def test_stream_mode_never_connects_to_ibkr():
    """Owner decision: in stream mode the feed is the only gateway client."""
    import inspect
    for fn in (ds.run_live_stream, ds.stream_warmup, ds.wait_for_store):
        src = inspect.getsource(fn)
        for name in ("_load_recent_history", "ib_insync", "TWSConnector", "IB_HOST", "IB_PORT"):
            assert name not in src, (fn.__name__, name)


# ── wait_for_store ──────────────────────────────────────────────────────────
class Clock:
    def __init__(self, t):
        self.t = T(t)

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += pd.Timedelta(seconds=s)


def test_wait_returns_at_once_when_ready(tmp_path):
    db = tmp_path / "store.sqlite"
    fill_store(db, ds._open_minutes(ds.warmup_start(NOW), NOW))
    clock, sent = Clock("2025-06-18 12:00:30"), []
    hist, now, detail = ds.wait_for_store(sent.append, db=str(db), clock=clock, sleep=clock.sleep,
                                          log=Log())
    assert now == NOW and len(hist) > 10_000 and sent == []


def test_wait_retries_until_the_store_is_usable(tmp_path):
    db = tmp_path / "store.sqlite"
    mins = ds._open_minutes(ds.warmup_start(NOW), NOW + pd.Timedelta(minutes=30))
    fill_store(db, mins[mins < NOW - pd.Timedelta(minutes=20)])        # stale at first
    clock, sent, log = Clock("2025-06-18 12:00:30"), [], Log()
    calls = {"n": 0}

    def sleep(s):
        calls["n"] += 1
        clock.sleep(s)
        if calls["n"] == 4:                                             # the recorder catches up
            fill_store(db, mins[(mins >= NOW - pd.Timedelta(minutes=20)) & (mins < clock() - pd.Timedelta(minutes=1))])

    hist, now, _ = ds.wait_for_store(sent.append, db=str(db), clock=clock, sleep=sleep, log=log)
    assert calls["n"] == 4 and now == NOW + pd.Timedelta(seconds=4 * ds.STORE_RETRY_S)
    assert sent == []                                                  # 2 minutes < STREAM_STALE_MIN
    assert len(log.having("scanner not started, retrying in 30s")) == 4


def test_wait_sends_one_message_then_one_on_recovery(tmp_path):
    db = tmp_path / "store.sqlite"
    clock, sent, log = Clock("2025-06-18 12:00:30"), [], Log()
    calls = {"n": 0}

    def sleep(s):
        calls["n"] += 1
        clock.sleep(s)
        if calls["n"] == 60:                                           # 30 minutes later
            fill_store(db, ds._open_minutes(ds.warmup_start(clock()), clock()))

    _, now, _ = ds.wait_for_store(sent.append, db=str(db), clock=clock, sleep=sleep, log=log)
    assert len(sent) == 2
    assert "unusable for 10 market-open minutes" in sent[0] and "store unavailable" in sent[0]
    assert sent[1].startswith("scanner: datafeed store usable again")


def test_wait_counts_market_open_minutes_only(tmp_path):
    db = tmp_path / "store.sqlite"                                     # never filled
    clock, sent = Clock("2025-06-21 12:00"), []                       # Saturday
    calls = {"n": 0}

    def sleep(s):
        calls["n"] += 1
        clock.sleep(s)
        if clock() >= T("2025-06-22 22:09"):                           # Sunday, 9 open minutes in
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ds.wait_for_store(sent.append, db=str(db), clock=clock, sleep=sleep, log=Log())
    assert sent == []                                                  # a whole weekend, 9 open minutes
