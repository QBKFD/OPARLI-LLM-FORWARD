"""Step 5: store schema, idempotent writes, conflicts, point-in-time reads."""

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

from datafeed.ingest.xauusd_bars import Bar, bar_message
from datafeed.store import schema
from datafeed.store.query import bars_before, compare_live_vs_history, latest_bar_ts

UTC = timezone.utc


def T(s):
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def msg(ts, px, history=False, n_ticks=5, source="ibkr", ingest=None):
    b = Bar("XAUUSD", T(ts) if isinstance(ts, str) else ts, px, px + 1, px - 1, px + 0.5,
            None if history else n_ticks)
    return bar_message(b, source=source, history=history,
                       ingest_ts=ingest or (b.bar_ts + timedelta(minutes=1, seconds=2)))


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "store.sqlite")
    con = schema.connect(path)
    yield path, con
    con.close()


def write(con, msgs):
    con.execute("BEGIN")
    res = [schema.insert_bar(con, m) for m in msgs]
    con.execute("COMMIT")
    return res


def test_schema_version_and_wal(db):
    path, con = db
    assert schema.schema_version(con) == len(schema.MIGRATIONS) == 3
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con2 = schema.connect(path)                     # re-open: migrations not re-applied
    assert con2.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 3
    con2.close()


def test_idempotent_and_conflict(db):
    _, con = db
    m = msg("2026-03-02 10:00", 4300.0)
    assert write(con, [m]) == ["inserted"]
    assert write(con, [dict(m, ingest_ts="2026-03-02T11:00:00+00:00")]) == ["duplicate"]
    changed = dict(m, close=4999.0)
    assert write(con, [changed]) == ["conflict"]
    row = con.execute("SELECT close FROM bars").fetchone()
    assert row["close"] == 4300.5                                  # first recorded wins
    c = con.execute("SELECT * FROM bar_conflicts").fetchall()
    assert len(c) == 1 and c[0]["kind"] == "live" and '"close": 4999.0' in c[0]["incoming"]


def test_restart_republish_creates_no_conflicts(db):
    _, con = db
    mins = [T("2026-03-02 10:00") + timedelta(minutes=i) for i in range(30)]
    live = [msg(t, 4300.0 + i * 0.1) for i, t in enumerate(mins)]
    hist = [msg(t, 4300.0 + i * 0.1 + 0.03, history=True) for i, t in enumerate(mins)]  # differs from live
    write(con, live)
    write(con, hist)                                  # first start: warm-up
    res = write(con, hist) + write(con, hist)         # two restarts re-publish the same history
    assert set(res) == {"duplicate"}
    assert con.execute("SELECT COUNT(*) FROM bar_conflicts").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 60


def test_strict_before_t(db):
    path, con = db
    write(con, [msg("2026-03-02 10:00", 1.0), msg("2026-03-02 10:01", 2.0), msg("2026-03-02 10:02", 3.0)])
    got = bars_before("XAUUSD", T("2026-03-02 10:02"), 10, db=path)
    assert [r["bar_ts"] for r in got] == ["2026-03-02T10:00:00+00:00", "2026-03-02T10:01:00+00:00"]
    got = bars_before("XAUUSD", T("2026-03-02 10:01:59.999"), 10, db=path)
    assert [r["bar_ts"][11:16] for r in got] == ["10:00"]          # 10:01 ends at 10:02
    assert bars_before("XAUUSD", T("2026-03-02 10:00"), 10, db=path) == []
    # lookback as a count keeps the most recent, as a timedelta keeps a window
    assert [r["open"] for r in bars_before("XAUUSD", T("2026-03-02 11:00"), 2, db=path)] == [2.0, 3.0]
    assert [r["open"] for r in bars_before("XAUUSD", T("2026-03-02 10:03"), timedelta(minutes=2),
                                           db=path)] == [2.0, 3.0]


