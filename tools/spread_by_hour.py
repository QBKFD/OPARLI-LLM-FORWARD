#!/usr/bin/env python3
"""Spread of live XAUUSD bars by hour of day, from the datafeed store.

Reads the spread stats stored with each live 1-min bar (migration 4: ask - bid
over the minute's quotes) and summarises them per hour of day:

    hour    the bar's open hour in --tz (default UTC)
    bars    live bars with spread stats
    quotes  sum of n_quotes
    mean    quote-weighted mean spread = the mean over all quotes
    median  median of the per-bar median spreads
    p90     90th percentile of the per-bar mean spreads
    max     largest single-quote spread

History and replay bars have no quotes and are not counted. Spreads are in
price units (USD per oz). The store is opened read-only. Exit codes: 0 table
printed, 1 no live bars with spread stats in the window, 2 the store is missing
or unreadable, or predates migration 4 (the recorder applies it on its next
start; this tool never writes the store).

    python tools/spread_by_hour.py --db database/datafeed.sqlite \\
        [--start 2026-10-16T00:00:00+00:00] [--end 2026-10-30T00:00:00+00:00] \\
        [--tz America/New_York] [--csv out.csv]
"""

from __future__ import annotations

import argparse
import csv
import os
import sqlite3
import statistics
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from backend.datafeed.ingest.base import parse_utc
from backend.datafeed.store.query import spreads

COLUMNS = ("hour", "bars", "quotes", "mean", "median", "p90", "max")
Row = dict[str, Any]


def _p90(xs: list[float]) -> float:
    if len(xs) == 1:
        return xs[0]
    return statistics.quantiles(xs, n=10, method="inclusive")[-1]


def _stats(label: str, rows: list[Row]) -> Row:
    quotes = sum(r["n_quotes"] for r in rows)
    return {"hour": label, "bars": len(rows), "quotes": quotes,
            "mean": sum(r["spread_mean"] * r["n_quotes"] for r in rows) / quotes,
            "median": statistics.median(r["spread_median"] for r in rows),
            "p90": _p90([r["spread_mean"] for r in rows]),
            "max": max(r["spread_max"] for r in rows)}


def summarise(rows: list[Row], tz: str = "UTC") -> list[Row]:
    """One row per hour of day that has bars, in hour order, then an "all" row."""
    zone = ZoneInfo(tz)
    by_hour: dict[int, list[Row]] = {}
    for r in rows:
        h = datetime.fromisoformat(r["bar_ts"]).astimezone(zone).hour
        by_hour.setdefault(h, []).append(r)
    out = [_stats(f"{h:02d}", by_hour[h]) for h in sorted(by_hour)]
    return out + [_stats("all", rows)] if rows else out


def render(table: list[Row], tz: str) -> str:
    lines = [f"| hour ({tz}) | bars | quotes | mean | median | p90 | max |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for t in table:
        lines.append(f"| {t['hour']} | {t['bars']:,} | {t['quotes']:,} | {t['mean']:.3f} | "
                     f"{t['median']:.3f} | {t['p90']:.3f} | {t['max']:.3f} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("DATAFEED_DB"),
                    help="store path (default $DATAFEED_DB)")
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--start", type=parse_utc, help="first bar_ts, ISO-8601 with offset")
    ap.add_argument("--end", type=parse_utc, help="bar_ts before this, ISO-8601 with offset")
    ap.add_argument("--tz", default="UTC", help="time zone of the hour of day (default UTC)")
    ap.add_argument("--csv", help="also write the table to this CSV file")
    args = ap.parse_args(argv)
    if not args.db:
        ap.error("pass --db or set DATAFEED_DB")
    if not os.path.isfile(args.db):
        print(f"spread_by_hour: no store at {args.db}", file=sys.stderr)
        return 2
    try:
        rows = spreads(args.symbol, args.start, args.end, db=args.db)
    except sqlite3.OperationalError as e:
        print(f"spread_by_hour: can't read spreads from {args.db} ({e}); a store from before "
              f"migration 4 has no spread columns until the recorder restarts", file=sys.stderr)
        return 2
    if not rows:
        print("no live bars with spread stats in the window", file=sys.stderr)
        return 1
    table = summarise(rows, args.tz)
    print(render(table, args.tz))
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
