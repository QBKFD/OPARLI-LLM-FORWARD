"""Step 6: recorder over InMemoryBus — commit-then-ack, crash windows, health."""

from datetime import datetime, timedelta, timezone

import pytest

from datafeed.bus import InMemoryBus
from datafeed.ingest.xauusd_bars import STREAM, Bar, bar_message
from datafeed.recorder.recorder import GROUP, Recorder
from datafeed.store import schema
from datafeed.store.query import bars_before

UTC = timezone.utc
T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)


def bars(n, start=0, history=False):
    return [bar_message(Bar("XAUUSD", T0 + timedelta(minutes=i), 4300.0 + i, 4301.0 + i, 4299.0 + i,
                            4300.5 + i, None if history else 3), source="ibkr", history=history)
            for i in range(start, start + n)]


class Crash(BaseException):
    """Simulated process death (not an Exception, so nothing catches it)."""


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "s.sqlite")
    return InMemoryBus(), path


def recorder(bus, path, **kw):
    return Recorder(bus, schema.connect(path), STREAM, "recorder-1", block_ms=None,
                    sleep=lambda s: None, **kw)


def rows(path):
    con = schema.connect(path)
    out = con.execute("SELECT bar_ts, kind FROM bars ORDER BY bar_ts").fetchall()
    con.close()
    return [tuple(r) for r in out]


def test_records_in_order_and_acks(env):
    bus, path = env
    for m in bars(25):
        bus.publish(STREAM, m)
    rec = recorder(bus, path, count=10)
    rec.run(stop_when_idle=True)
    assert len(rows(path)) == 25
    assert rec.stats["inserted"] == 25 and rec.stats["batches"] == 3
    assert bus.consume(STREAM, GROUP, "recorder-1") == []          # everything acked
    got = bars_before("XAUUSD", T0 + timedelta(hours=1), 100, db=path)
    assert [r["open"] for r in got] == [4300.0 + i for i in range(25)]


def test_crash_between_commit_and_ack_no_duplicate(env):
    bus, path = env
    for m in bars(30):
        bus.publish(STREAM, m)
    rec = recorder(bus, path, count=10)
    rec.step()                                                   # batch 1 fine

    def dying_ack(*a):
        raise Crash()
    bus_ack, bus.ack = bus.ack, dying_ack
    with pytest.raises(Crash):
        rec.step()                                               # batch 2 committed, not acked
    rec.con.close()
    bus.ack = bus_ack
    assert len(rows(path)) == 20

    rec2 = recorder(bus, path, count=10)                         # restart, same consumer name
    rec2.run(stop_when_idle=True)
    assert rec2.stats["duplicate"] == 10 and rec2.stats["inserted"] == 10
    r = rows(path)
    assert len(r) == len(set(r)) == 30
    con = schema.connect(path)
    assert con.execute("SELECT COUNT(*) FROM bar_conflicts").fetchone()[0] == 0


def test_crash_before_commit_no_gap(env, monkeypatch):
    bus, path = env
    for m in bars(30):
        bus.publish(STREAM, m)
    rec = recorder(bus, path, count=10)
    rec.step()
    calls = {"n": 0}
    real = schema.insert_bar

    def dying_insert(con, msg):
        calls["n"] += 1
        if calls["n"] == 5:
            raise Crash()                                        # mid-batch, before COMMIT
        return real(con, msg)
    monkeypatch.setattr(schema, "insert_bar", dying_insert)
    with pytest.raises(Crash):
        rec.step()
    rec.con.close()                                              # process gone: open txn discarded
    monkeypatch.setattr(schema, "insert_bar", real)
    assert len(rows(path)) == 10

    for m in bars(5, start=30):                                  # feed kept publishing meanwhile
        bus.publish(STREAM, m)
    rec2 = recorder(bus, path, count=10)
    rec2.run(stop_when_idle=True)
    ts = [r[0] for r in rows(path)]
    assert len(ts) == 35
    want = [schema.canonical(T0 + timedelta(minutes=i)) for i in range(35)]
    assert ts == want                                            # contiguous: no gap


def test_store_error_rolls_back_and_retries(env, monkeypatch):
    bus, path = env
    for m in bars(5):
        bus.publish(STREAM, m)
    rec = recorder(bus, path)
    real, fail = schema.insert_bar, {"left": 1}

    def flaky(con, msg):
        if fail["left"]:
            fail["left"] -= 1
            raise RuntimeError("disk full")
        return real(con, msg)
    monkeypatch.setattr(schema, "insert_bar", flaky)
    rec.step()
    assert rows(path) == [] and rec.health()["error_count"] == 1
    rec.run(stop_when_idle=True)
    assert len(rows(path)) == 5


def test_invalid_message_rejected_and_acked(env):
    bus, path = env
    good = bars(2)
    bus.publish(STREAM, good[0])
    bus.publish(STREAM, {k: v for k, v in good[1].items() if k != "source"})
    bus.publish(STREAM, dict(good[1], bar_ts="not a time"))
    rec = recorder(bus, path)
    rec.run(stop_when_idle=True)
    assert rec.stats["rejected"] == 2 and rec.stats["inserted"] == 1
    assert bus.consume(STREAM, GROUP, "recorder-1") == []


def test_history_and_live_both_recorded(env):
    bus, path = env
    for m in bars(3, history=True) + bars(3):
        bus.publish(STREAM, m)
    recorder(bus, path).run(stop_when_idle=True)
    assert sorted(k for _, k in rows(path)) == ["history"] * 3 + ["live"] * 3


def test_health(env):
    bus, path = env
    for m in bars(3):
        bus.publish(STREAM, m)
    now = T0 + timedelta(minutes=3, seconds=10)
    rec = recorder(bus, path, clock=lambda: now)
    assert rec.health()["last_recorded_bar_ts"] is None and rec.health()["lag_s"] is None
    rec.run(stop_when_idle=True)
    h = rec.health()
    assert h["last_recorded_bar_ts"] == "2026-03-02T10:02:00+00:00"
    assert h["lag_s"] == 10.0                                    # 10:02 bar completed at 10:03
    assert h["error_count"] == 0
