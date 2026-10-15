"""Undecodable bus entries never crash a consumer: they are delivered with
`error` set, and the recorder writes them to rejected_messages and acks them."""

import json
import os
from datetime import datetime, timezone

import pytest

from datafeed.bus import UNDECODABLE, Delivery, RedisStreamBus
from datafeed.ingest.base import validate_message
from datafeed.ingest.xauusd_bars import STREAM, Bar, bar_message
from datafeed.recorder.recorder import Recorder
from datafeed.store import schema


def _bar(minute=0):
    t = datetime(2025, 6, 16, 10, minute, tzinfo=timezone.utc)
    return bar_message(Bar("XAUUSD", t, 3400.0, 3401.0, 3399.0, 3400.5, 7), source="ibkr", history=False)


@pytest.mark.parametrize("fields, why", [
    ({"json": "{not json"}, "Expecting property name"),
    ({"json": "[1, 2]"}, "not a JSON object but list"),
    ({"json": "42"}, "not a JSON object but int"),
    ({"other": "x"}, "no 'json' field"),
    ({"json": "��"}, "Expecting value"),             # what replaced invalid UTF-8 looks like
])
def test_decode_never_raises(fields, why):
    bus = RedisStreamBus(client=object())                       # no Redis needed for _decode
    d = bus._decode(STREAM, "1-0", fields)
    assert d.error is not None and why in d.error
    assert UNDECODABLE in d.msg and bus.undecodable == 1
    with pytest.raises(ValueError):                             # every consumer's validation rejects it
        validate_message(d.msg)


def test_decode_good_entry():
    bus = RedisStreamBus(client=object())
    d = bus._decode(STREAM, "1-0", {"json": json.dumps(_bar())})
    assert d == Delivery("1-0", _bar() | {"ingest_ts": d.msg["ingest_ts"]}) and d.error is None
    assert bus.undecodable == 0


def _redis_url():
    url = os.environ.get("REDIS_TEST_URL")
    if not url:
        pytest.skip("REDIS_TEST_URL not set (needs a throwaway Redis)")
    redis = pytest.importorskip("redis")
    raw = redis.Redis.from_url(url)                             # bytes, no decoding: can write anything
    try:
        raw.ping()
    except Exception as e:
        pytest.skip(f"Redis unreachable: {e!r}")
    raw.flushdb()
    return url, raw


@pytest.mark.redis
def test_consume_survives_undecodable_entries():
    url, raw = _redis_url()
    bus = RedisStreamBus(url=url)                               # the default client (encoding_errors=replace)
    raw.xadd(STREAM, {"json": json.dumps(_bar(0))})
    raw.xadd(STREAM, {"json": b"\xff\xfe not utf-8"})
    raw.xadd(STREAM, {"json": "{broken"})
    raw.xadd(STREAM, {"something": "else"})
    raw.xadd(STREAM, {"json": json.dumps(_bar(1))})
    got = bus.consume(STREAM, "g", "c", count=10)
    assert [d.error is None for d in got] == [True, False, False, False, True]
    assert bus.undecodable == 3
    for d in got:
        bus.ack(STREAM, "g", d.id)
    assert bus.consume(STREAM, "g", "c") == []
    raw.flushdb()


@pytest.mark.redis
def test_recorder_rejects_undecodable_and_records_the_rest(tmp_path):
    url, raw = _redis_url()
    bus = RedisStreamBus(url=url)
    raw.xadd(STREAM, {"json": json.dumps(_bar(0))})
    raw.xadd(STREAM, {"json": "{broken"})
    raw.xadd(STREAM, {"json": b"\xff\xfe"})
    raw.xadd(STREAM, {"json": json.dumps(_bar(1))})
    con = schema.connect(str(tmp_path / "store.sqlite"))
    rec = Recorder(bus, con, STREAM, block_ms=None, sleep=lambda s: None)
    rec.run(stop_when_idle=True)
    assert rec.stats["inserted"] == 2 and rec.stats["rejected"] == 2
    rows = con.execute("SELECT stream_id, raw, error, stage FROM rejected_messages ORDER BY stream_id").fetchall()
    assert len(rows) == 2 and {r["stage"] for r in rows} == {"invalid"}
    assert all(r["error"].startswith("undecodable: ") for r in rows)
    assert json.loads(rows[0]["raw"]) == "{broken"                # the raw text, kept as JSON
    assert con.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 2
    assert bus.consume(STREAM, "recorder", "recorder-1") == []      # all acked
    raw.flushdb()
