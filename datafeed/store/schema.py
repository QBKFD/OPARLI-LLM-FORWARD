"""SQLite point-in-time store: schema, migrations, and the bar writer.

- WAL mode: readers never block the writer and see the last committed state.
- Migrations are numbered SQL scripts (MIGRATIONS), applied in order inside
  one transaction each and recorded in schema_version. Never edit an applied
  migration; add the next number.
- Timestamps are TEXT, ISO-8601 UTC, canonical form "YYYY-MM-DDTHH:MM:SS+00:00",
  so string order is time order.

bars
    One row per (symbol, bar_ts, kind), kind in {live, history, replay}:
      live     tick-built bars from the live feed
      history  IBKR history warm-up bars (published with history=True)
      replay   bars replayed from a parquet file (source="replay"); never mixed
               with the other two by readers (see query.py)
    Live and history bars for the same minute are separate rows. They usually
    differ slightly (tick midpoints vs IBKR MIDPOINT bars), and neither is
    allowed to overwrite the other. Readers choose (see query.py).
    Writes are idempotent: the same values again are a no-op. Different values
    for an existing (symbol, bar_ts, kind) are NOT written. The incoming row
    goes to bar_conflicts and the stored row stays as first recorded.

bar_conflicts
    Append-only log of rejected different-value writes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone

from datafeed.ingest.base import parse_utc

log = logging.getLogger(__name__)

KINDS = ("live", "history", "replay")
VALUE_FIELDS = ("open", "high", "low", "close", "n_ticks")

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE bars (
        symbol          TEXT    NOT NULL,
        bar_ts          TEXT    NOT NULL,   -- minute open, UTC
        kind            TEXT    NOT NULL CHECK (kind IN ('live', 'history')),
        open            REAL    NOT NULL,
        high            REAL    NOT NULL,
        low             REAL    NOT NULL,
        close           REAL    NOT NULL,
        n_ticks         INTEGER,            -- NULL for history / replay bars
        source          TEXT    NOT NULL,   -- producing Source: ibkr, replay, ...
        schema_version  INTEGER NOT NULL,   -- message schema version
        ingest_ts       TEXT    NOT NULL,   -- when the source received it
        recorded_ts     TEXT    NOT NULL,   -- when the store committed it
        PRIMARY KEY (symbol, bar_ts, kind)
    ) WITHOUT ROWID;

    CREATE TABLE bar_conflicts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT    NOT NULL,
        bar_ts          TEXT    NOT NULL,
        kind            TEXT    NOT NULL,
        stored          TEXT    NOT NULL,   -- JSON of the row kept
        incoming        TEXT    NOT NULL,   -- JSON of the rejected message
        detected_ts     TEXT    NOT NULL
    );
    CREATE INDEX bar_conflicts_key ON bar_conflicts (symbol, bar_ts);
    """),
    (2, """
    -- add kind 'replay' (SQLite can't alter a CHECK: rebuild the table) and
    -- move rows recorded from the replay source, stored as 'live' by v1, to it
    CREATE TABLE bars_v2 (
        symbol          TEXT    NOT NULL,
        bar_ts          TEXT    NOT NULL,
        kind            TEXT    NOT NULL CHECK (kind IN ('live', 'history', 'replay')),
        open            REAL    NOT NULL,
        high            REAL    NOT NULL,
        low             REAL    NOT NULL,
        close           REAL    NOT NULL,
        n_ticks         INTEGER,
        source          TEXT    NOT NULL,
        schema_version  INTEGER NOT NULL,
        ingest_ts       TEXT    NOT NULL,
        recorded_ts     TEXT    NOT NULL,
        PRIMARY KEY (symbol, bar_ts, kind)
    ) WITHOUT ROWID;
    INSERT INTO bars_v2
        SELECT symbol, bar_ts,
               CASE WHEN source = 'replay' THEN 'replay' ELSE kind END,
               open, high, low, close, n_ticks, source, schema_version, ingest_ts, recorded_ts
        FROM bars;
    DROP TABLE bars;
    ALTER TABLE bars_v2 RENAME TO bars;
    """),
]


def _now() -> str:
    return canonical(datetime.now(timezone.utc))


def canonical(ts) -> str:
    """datetime or ISO string -> canonical UTC string (second precision)."""
    if isinstance(ts, str):
        ts = parse_utc(ts)
    elif ts.tzinfo is None:
        raise ValueError(f"naive timestamp {ts!r}")
    return ts.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def schema_version(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, applied_ts TEXT NOT NULL)")
    return con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]


def migrate(con: sqlite3.Connection) -> int:
    current = schema_version(con)
    for n, sql in MIGRATIONS:
        if n <= current:
            continue
        con.execute("BEGIN")
        try:
            for stmt in (s.strip() for s in sql.split(";")):
                if stmt:
                    con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, ?)", (n, _now()))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        log.info("store: applied migration %d", n)
        current = n
    return current


def connect(path: str, *, readonly: bool = False) -> sqlite3.Connection:
    """Open the store. A writer connection enables WAL and applies migrations.
    Connections are in autocommit mode (isolation_level=None): writers wrap
    their work in explicit BEGIN/COMMIT (see insert_bar's caller)."""
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, isolation_level=None)
        con.row_factory = sqlite3.Row
        return con
    con = sqlite3.connect(path, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    migrate(con)
    return con


def kind_of(msg: dict) -> str:
    if msg.get("source") == "replay":
        return "replay"
    return "history" if msg.get("history") else "live"


def insert_bar(con: sqlite3.Connection, msg: dict) -> str:
    """Write one bar message. Returns "inserted", "duplicate" or "conflict".
    Does not commit: the caller owns the transaction, so it can commit a batch
    and only then ack it on the bus."""
    key = (msg["symbol"], canonical(msg["bar_ts"]), kind_of(msg))
    row = con.execute("SELECT * FROM bars WHERE symbol=? AND bar_ts=? AND kind=?", key).fetchone()
    if row is None:
        con.execute(
            "INSERT INTO bars (symbol, bar_ts, kind, open, high, low, close, n_ticks, source, "
            "schema_version, ingest_ts, recorded_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (*key, msg["open"], msg["high"], msg["low"], msg["close"], msg.get("n_ticks"),
             msg["source"], msg["schema_version"], canonical(msg["ingest_ts"]), _now()))
        return "inserted"
    if all(row[f] == msg.get(f) for f in VALUE_FIELDS):
        return "duplicate"
    con.execute("INSERT INTO bar_conflicts (symbol, bar_ts, kind, stored, incoming, detected_ts) "
                "VALUES (?,?,?,?,?,?)",
                (*key, json.dumps(dict(row)), json.dumps(msg), _now()))
    log.warning("store: conflicting %s bar %s %s kept as first recorded", key[2], key[0], key[1])
    return "conflict"