def test_mid_minute_t_excludes_forming_bar(db):
    # a bar is complete only at bar_ts + 1 min, for every kind
    path, con = db
    write(con, [msg("2026-03-02 10:00", 1.0), msg("2026-03-02 10:01", 2.0, history=True),
                msg("2026-03-02 10:02", 3.0, history=True),
                msg("2026-03-02 10:00", 1.0, source="replay"), msg("2026-03-02 10:01", 2.0, source="replay")])
    for t in (T("2026-03-02 10:01:30"), T("2026-03-02 10:01:00.5"), "2026-03-02T11:01:30+01:00"):
        assert [r["bar_ts"][11:16] for r in bars_before("XAUUSD", t, 10, db=path)] == ["10:00"]
        assert [r["bar_ts"][11:16] for r in bars_before("XAUUSD", t, 10, db=path,
                                                         kinds=("replay",))] == ["10:00"]
    # visible exactly when it ends, and from then on
    for t in (T("2026-03-02 10:02"), T("2026-03-02 10:02:00.5"), T("2026-03-02 10:02:59")):
        got = bars_before("XAUUSD", t, 10, db=path)
        assert [(r["bar_ts"][11:16], r["kind"]) for r in got] == [("10:00", "live"), ("10:01", "history")]
    # a timedelta window keeps the bars that lie wholly inside [t - lookback, t]
    got = bars_before("XAUUSD", T("2026-03-02 10:03:30"), timedelta(minutes=2), db=path)
    assert [r["bar_ts"][11:16] for r in got] == ["10:02"]
    with pytest.raises(ValueError):
        bars_before("XAUUSD", datetime(2026, 3, 2, 10, 1, 30), 10, db=path)   # naive t


def test_live_preferred_history_fills_gaps(db):
    path, con = db
    write(con, [msg("2026-03-02 10:00", 100.0, history=True), msg("2026-03-02 10:01", 101.0, history=True),
                msg("2026-03-02 10:02", 102.0, history=True)])
    write(con, [msg("2026-03-02 10:00", 200.0), msg("2026-03-02 10:02", 202.0)])  # 10:01 live missing
    got = bars_before("XAUUSD", T("2026-03-02 10:03"), 10, db=path)
    assert [(r["bar_ts"][11:16], r["kind"], r["open"]) for r in got] == [
        ("10:00", "live", 200.0), ("10:01", "history", 101.0), ("10:02", "live", 202.0)]
    assert latest_bar_ts("XAUUSD", db=path) == "2026-03-02T10:02:00+00:00"
    assert latest_bar_ts("XAUUSD", db=path, kind="history") == "2026-03-02T10:02:00+00:00"
    assert latest_bar_ts("NOPE", db=path) is None


def test_known_by_hides_later_backfill(db):
    path, con = db
    write(con, [msg("2026-03-02 10:00", 1.0, history=True, ingest=T("2026-03-05 09:00")),  # backfilled later
                msg("2026-03-02 10:01", 2.0)])
    t = T("2026-03-02 10:05")
    assert len(bars_before("XAUUSD", t, 10, db=path)) == 2
    seen = bars_before("XAUUSD", t, 10, db=path, known_by=t)
    assert [r["bar_ts"][11:16] for r in seen] == ["10:01"]


