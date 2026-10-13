"""Point-in-time reads from the store.

THE "AS OF t" RULE
    A bar is the minute [bar_ts, bar_ts + 1 min). At time t only bars with
    bar_ts + 1 min <= t are complete, but the bar at bar_ts = t - 1 min only
    completes exactly at t. So the rule is: bars_before(t) returns bars with
    bar_ts STRICTLY < t. The minute that opens at t is never included, and
    neither is any later one. This matches research HistoricalDataProvider
    (< as_of) and VolBlock ("1-min bars opening before t").

    One bar per minute: the live (tick-built) bar if one exists, otherwise the
    IBKR history bar. Each result row carries `kind` so callers can see which.

    Optional `known_by`: also require ingest_ts <= known_by, i.e. only bars the
    system had actually received by then. Backfilled history for an old minute
    is then invisible to a replay of a moment before the backfill ran. Use it
    when simulating what a live process could have seen. Leave it None for
    research on the best available bars.

PATTERN FOR LATER TABLES (chain snapshots, calendar events, ...)
    Append-only and versioned by ingest_ts. Never UPDATE or DELETE a fact.
    A revision is a new row with a later ingest_ts, keyed by
    (natural key, ingest_ts). A point-in-time read at t selects, per natural
    key, the latest row with ingest_ts <= t (and, where the data has its own
    event/market time, event_ts < t). Bars are the special case where the
    natural key is (symbol, bar_ts, kind) and revisions are refused and logged
    in bar_conflicts instead of stored.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import timedelta

from datafeed.store.schema import canonical, connect

COLUMNS = "symbol, bar_ts, kind, open, high, low, close, n_ticks, source, ingest_ts"


def _con(db):
    if isinstance(db, sqlite3.Connection):
        return db, False
    path = db or os.environ.get("DATAFEED_DB")
    if not path:
        raise RuntimeError("pass db= or set DATAFEED_DB")
    return connect(path, readonly=True), True


def bars_before(symbol, t, lookback, db=None, known_by=None) -> list[dict]:
    """Bars with bar_ts < t, one per minute (live preferred, history as
    fallback), oldest first.

    lookback: int -> at most that many bars (the most recent ones);
              timedelta -> bars with bar_ts >= t - lookback.
    db: sqlite3.Connection or path (default $DATAFEED_DB)."""
    con, own = _con(db)
    try:
        t_s = canonical(t)
        where = ["b.symbol = ?", "b.bar_ts < ?"]
        args: list = [symbol, t_s]
        live_known = ""
        if known_by is not None:
            k = canonical(known_by)
            where.append("b.ingest_ts <= ?")
            args.append(k)
            live_known = "AND l.ingest_ts <= ?"
        if isinstance(lookback, timedelta):
            where.append("b.bar_ts >= ?")
            args.append(canonical(t - lookback))
            limit, largs = "", []
        else:
            limit, largs = "LIMIT ?", [int(lookback)]
        sql = f"""
            SELECT {', '.join('b.' + c.strip() for c in COLUMNS.split(','))}
            FROM bars b
            WHERE {' AND '.join(where)}
              AND (b.kind = 'live' OR NOT EXISTS (
                     SELECT 1 FROM bars l
                     WHERE l.symbol = b.symbol AND l.bar_ts = b.bar_ts
                       AND l.kind = 'live' {live_known}))
            ORDER BY b.bar_ts DESC {limit}"""
        if known_by is not None:
            args.append(canonical(known_by))
        rows = con.execute(sql, args + largs).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        if own:
            con.close()


def latest_bar_ts(symbol, db=None, kind: str | None = None) -> str | None:
    """Newest bar_ts stored for symbol (optionally one kind), for staleness checks."""
    con, own = _con(db)
    try:
        sql, args = "SELECT MAX(bar_ts) FROM bars WHERE symbol = ?", [symbol]
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        return con.execute(sql, args).fetchone()[0]
    finally:
        if own:
            con.close()


def compare_live_vs_history(symbol, start, end, db=None) -> dict:
    """Per-minute live minus history deltas for minutes in [start, end) that
    have both kinds, plus summary stats.

    Returns {"rows": [{bar_ts, d_open, d_high, d_low, d_close}, ...],
             "summary": {n_both, n_live_only, n_history_only,
                         per field: mean, mean_abs, max_abs}}."""
    con, own = _con(db)
    try:
        s, e = canonical(start), canonical(end)
        rows = con.execute("""
            SELECT l.bar_ts,
                   l.open - h.open AS d_open, l.high - h.high AS d_high,
                   l.low - h.low AS d_low,   l.close - h.close AS d_close
            FROM bars l JOIN bars h
              ON h.symbol = l.symbol AND h.bar_ts = l.bar_ts AND h.kind = 'history'
            WHERE l.symbol = ? AND l.kind = 'live' AND l.bar_ts >= ? AND l.bar_ts < ?
            ORDER BY l.bar_ts""", (symbol, s, e)).fetchall()
        counts = dict(con.execute("""
            SELECT kind, COUNT(*) FROM bars
            WHERE symbol = ? AND bar_ts >= ? AND bar_ts < ? GROUP BY kind""",
                                  (symbol, s, e)).fetchall())
        out = [dict(r) for r in rows]
        n = len(out)
        summary = {"n_both": n,
                   "n_live_only": counts.get("live", 0) - n,
                   "n_history_only": counts.get("history", 0) - n}
        for f in ("d_open", "d_high", "d_low", "d_close"):
            v = [r[f] for r in out]
            summary[f] = ({"mean": sum(v) / n, "mean_abs": sum(map(abs, v)) / n,
                           "max_abs": max(map(abs, v))} if n else None)
        return {"rows": out, "summary": summary}
    finally:
        if own:
            con.close()
