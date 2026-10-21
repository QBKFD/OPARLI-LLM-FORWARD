"""Step 4: one Telegram message after STREAM_STALE_MIN market-open minutes
without a new live bar, one recovery message when bars resume."""

import pandas as pd
import pytest

import deterministic_scanner as ds
from backend.datafeed.bus import InMemoryBus
from backend.datafeed.ingest.xauusd_bars import STREAM

from tests.scanner.scanner_helpers import FakeLive, Log, bar_msg

T = lambda s: pd.Timestamp(s, tz="UTC")   # noqa: E731


class Clock:
    def __init__(self, t):
        self.t = T(t)

    def __call__(self):
        return self.t

    def set(self, t):
        self.t = T(t)


def _bar(ts, **kw):
    return bar_msg(ts, 3400, 3401, 3399, 3400.5, **kw)


def _setup(start, last_bar_ts=None):
    bus, sent, clock = InMemoryBus(), [], Clock(start)
    st = ds.StreamStaleness(sent.append, started=clock())
    feed = ds.StreamFeed(bus, STREAM, "scanner-t", FakeLive(), lambda a: None, block_ms=None,
                         log=Log(), staleness=st, clock=clock,
                         last_bar_ts=None if last_bar_ts is None else T(last_bar_ts))
    return bus, feed, sent, clock


def test_constant_is_named():
    assert ds.STREAM_STALE_MIN == 10


def test_one_alert_then_one_recovery():
    bus, feed, sent, clock = _setup("2025-06-16 10:00:30")
    bus.publish(STREAM, _bar("2025-06-16T10:00:00"))
    feed.step()
    clock.set("2025-06-16 10:10:59")          # 10:01..10:09 complete: 9 minutes
    feed.step()
    assert sent == []
    clock.set("2025-06-16 10:11:00")          # 10:01..10:10: 10 minutes
    feed.step()
    assert len(sent) == 1 and "10 market-open minutes" in sent[0]
    assert "last bar 2025-06-16 10:00:00+00:00" in sent[0]
    for t in ("2025-06-16 10:20", "2025-06-16 11:30"):
        clock.set(t)
        feed.step()
    assert len(sent) == 1                     # only one alert
    bus.publish(STREAM, _bar("2025-06-16T11:31:00"))
    clock.set("2025-06-16 11:32:03")
    feed.step()
    assert len(sent) == 2 and "resumed (bar 2025-06-16 11:31:00+00:00)" in sent[1]
    assert "after 90 market-open minutes" in sent[1]
    bus.publish(STREAM, _bar("2025-06-16T11:32:00"))
    feed.step()
    assert len(sent) == 2                     # one recovery only


def test_alerts_again_after_a_second_outage():
    bus, feed, sent, clock = _setup("2025-06-16 10:00:30", last_bar_ts="2025-06-16 10:00")
    clock.set("2025-06-16 10:11")
    feed.step()
    bus.publish(STREAM, _bar("2025-06-16T10:12:00"))
    clock.set("2025-06-16 10:13")
    feed.step()
    clock.set("2025-06-16 10:23")
    feed.step()
    assert len(sent) == 3 and "no new" in sent[2]


@pytest.mark.parametrize("last, now, alert", [
    ("2025-06-20 20:59", "2025-06-22 22:09:59", False),   # weekend + 22:00..22:08 = 9
    ("2025-06-20 20:59", "2025-06-22 22:10:00", True),    # 22:00..22:09 = 10
    ("2025-06-16 20:59", "2025-06-16 22:05", False),      # summer daily break doesn't count
    ("2025-01-13 21:59", "2025-01-13 23:09:30", False),   # winter break (22-23 UTC): 23:00..23:08
    ("2025-01-13 21:59", "2025-01-13 23:10", True),
    ("2025-06-16 20:50", "2025-06-16 22:01", True),       # 20:51..20:59 (9) + 22:00 (1)
])
def test_only_market_open_minutes_count(last, now, alert):
    _, feed, sent, clock = _setup(last, last_bar_ts=last)
    clock.set(now)
    feed.step()
    assert bool(sent) is alert


def test_skipped_entries_do_not_reset_it():
    bus, feed, sent, clock = _setup("2025-06-16 10:00:30", last_bar_ts="2025-06-16 10:00")
    bus.publish(STREAM, _bar("2025-06-16T10:09:00", history=True))
    bus.publish(STREAM, _bar("2025-06-16T10:09:00", source="replay"))
    bus.publish(STREAM, _bar("2025-06-16T10:00:00"))           # duplicate
    clock.set("2025-06-16 10:11")
    feed.step()
    assert len(sent) == 1


def test_no_bar_at_all_counts_from_start():
    _, feed, sent, clock = _setup("2025-06-16 10:00:00")
    clock.set("2025-06-16 10:10:00")
    feed.step()
    assert len(sent) == 1 and "none since start" in sent[0]
