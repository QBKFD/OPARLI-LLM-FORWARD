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


def tg_msg(text: str, update_id: int = 1, date: str = "2025-10-14 01:30:00"):
    """A Telegram reply as the scanner receives it: update id, message date (UTC), text."""
    import pandas as pd
    import deterministic_scanner as ds
    return ds.TelegramMessage(update_id, pd.Timestamp(date, tz="UTC"), text)


class FakeLive:
    """Stands in for LiveLevels: records bars, returns scripted alerts per bar_ts.
    Like LiveLevels it keeps every bar it has seen in .hist (warm-up included)."""

    def __init__(self, alerts_at=None, hist=None):
        import pandas as pd
        self.bars, self.alerts_at = [], alerts_at or {}
        self.hist = hist if hist is not None else pd.DataFrame(
            columns=["open", "high", "low", "close"], dtype=float,
            index=pd.DatetimeIndex([], tz="UTC"))

    def on_bar(self, ts, o, h, l, c):
        self.bars.append((ts.isoformat(), o, h, l, c))
        self.hist.loc[ts] = [o, h, l, c]
        return list(self.alerts_at.get(ts.isoformat(), []))


class Log(list):
    def __call__(self, *a, **k):
        self.append(" ".join(map(str, a)))

    def having(self, s):
        return [x for x in self if s in x]


def fill_store(path, minutes, *, kind="history", price=3400.0):
    """Write one bar per minute (pd.Timestamp, UTC) into a datafeed store."""
    from datafeed.store import schema
    con = schema.connect(str(path))
    con.execute("BEGIN")
    for i, m in enumerate(minutes):
        p = price + (i % 7) * 0.1
        msg = bar_msg(m.strftime("%Y-%m-%dT%H:%M:%S"), p, p + 0.5, p - 0.5, p,
                      history=(kind == "history"), source="replay" if kind == "replay" else "ibkr")
        schema.insert_bar(con, msg)
    con.execute("COMMIT")
    con.close()
