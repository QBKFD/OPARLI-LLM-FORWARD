"""Step 2: bus semantics (ordering, ack/redelivery, trimming) and the envelope.

Each bus test runs against InMemoryBus and, when REDIS_TEST_URL points at a
reachable *throwaway* Redis, against RedisStreamBus too (the DB is flushed).
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from backend.datafeed.bus import InMemoryBus, RedisStreamBus
from backend.datafeed.ingest.base import Source, make_message, parse_utc, validate_message


def _redis_bus(maxlen):
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
    return RedisStreamBus(client=r, maxlen=maxlen)


@pytest.fixture(params=["memory", pytest.param("redis", marks=pytest.mark.redis)])
def make_bus(request):
    def make(maxlen=1000):
        return InMemoryBus(maxlen=maxlen) if request.param == "memory" else _redis_bus(maxlen)
    return make


def _msg(i):
    t = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=i)
    return make_message(schema_version=1, source="test", symbol="XAUUSD",
                        source_ts=t, payload={"i": i, "close": 4000.5 + i})


def _s():
    return f"s-{uuid.uuid4().hex[:8]}"


def test_ordering_and_roundtrip(make_bus):
    bus, s = make_bus(), _s()
    for i in range(5):
        bus.publish(s, _msg(i))
    got = bus.consume(s, "g", "c1", count=10)
    assert [d.msg["i"] for d in got] == [0, 1, 2, 3, 4]
    assert got[0].msg == _msg(0) | {"ingest_ts": got[0].msg["ingest_ts"]}
    assert isinstance(got[0].msg["i"], int) and isinstance(got[0].msg["close"], float)
    for d in got:
        bus.ack(s, "g", d.id)
    assert bus.consume(s, "g", "c1") == []


def test_count_limits_batch_and_keeps_order(make_bus):
    bus, s = make_bus(), _s()
    for i in range(5):
        bus.publish(s, _msg(i))
    seen = []
    while batch := bus.consume(s, "g", "c1", count=2):
        seen += [d.msg["i"] for d in batch]
        for d in batch:
            bus.ack(s, "g", d.id)
    assert seen == [0, 1, 2, 3, 4]


def test_unacked_redelivered_after_crash(make_bus):
    bus, s = make_bus(), _s()
    for i in range(4):
        bus.publish(s, _msg(i))
    first = bus.consume(s, "g", "c1", count=10)
    bus.ack(s, "g", first[0].id)
    bus.ack(s, "g", first[1].id)
    # crash: entries 2 and 3 delivered but never acked; new data arrives meanwhile
    bus.publish(s, _msg(4))
    again = bus.consume(s, "g", "c1", count=10)      # restart, same consumer name
    assert [d.msg["i"] for d in again] == [2, 3]     # pending first, nothing new mixed in
    assert [d.id for d in again] == [first[2].id, first[3].id]
    for d in again:
        bus.ack(s, "g", d.id)
    assert [d.msg["i"] for d in bus.consume(s, "g", "c1")] == [4]


def test_groups_are_independent(make_bus):
    bus, s = make_bus(), _s()
    bus.publish(s, _msg(0))
    a = bus.consume(s, "recorder", "c1")
    b = bus.consume(s, "other", "c1")
    assert [d.msg["i"] for d in a] == [d.msg["i"] for d in b] == [0]


def test_trimming_keeps_newest(make_bus):
    bus, s = make_bus(maxlen=10), _s()
    for i in range(500):
        bus.publish(s, _msg(i))
    got = bus.consume(s, "g", "c1", count=1000)
    ids = [d.msg["i"] for d in got]
    assert ids == sorted(ids) and ids[-1] == 499
    assert 10 <= len(ids) < 500          # Redis trims approximately (~), never below maxlen


def test_trimmed_pending_is_counted_not_silent():
    bus, s = InMemoryBus(maxlen=3), _s()
    for i in range(3):
        bus.publish(s, _msg(i))
    first = bus.consume(s, "g", "c1")                # 0,1,2 pending
    for i in range(3, 6):
        bus.publish(s, _msg(i))                      # trims 0,1,2 away
    got = bus.consume(s, "g", "c1")
    assert bus.lost == 3
    assert [d.msg["i"] for d in got] == [3, 4, 5]
    assert len(first) == 3


def test_message_envelope():
    m = _msg(0)
    validate_message(m)
    assert parse_utc(m["source_ts"]) == datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)
    assert m["source_ts"].endswith("+00:00") and m["ingest_ts"].endswith("+00:00")
    with pytest.raises(ValueError):
        make_message(schema_version=1, source="t", symbol="X",
                     source_ts=datetime(2026, 1, 5, 10, 0), payload={})   # naive
    with pytest.raises(ValueError):
        make_message(schema_version=1, source="t", symbol="X",
                     source_ts=datetime.now(timezone.utc), payload={"source": "x"})
    with pytest.raises(ValueError):
        validate_message({k: v for k, v in m.items() if k != "ingest_ts"})


def test_source_protocol_shape():
    class Dummy:
        name, stream, schema_version = "dummy", "s", 1
        def run(self, publish): publish(_msg(0))
        def health(self): return {"ok": True}
    assert isinstance(Dummy(), Source)