def test_compare_live_vs_history_known_deltas(db):
    path, con = db
    base = T("2026-03-02 10:00")
    deltas = [0.10, -0.20, 0.05, 0.0]
    for i, d in enumerate(deltas):
        t = base + timedelta(minutes=i)
        h = msg(t, 4300.0, history=True)
        lv = dict(msg(t, 4300.0), open=4300.0 + d, high=4301.0 + 2 * d, low=4299.0 - d, close=4300.5 + d)
        write(con, [h, lv])
    write(con, [msg(base + timedelta(minutes=10), 1.0)])                     # live only
    write(con, [msg(base + timedelta(minutes=11), 1.0, history=True),       # history only
                msg(base + timedelta(minutes=12), 1.0, history=True)])
    write(con, [msg(base + timedelta(hours=2), 1.0, history=True),          # outside window
                msg(base + timedelta(hours=2), 1.0)])
    r = compare_live_vs_history("XAUUSD", base, base + timedelta(hours=1), db=path)
    assert [round(x["d_open"], 9) for x in r["rows"]] == deltas
    assert [round(x["d_high"], 9) for x in r["rows"]] == [2 * d for d in deltas]
    assert [round(x["d_low"], 9) for x in r["rows"]] == [-d for d in deltas]
    s = r["summary"]
    assert (s["n_both"], s["n_live_only"], s["n_history_only"]) == (4, 1, 2)
    assert s["d_open"]["mean"] == pytest.approx(-0.0125)
    assert s["d_open"]["mean_abs"] == pytest.approx(0.0875)
    assert s["d_open"]["max_abs"] == pytest.approx(0.20)
    assert s["d_high"]["max_abs"] == pytest.approx(0.40)
    empty = compare_live_vs_history("XAUUSD", T("2027-01-01"), T("2027-01-02"), db=path)
    assert empty["rows"] == [] and empty["summary"]["d_close"] is None


def test_wal_reader_while_writer_active(db):
    path, con = db
    write(con, [msg("2026-03-02 10:00", 1.0)])
    con.execute("BEGIN")
    schema.insert_bar(con, msg("2026-03-02 10:01", 2.0))          # uncommitted
    # a reader in another thread is neither blocked nor sees uncommitted data
    result = {}
    th = threading.Thread(target=lambda: result.update(
        rows=bars_before("XAUUSD", T("2026-03-02 11:00"), 10, db=path)))
    th.start()
    th.join(timeout=5)
    assert not th.is_alive()
    assert [r["open"] for r in result["rows"]] == [1.0]
    con.execute("COMMIT")
    assert [r["open"] for r in bars_before("XAUUSD", T("2026-03-02 11:00"), 10, db=path)] == [1.0, 2.0]


def test_query_uses_primary_key_index(db):
    _, con = db
    sql = []
    con.set_trace_callback(sql.append)          # capture the SQL bars_before really runs
    bars_before("XAUUSD", T("2026-03-02 10:00"), 5, db=con)
    con.set_trace_callback(None)
    q = next(x for x in sql if x.startswith("SELECT b."))
    plan = [r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q)]
    assert any("SEARCH b USING PRIMARY KEY" in p for p in plan)
    assert any("SEARCH p USING PRIMARY KEY" in p for p in plan)
    assert not any(p.startswith("SCAN") for p in plan)


def test_timestamps_canonical(db):
    _, con = db
    m = dict(msg("2026-03-02 10:00", 1.0), bar_ts="2026-03-02T11:00:00+01:00")   # same instant, other offset
    write(con, [msg("2026-03-02 10:00", 1.0)])
    assert write(con, [m]) == ["duplicate"]
    with pytest.raises(ValueError):
        schema.canonical(datetime(2026, 3, 2, 10))


def test_replay_kind_only_when_asked(db):
    path, con = db
    write(con, [msg("2026-03-02 10:00", 1.0, source="replay"), msg("2026-03-02 10:01", 2.0, source="replay"),
                msg("2026-03-02 10:01", 20.0, history=True), msg("2026-03-02 10:02", 30.0)])
    assert {r["kind"] for r in con.execute("SELECT kind FROM bars WHERE source='replay'")} == {"replay"}
    t = T("2026-03-02 11:00")
    default = bars_before("XAUUSD", t, 10, db=path)
    assert [(r["bar_ts"][11:16], r["kind"]) for r in default] == [("10:01", "history"), ("10:02", "live")]
    rep = bars_before("XAUUSD", t, 10, db=path, kinds=("replay",))
    assert [(r["bar_ts"][11:16], r["kind"], r["open"]) for r in rep] == [("10:00", "replay", 1.0),
                                                                         ("10:01", "replay", 2.0)]
    assert [r["kind"] for r in bars_before("XAUUSD", t, 10, db=path, kinds=("history",))] == ["history"]
    for bad in [("live", "replay"), ("replay", "history"), (), ("live", "live"), ("nope",)]:
        with pytest.raises(ValueError):
            bars_before("XAUUSD", t, 10, db=path, kinds=bad)


