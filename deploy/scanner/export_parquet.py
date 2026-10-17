#!/usr/bin/env python3
"""Export the datafeed store's bars to daily parquet files for the S3 archive.
Runs inside the scanner image (pandas + pyarrow); backup.sh calls it daily.

    python export_parquet.py STORE OUTDIR [--last-days 7] [--today YYYY-MM-DD]

For each of the last N *completed* UTC days (today is never exported: it is
still being written), writes OUTDIR/date=YYYY-MM-DD/XAUUSD.parquet holding
every row of `bars` with bar_ts in that day:
  - all kinds (live, history, replay), oldest first, with these columns:
    symbol, bar_ts, kind, open, high, low, close, n_ticks, source,
    schema_version, ingest_ts, recorded_ts;
  - timestamps are UTC; n_ticks is a nullable integer.
A day without bars (a weekend, a closure) gets no file. Each file is read
back and must hold the same row count. The store is opened read-only. Prints
one line per written file: its path relative to OUTDIR, then the row count.

The archive holds data from 2026-10-01 onward, which research/SEALING.md
seals: no study may read it before the final strategy test.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

COLUMNS = ["symbol", "bar_ts", "kind", "open", "high", "low", "close", "n_ticks", "source",
           "schema_version", "ingest_ts", "recorded_ts"]


def _bound(d: date) -> str:
    # the store's canonical form (datafeed.store.schema.canonical): ISO-8601, UTC, seconds
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat()


def export_day(con, day: date, out_dir: str, symbol: str = "XAUUSD"):
    """Write one day; returns (relative path, rows) or None when there are no bars."""
    import pandas as pd
    df = pd.read_sql_query(
        f"SELECT {', '.join(COLUMNS)} FROM bars WHERE symbol = ? AND bar_ts >= ? AND bar_ts < ? "
        "ORDER BY bar_ts, kind", con, params=(symbol, _bound(day), _bound(day + timedelta(days=1))))
    if df.empty:
        return None
    for c in ("bar_ts", "ingest_ts", "recorded_ts"):
        df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601")
    df["n_ticks"] = df["n_ticks"].astype("Int64")
    rel = os.path.join(f"date={day.isoformat()}", f"{symbol}.parquet")
    path = os.path.join(out_dir, rel)
    if os.path.exists(path):
        raise SystemExit(f"export_parquet: {path} exists; refusing to overwrite")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".partial"
    df.to_parquet(tmp, index=False)
    back = pd.read_parquet(tmp)
    if len(back) != len(df) or list(back.columns) != COLUMNS:
        os.remove(tmp)
        raise SystemExit(f"export_parquet: read-back of {path} doesn't match ({len(back)} vs {len(df)} rows)")
    os.replace(tmp, path)
    return rel, len(df)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="export_parquet.py")
    ap.add_argument("store")
    ap.add_argument("outdir")
    ap.add_argument("--last-days", type=int, default=7)
    ap.add_argument("--today", help="UTC date to count back from (default: today)")
    ap.add_argument("--symbol", default=os.environ.get("INSTRUMENT") or "XAUUSD",
                    help="bars' symbol (default: $INSTRUMENT, else XAUUSD)")
    a = ap.parse_args(argv)
    if not os.path.isfile(a.store):
        print(f"export_parquet: {a.store}: no such file", file=sys.stderr)
        return 1
    today = date.fromisoformat(a.today) if a.today else datetime.now(timezone.utc).date()
    con = sqlite3.connect(f"file:{a.store}?mode=ro", uri=True)
    try:
        for i in range(a.last_days, 0, -1):
            done = export_day(con, today - timedelta(days=i), a.outdir, a.symbol)
            if done:
                print(f"{done[0]} {done[1]}")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
