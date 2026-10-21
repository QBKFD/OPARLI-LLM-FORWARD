"""Step 7: replay one day of parquet -> bus -> recorder -> store -> bars_before.

The result must equal the parquet rows for that day after _valid_bar
filtering. Runs over InMemoryBus always, and over a real Redis when
REDIS_TEST_URL points at a throwaway instance (the DB is flushed).
"""

import os
from datetime import timedelta
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")

from backend.datafeed.bus import InMemoryBus, RedisStreamBus
from backend.datafeed.ingest.xauusd_bars import STREAM, ReplaySource, _valid_bar
from backend.datafeed.recorder.recorder import Recorder
from backend.datafeed.store import schema
from backend.datafeed.store.query import bars_before

PARQUET = Path(__file__).resolve().parents[2] / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
DAY = "2025-06-11"


def _redis():
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


@pytest.mark.parametrize("kind", ["memory", pytest.param("redis", marks=pytest.mark.redis)])
def test_replay_one_day_end_to_end(kind, tmp_path):
    if not PARQUET.exists():
        pytest.skip("historical parquet not present (untracked)")
    bus = InMemoryBus() if kind == "memory" else _redis()
    start = pd.Timestamp(DAY, tz="UTC")
    end = start + pd.Timedelta(days=1)

    ReplaySource(str(PARQUET), start=start.to_pydatetime(), end=end.to_pydatetime()).run(
        lambda m: bus.publish(STREAM, m))
    path = str(tmp_path / "store.sqlite")
    Recorder(bus, schema.connect(path), STREAM, block_ms=None, sleep=lambda s: None).run(
        stop_when_idle=True)

    got = bars_before("XAUUSD", end.to_pydatetime(), timedelta(days=1), db=path, kinds=("replay",))
    assert bars_before("XAUUSD", end.to_pydatetime(), timedelta(days=1), db=path) == []   # never a fallback

    df = pd.read_parquet(PARQUET, filters=[("timestamp", ">=", start), ("timestamp", "<", end)])
    df = df.sort_values("timestamp")
    want = [(t.isoformat(), o, h, l, c)
            for t, o, h, l, c in df[["timestamp", "open", "high", "low", "close"]].itertuples(
                index=False, name=None)
            if _valid_bar(t.to_pydatetime(), o, h, l, c)]
    assert len(want) > 1000
    assert [(r["bar_ts"], r["open"], r["high"], r["low"], r["close"]) for r in got] == want
    assert {r["kind"] for r in got} == {"replay"} and {r["source"] for r in got} == {"replay"}
