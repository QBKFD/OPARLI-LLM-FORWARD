"""The options snapshot store: one SQLite file of its own (default
/data/options.sqlite, OPTIONS_DB), written once a day by backend.options.snapshot.

- Rollback-journal mode (not WAL): one writer, once a day, and no reader while
  it writes, so the file is always self-contained.
- Migrations are numbered SQL scripts (MIGRATIONS), applied in order inside
  one transaction each and recorded in schema_version. Never edit an applied
  migration; add the next number.
- Timestamps are TEXT, ISO-8601 UTC with milliseconds ("...T05:00:12.250Z"),
  so string order is time order. Dates are "YYYY-MM-DD"; expiries "YYYYMMDD".
- Values are stored as IBKR sent them: IBKR's -1 for "no bid / no ask" stays
  -1, and NULL means no value arrived.

option_runs
    One row per invocation, whatever happened: status ok (a snapshot, with
    its rows below), skipped (the market was closed, a CME holiday, error
    10197 from a competing live session, or the trade date already has an ok
    snapshot) or failed. `reason` says why for skipped and failed. At most one
    ok run per trade date (a partial unique index).

option_quotes
    One row per OG option contract of an ok run: the contract (expiry, strike,
    right, underlying), where its values came from (data_type live / frozen /
    none, and IBKR's own market data type), the values, and when each value
    was first received and last changed (field_times, JSON
    {field: [t_first, t_last]}).

underlying_quotes
    The same for the GC futures the options are written on (by each option's
    underConId): bid, ask, last, close and futures open interest.

ok_option_quotes (view)
    option_quotes of ok runs, with the run's trade date and OI date.
"""

from __future__ import annotations

import json
import os
import sqlite3

