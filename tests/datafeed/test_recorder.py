"""Step 6: recorder over InMemoryBus — commit-then-ack, crash windows, poison
messages, health."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from backend.datafeed.bus import InMemoryBus
from backend.datafeed.ingest.xauusd_bars import STREAM, Bar, bar_message
from backend.datafeed.recorder.recorder import GROUP, MAX_BATCH_FAILURES, Recorder
from backend.datafeed.store import schema
from backend.datafeed.store.query import bars_before

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


def rejected(path):
    con = schema.connect(path)
    out = [dict(r) for r in con.execute("SELECT * FROM rejected_messages ORDER BY stream_id")]
    con.close()
    return out


def test_poison_message_isolated_after_n_failures(env):
    bus, path = env
    msgs = bars(10)
    msgs[4] = dict(msgs[4], open=None)            # passes validation, fails NOT NULL in the store
    ids = [bus.publish(STREAM, m) for m in msgs]
    now = T0 + timedelta(minutes=12)
    rec = recorder(bus, path, count=10, clock=lambda: now)
    for i in range(MAX_BATCH_FAILURES):           # the whole batch rolls back each time
        rec.step()
        assert rows(path) == [] and rec.stats["errors"] == i + 1
    rec.step()                                    # redelivered again: one message per transaction
    assert len(rows(path)) == 9 and rec.stats["inserted"] == 9
    assert rec.stats["poison"] == 1 and rec.stats["split_batches"] == 1
    (r,) = rejected(path)
    assert (r["stream"], r["stream_id"], r["stage"]) == (STREAM, ids[4], "poison")
    assert "NOT NULL" in r["error"] and json.loads(r["raw"]) == msgs[4]
    assert r["first_seen"] == schema.canonical(now)
    assert bus.consume(STREAM, GROUP, "recorder-1") == []          # all 10 acked
    for m in bars(3, start=10):                   # not stuck: later messages go through as batches
        bus.publish(STREAM, m)
    rec.run(stop_when_idle=True)
    assert len(rows(path)) == 12 and rec.stats["batches"] == 1


def test_failing_store_never_drops_bars(env, monkeypatch):
    # the store itself fails (disk full): even the rejected_messages row can't be
    # written, so nothing is acked or given up on, and everything lands once it recovers
    bus, path = env
    for m in bars(5):
        bus.publish(STREAM, m)
    rec = recorder(bus, path, count=5)
    broken = {"on": True}
    real_insert, real_reject = schema.insert_bar, schema.reject_message

    def insert(con, msg):
        if broken["on"]:
            raise sqlite3.OperationalError("database or disk is full")
        return real_insert(con, msg)

    def reject(*a, **kw):
        if broken["on"]:
            raise sqlite3.OperationalError("database or disk is full")
        return real_reject(*a, **kw)
    monkeypatch.setattr(schema, "insert_bar", insert)
    monkeypatch.setattr(schema, "reject_message", reject)
    for _ in range(MAX_BATCH_FAILURES + 2):
        rec.step()
    assert rows(path) == [] and rejected(path) == [] and rec.stats["poison"] == 0
    broken["on"] = False
    rec.run(stop_when_idle=True)
    assert len(rows(path)) == 5 and rejected(path) == []
    assert bus.consume(STREAM, GROUP, "recorder-1") == []


def test_invalid_message_rejected_and_acked(env):
    bus, path = env
    good = bars(2)
    bus.publish(STREAM, good[0])
    bus.publish(STREAM, {k: v for k, v in good[1].items() if k != "source"})
    bus.publish(STREAM, dict(good[1], bar_ts="not a time"))
    rec = recorder(bus, path, clock=lambda: T0 + timedelta(minutes=5))
    rec.run(stop_when_idle=True)
    assert rec.stats["rejected"] == 2 and rec.stats["inserted"] == 1
    assert bus.consume(STREAM, GROUP, "recorder-1") == []
    got = rejected(path)
    assert [(r["stream"], r["stage"], r["first_seen"]) for r in got] == [
        (STREAM, "invalid", "2026-03-02T10:05:00+00:00")] * 2
    assert "source" in got[0]["error"] and "not a time" in got[1]["error"]
    assert json.loads(got[1]["raw"]) == dict(good[1], bar_ts="not a time")


def test_invalid_message_row_shares_the_batch_transaction(env, monkeypatch):
    bus, path = env
    good = bars(3)
    bad = {k: v for k, v in good[0].items() if k != "source"}
    ids = [bus.publish(STREAM, m) for m in (bad, good[1], good[2])]
    rec = recorder(bus, path)
    real, fail = schema.insert_bar, {"left": 1}

    def flaky(con, msg):                          # the batch fails after the reject row was written
        if fail["left"]:
            fail["left"] -= 1
            raise RuntimeError("disk full")
        return real(con, msg)
    monkeypatch.setattr(schema, "insert_bar", flaky)
    rec.step()
    assert rejected(path) == [] and rows(path) == []      # rolled back together, nothing acked

    def dying_ack(*a):
        raise Crash()
    bus_ack, bus.ack = bus.ack, dying_ack
    with pytest.raises(Crash):
        rec.step()                                # committed (reject row + 2 bars), not acked
    rec.con.close()
    bus.ack = bus_ack
    assert [r["stream_id"] for r in rejected(path)] == [ids[0]] and len(rows(path)) == 2

    rec2 = recorder(bus, path)                    # restart: redelivered, no second reject row
    rec2.run(stop_when_idle=True)
    assert [r["stream_id"] for r in rejected(path)] == [ids[0]] and len(rows(path)) == 2
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
    assert rec.health()["last_live_bar_ts"] is None and rec.health()["lag_s"] is None
    rec.run(stop_when_idle=True)
    h = rec.health()
    assert h["last_live_bar_ts"] == "2026-03-02T10:02:00+00:00"
    assert h["lag_s"] == 10.0                                    # 10:02 bar completed at 10:03
    assert h["error_count"] == 0


def test_health_lag_uses_live_bars_only(env):
    # feed start: a history burst up to the forming minute, then live stops after 10:02.
    # The fresh history must not hide the stalled live feed.
    bus, path = env
    for m in bars(3) + bars(20, history=True):   # live 10:00-10:02, history 10:00-10:19
        bus.publish(STREAM, m)
    now = T0 + timedelta(minutes=20, seconds=5)
    rec = recorder(bus, path, clock=lambda: now)
    rec.run(stop_when_idle=True)
    h = rec.health()
    assert h["last_live_bar_ts"] == "2026-03-02T10:02:00+00:00"
    assert h["last_history_bar_ts"] == "2026-03-02T10:19:00+00:00"
    assert h["last_replay_bar_ts"] is None
    assert h["lag_s"] == 17 * 60 + 5.0                           # from the live 10:02 bar, not 5 s


def test_health_without_live_bars_has_no_lag(env):
    bus, path = env
    for m in bars(5, history=True) + [dict(m, source="replay") for m in bars(5, start=5)]:
        bus.publish(STREAM, m)
    rec = recorder(bus, path, clock=lambda: T0 + timedelta(hours=1))
    rec.run(stop_when_idle=True)
    h = rec.health()
    assert h["last_live_bar_ts"] is None and h["lag_s"] is None
    assert h["last_history_bar_ts"] == "2026-03-02T10:04:00+00:00"
    assert h["last_replay_bar_ts"] == "2026-03-02T10:09:00+00:00"