def test_migration_2_moves_v1_replay_rows(tmp_path):
    import sqlite3
    path = str(tmp_path / "v1.sqlite")
    con = sqlite3.connect(path, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    real = schema.MIGRATIONS
    try:
        schema.MIGRATIONS = real[:1]                       # a store created by v1
        schema.migrate(con)
    finally:
        schema.MIGRATIONS = real
    con.execute("BEGIN")
    v1_rows = [msg("2026-03-02 10:00", 1.0, source="replay"), msg("2026-03-02 10:00", 2.0, history=True),
               msg("2026-03-02 10:01", 3.0)]
    for m in v1_rows:                                    # v1 stored replay as 'live'
        con.execute("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (m["symbol"], schema.canonical(m["bar_ts"]), "history" if m["history"] else "live",
                     m["open"], m["high"], m["low"], m["close"], m["n_ticks"], m["source"],
                     1, schema.canonical(m["ingest_ts"]), "2026-03-02T12:00:00+00:00"))
    con.execute("COMMIT")
    assert schema.migrate(con) == len(schema.MIGRATIONS)
    got = {(r["bar_ts"][11:16], r["kind"], r["source"]) for r in con.execute("SELECT * FROM bars")}
    assert got == {("10:00", "replay", "replay"), ("10:00", "history", "ibkr"), ("10:01", "live", "ibkr")}
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO bars VALUES ('X','t','bogus',1,1,1,1,NULL,'s',1,'t','t')")


def test_reject_message_idempotent_per_stream_id(db):
    import sqlite3
    path, con = db
    bad = dict(msg("2026-03-02 10:00", 1.0), open=None)
    con.execute("BEGIN")
    schema.reject_message(con, "s", "7-0", bad, "IntegrityError('x')", "poison", T("2026-03-02 10:05"))
    schema.reject_message(con, "s", "7-0", bad, "again", "poison", T("2026-03-02 11:00"))  # redelivery
    schema.reject_message(con, "other", "7-0", bad, "y", "invalid", T("2026-03-02 10:06"))
    con.execute("COMMIT")
    got = [dict(r) for r in con.execute("SELECT * FROM rejected_messages ORDER BY stream")]
    assert [(r["stream"], r["stage"], r["first_seen"], r["error"]) for r in got] == [
        ("other", "invalid", "2026-03-02T10:06:00+00:00", "y"),
        ("s", "poison", "2026-03-02T10:05:00+00:00", "IntegrityError('x')")]
    assert json.loads(got[1]["raw"]) == bad
    with pytest.raises(sqlite3.IntegrityError):
        schema.reject_message(con, "s", "8-0", bad, "e", "bogus", T("2026-03-02 10:05"))


def test_connections_are_autocommit(tmp_path):
    # isolation_level=None keeps the explicit BEGIN/COMMIT (migrate, recorder) the only
    # transactions. Under sqlite3's default, a write outside BEGIN silently opens a
    # transaction that holds the write lock, and the next explicit BEGIN fails.
    import sqlite3
    path = str(tmp_path / "s.sqlite")
    w, r = schema.connect(path), schema.connect(path, readonly=True)
    assert w.isolation_level is None and r.isolation_level is None
    assert not w.in_transaction                                    # migrate() left nothing open
    schema.insert_bar(w, msg("2026-03-02 10:00", 1.0))             # a write outside BEGIN ...
    assert not w.in_transaction                                    # ... is committed at once
    assert r.execute("SELECT COUNT(*) FROM bars").fetchone()[0] == 1
    w.execute("BEGIN")
    w.execute("COMMIT")
    # control: the same write on a default connection leaves a transaction open
    d = sqlite3.connect(path)
    schema.insert_bar(d, msg("2026-03-02 10:01", 2.0))
    assert d.in_transaction
    with pytest.raises(sqlite3.OperationalError, match="within a transaction"):
        d.execute("BEGIN")
    d.rollback()
    d.close()