DEFAULT_DB = "/data/options.sqlite"

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
    CREATE TABLE option_runs (
        run_id               INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_date           TEXT    NOT NULL,   -- CME trade date of started_utc
        status               TEXT    NOT NULL CHECK (status IN ('ok', 'skipped', 'failed')),
        exit_code            INTEGER NOT NULL,
        reason               TEXT,               -- NULL for ok
        started_utc          TEXT    NOT NULL,
        ended_utc            TEXT    NOT NULL,
        oi_settlement_date   TEXT,               -- inferred: the trade date before trade_date
        client_id            INTEGER,
        selected_expiries    TEXT,               -- JSON list
        expiry_fallback      TEXT,               -- why fewer than 3 expiries, else NULL
        chain_futures_queried TEXT,              -- JSON list of GC local symbols
        expiry_sources       TEXT,               -- JSON: per expiry, the underlyings from its chain and details
        params               TEXT,               -- JSON
        max_concurrent_lines INTEGER,
        n_options            INTEGER,
        n_live               INTEGER,
        n_frozen             INTEGER,
        n_none               INTEGER,
        ib_messages          TEXT                -- JSON list of IBKR error/notice events
    );
    CREATE UNIQUE INDEX option_runs_one_ok_per_day ON option_runs (trade_date) WHERE status = 'ok';

    CREATE TABLE option_quotes (
        run_id               INTEGER NOT NULL REFERENCES option_runs (run_id),
        con_id               INTEGER NOT NULL,
        local_symbol         TEXT    NOT NULL,
        expiry               TEXT    NOT NULL,   -- YYYYMMDD
        strike               REAL    NOT NULL,
        right                TEXT    NOT NULL CHECK (right IN ('C', 'P')),
        multiplier           TEXT,
        under_con_id         INTEGER NOT NULL,   -- the contract's underConId (contract details)
        chain_under_con_id   INTEGER,            -- the underlying whose option chain listed this expiry
        real_expiration_date TEXT,
        last_trade_time      TEXT,
        time_zone_id         TEXT,
        data_type            TEXT    NOT NULL CHECK (data_type IN ('live', 'frozen', 'none')),
        ib_market_data_type  INTEGER,
        open_interest        REAL,
        iv                   REAL,               -- IBKR model implied volatility
        gamma                REAL,               -- IBKR model greeks
        delta                REAL,
        vega                 REAL,
        und_price            REAL,               -- IBKR model's underlying price
        opt_price            REAL,               -- IBKR model's option price
        bid                  REAL,
        ask                  REAL,
        last                 REAL,
        close                REAL,
        first_tick_utc       TEXT,
        last_tick_utc        TEXT,
        field_times          TEXT    NOT NULL,   -- JSON {field: [t_first, t_last]}
        PRIMARY KEY (run_id, con_id)
    ) WITHOUT ROWID;
    CREATE INDEX option_quotes_expiry_strike ON option_quotes (expiry, strike);

    CREATE TABLE underlying_quotes (
        run_id               INTEGER NOT NULL REFERENCES option_runs (run_id),
        con_id               INTEGER NOT NULL,
        local_symbol         TEXT    NOT NULL,
        expiry               TEXT,
        data_type            TEXT    NOT NULL CHECK (data_type IN ('live', 'frozen', 'none')),
        ib_market_data_type  INTEGER,
        bid                  REAL,
        ask                  REAL,
        last                 REAL,
        close                REAL,
        open_interest        REAL,               -- futures open interest (generic tick 588)
        first_tick_utc       TEXT,
        last_tick_utc        TEXT,
        field_times          TEXT    NOT NULL,
        PRIMARY KEY (run_id, con_id)
    ) WITHOUT ROWID;

    CREATE VIEW ok_option_quotes AS
        SELECT r.trade_date, r.oi_settlement_date, q.*
        FROM option_quotes q JOIN option_runs r USING (run_id)
        WHERE r.status = 'ok';
    """),
]

# option_quotes column <- Recorder field name
OPTION_VALUES = (("open_interest", "open_interest"), ("iv", "model_iv"), ("gamma", "model_gamma"),
                 ("delta", "model_delta"), ("vega", "model_vega"), ("und_price", "model_und_price"),
                 ("opt_price", "model_opt_price"), ("bid", "bid"), ("ask", "ask"), ("last", "last"),
                 ("close", "close"))
UNDERLYING_VALUES = (("bid", "bid"), ("ask", "ask"), ("last", "last"), ("close", "close"),
                     ("open_interest", "open_interest"))


def connect(path: str) -> sqlite3.Connection:
    """Opens (creating if needed) and migrates the store."""
    if os.path.dirname(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON")
    migrate(con)
    return con


def migrate(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_utc TEXT NOT NULL)")
    have = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    for version, sql in MIGRATIONS:
        if version in have:
            continue
        con.execute("BEGIN IMMEDIATE")
        try:
            for stmt in (s for s in sql.split(";") if s.strip()):
                con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))", (version,))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    return max(v for v, _ in MIGRATIONS)


def ok_run_for(con: sqlite3.Connection, trade_date: str) -> int | None:
    row = con.execute("SELECT run_id FROM option_runs WHERE trade_date = ? AND status = 'ok'",
                      (trade_date,)).fetchone()
    return row[0] if row else None


def _times(fields: dict) -> tuple[str | None, str | None, str]:
    firsts = [f["t_first"] for f in fields.values()]
    lasts = [f["t_last"] for f in fields.values()]
    return (min(firsts) if firsts else None, max(lasts) if lasts else None,
            json.dumps({k: [f["t_first"], f["t_last"]] for k, f in sorted(fields.items())}))


def _value(fields: dict, name: str):
    f = fields.get(name)
    return None if f is None else f["value"]


def _js(v):
    return None if v is None else json.dumps(v)


def record_run(con: sqlite3.Connection, run: dict, options: list[dict] = (), underlyings: list[dict] = ()) -> int:
    """One transaction: the run row, then (for ok runs) its quotes. Returns run_id.

    run: trade_date, status, exit_code, started_utc, ended_utc, and optionally
    reason, oi_settlement_date, client_id, selected_expiries, expiry_fallback,
    chain_futures_queried, expiry_sources, params, max_concurrent_lines,
    ib_messages. options / underlyings: dicts with the contract keys and
    data_type, ib_market_data_type and fields (Recorder.fields)."""
    kinds = [o["data_type"] for o in options]
    con.execute("BEGIN IMMEDIATE")
    try:
        cur = con.execute(
            "INSERT INTO option_runs (trade_date, status, exit_code, reason, started_utc, ended_utc, "
            "oi_settlement_date, client_id, selected_expiries, expiry_fallback, chain_futures_queried, "
            "expiry_sources, params, max_concurrent_lines, n_options, n_live, n_frozen, n_none, ib_messages) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run["trade_date"], run["status"], run["exit_code"], run.get("reason"), run["started_utc"],
             run["ended_utc"], run.get("oi_settlement_date"), run.get("client_id"),
             _js(run.get("selected_expiries")), run.get("expiry_fallback"), _js(run.get("chain_futures_queried")),
             _js(run.get("expiry_sources")), _js(run.get("params")), run.get("max_concurrent_lines"),
             len(options) if options else None, kinds.count("live") if options else None,
             kinds.count("frozen") if options else None, kinds.count("none") if options else None,
             _js(run.get("ib_messages"))))
        run_id = cur.lastrowid
        for o in options:
            first, last, times = _times(o["fields"])
            con.execute(
                "INSERT INTO option_quotes (run_id, con_id, local_symbol, expiry, strike, right, multiplier, "
                "under_con_id, chain_under_con_id, real_expiration_date, last_trade_time, time_zone_id, "
                "data_type, ib_market_data_type, " + ", ".join(c for c, _ in OPTION_VALUES) +
                ", first_tick_utc, last_tick_utc, field_times) VALUES (" + ", ".join("?" * (17 + len(OPTION_VALUES))) + ")",
                (run_id, o["con_id"], o["local_symbol"], o["expiry"], o["strike"], o["right"], o.get("multiplier"),
                 o["under_con_id"], o.get("chain_under_con_id"), o.get("real_expiration_date"),
                 o.get("last_trade_time"), o.get("time_zone_id"), o["data_type"], o.get("ib_market_data_type"),
                 *(_value(o["fields"], f) for _, f in OPTION_VALUES), first, last, times))
        for u in underlyings:
            first, last, times = _times(u["fields"])
            con.execute(
                "INSERT INTO underlying_quotes (run_id, con_id, local_symbol, expiry, data_type, "
                "ib_market_data_type, " + ", ".join(c for c, _ in UNDERLYING_VALUES) +
                ", first_tick_utc, last_tick_utc, field_times) VALUES (" + ", ".join("?" * (9 + len(UNDERLYING_VALUES))) + ")",
                (run_id, u["con_id"], u["local_symbol"], u.get("expiry"), u["data_type"], u.get("ib_market_data_type"),
                 *(_value(u["fields"], f) for _, f in UNDERLYING_VALUES), first, last, times))
        con.execute("COMMIT")
    except BaseException:
        con.execute("ROLLBACK")
        raise
    return run_id
