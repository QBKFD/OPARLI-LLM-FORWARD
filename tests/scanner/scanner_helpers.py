"""Shared builders for the scanner stream tests."""

import os
from datetime import datetime, timezone

import pytest

from datafeed.bus import InMemoryBus, RedisStreamBus
from datafeed.ingest.xauusd_bars import Bar, bar_message


def bar_msg(ts: str, o, h, l, c, *, history=False, source="ibkr", n_ticks=10):
    t = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return bar_message(Bar("XAUUSD", t, float(o), float(h), float(l), float(c),
                           None if history else n_ticks),
                       source=source, history=history)


def redis_bus():
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        pytest.skip("REDIS_TEST_URL not set (needs a throwaway Redis)")
    redis = pytest.importorskip("redis")
    r = redis.Redis.from_url(url, decode_responses=True)
    try:
        r.ping()
    except Exception as e:
        pytest.skip(f"Redis unreachable: {e!r}")
    r.flushdb()
    return RedisStreamBus(client=r)


def make_bus(kind):
    return InMemoryBus() if kind == "memory" else redis_bus()


class FakeLive:
    """Stands in for LiveLevels: records bars, returns scripted alerts per bar_ts."""

    def __init__(self, alerts_at=None):
        self.bars, self.alerts_at = [], alerts_at or {}

    def on_bar(self, ts, o, h, l, c):
        self.bars.append((ts.isoformat(), o, h, l, c))
        return list(self.alerts_at.get(ts.isoformat(), []))
