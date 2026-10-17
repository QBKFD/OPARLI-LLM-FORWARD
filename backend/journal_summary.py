#!/usr/bin/env python3
"""Daily summary of the scanner's decision journal. Read-only, stdlib only.

    python backend/journal_summary.py [--db PATH] [--date YYYY-MM-DD] [--json]

For one UTC day (default: today), by journal epoch:
  alerts     by status: sent, missed_while_down, missed_history
  decisions  the FIRST decision per sent alert (the one that counts): TAKE,
             SKIP, MISSED, and sent alerts still undecided
  revisions  later decision rows for alerts already decided (logged, not counted)
  outcomes   for the day's TAKEs: tp, sl, timeout (with the sum of R), unscorable
And, over the whole journal: open outcomes = first-decision TAKEs with no
outcome row yet.

The journal is opened read-only (sqlite URI mode=ro): the command never
creates, migrates or writes it, so it is safe on the production journal
(docker compose exec scanner python /app/journal_summary.py). Journals from
before migration 3 (no alerts.status) or 4 (no outcome detail) still work.
Exit codes: 0 ok, 2 no journal or bad arguments.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "database/scanner_decisions.sqlite"
ALERT_STATUSES = ("sent", "missed_while_down", "missed_history")
DECISIONS = ("TAKE", "SKIP", "MISSED")
EXIT_TYPES = ("tp", "sl", "timeout", "unscorable")


@dataclass
class EpochSummary:
    """Counts for one epoch on one day."""
    epoch: int
    alerts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(ALERT_STATUSES, 0))
    decisions: dict[str, int] = field(default_factory=lambda: dict.fromkeys(DECISIONS, 0))
    undecided: int = 0
    revisions: int = 0
    outcomes: dict[str, int] = field(default_factory=lambda: dict.fromkeys(EXIT_TYPES, 0))
    sum_r: float = 0.0
    open_outcomes: int = 0


@dataclass
class DaySummary:
    day: str
    epochs: list[EpochSummary]
    open_outcomes_all: int                 # first-decision TAKEs without an outcome, any day
    journal_version: int


def open_readonly(path: str | Path) -> sqlite3.Connection:
    """The journal, read-only. Raises FileNotFoundError if it doesn't exist
    (sqlite would otherwise create an empty file in rw mode)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no journal at {p}")
    return sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True)


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def _epoch_expr(cols: set[str], alias: str) -> str:
    return f"{alias}.epoch" if "epoch" in cols else "1"


def summarize(con: sqlite3.Connection, day: date) -> DaySummary:
    """Counts for alerts whose alert_ts_utc falls on `day` (UTC)."""
    a_cols, d_cols, o_cols = (_columns(con, t) for t in ("alerts", "decisions", "outcomes"))
    a_ep, d_ep, o_ep = _epoch_expr(a_cols, "a"), _epoch_expr(d_cols, "d"), _epoch_expr(o_cols, "o")
    status = "a.status" if "status" in a_cols else "'sent'"
    day_s = day.isoformat()
    by_epoch: dict[int, EpochSummary] = {}

    def ep(e: int) -> EpochSummary:
        return by_epoch.setdefault(e, EpochSummary(epoch=e))

    for e, st, n in con.execute(
            f"SELECT {a_ep}, {status}, COUNT(*) FROM alerts a WHERE substr(a.alert_ts_utc, 1, 10) = ? "
            f"GROUP BY 1, 2", (day_s,)):
        ep(e).alerts[st] = ep(e).alerts.get(st, 0) + n

    # the first decision per (alert, epoch) counts; later rows are revisions
    first = (f"SELECT d.alert_id, {d_ep} AS epoch, MIN(d.rowid) AS r, COUNT(*) AS n "
             f"FROM decisions d GROUP BY d.alert_id, {d_ep}")
    rows = con.execute(
        f"SELECT {a_ep}, d.decision, f.n FROM alerts a "
        f"JOIN ({first}) f ON f.alert_id = a.alert_id AND f.epoch = {a_ep} "
        f"JOIN decisions d ON d.rowid = f.r "
        f"WHERE substr(a.alert_ts_utc, 1, 10) = ? AND {status} = 'sent'", (day_s,)).fetchall()
    for e, decision, n in rows:
        s = ep(e)
        s.decisions[decision] = s.decisions.get(decision, 0) + 1
        s.revisions += n - 1
    for e, n in con.execute(
            f"SELECT {a_ep}, COUNT(*) FROM alerts a LEFT JOIN decisions d "
            f"ON d.alert_id = a.alert_id AND {d_ep} = {a_ep} "
            f"WHERE substr(a.alert_ts_utc, 1, 10) = ? AND {status} = 'sent' AND d.alert_id IS NULL "
            f"GROUP BY 1", (day_s,)):
        ep(e).undecided = n

    takes = (f"SELECT a.alert_id, {a_ep} AS epoch, substr(a.alert_ts_utc, 1, 10) AS day "
             f"FROM alerts a JOIN ({first}) f ON f.alert_id = a.alert_id AND f.epoch = {a_ep} "
             f"JOIN decisions d ON d.rowid = f.r WHERE d.decision = 'TAKE' AND {status} = 'sent'")
    for e, exit_type, r_multiple, day_t in con.execute(
            f"SELECT t.epoch, o.exit_type, o.r_multiple, t.day FROM ({takes}) t "
            f"LEFT JOIN outcomes o ON o.alert_id = t.alert_id AND {o_ep} = t.epoch"):
        if exit_type is None:
            if day_t == day_s:
                ep(e).open_outcomes += 1
            continue
        if day_t != day_s:
            continue
        s = ep(e)
        s.outcomes[exit_type] = s.outcomes.get(exit_type, 0) + 1
        if exit_type in ("tp", "sl", "timeout") and r_multiple is not None:
            s.sum_r = round(s.sum_r + float(r_multiple), 4)
    open_all = con.execute(
        f"SELECT COUNT(*) FROM ({takes}) t LEFT JOIN outcomes o "
        f"ON o.alert_id = t.alert_id AND {o_ep} = t.epoch WHERE o.alert_id IS NULL").fetchone()[0]
    version = con.execute("PRAGMA user_version").fetchone()[0]
    return DaySummary(day=day_s, epochs=[by_epoch[e] for e in sorted(by_epoch)],
                      open_outcomes_all=open_all, journal_version=version)


def format_summary(s: DaySummary) -> str:
    """Plain text, one block per epoch; ASCII only, so it reads in any terminal or chat."""
    lines = [f"journal {s.day} (UTC), journal version {s.journal_version}"]
    if not s.epochs:
        lines.append("  no alerts")
    for e in s.epochs:
        a, d, o = e.alerts, e.decisions, e.outcomes
        lines += [
            f"  epoch {e.epoch}",
            f"    alerts    {sum(a.values())}: sent {a.get('sent', 0)}, missed_while_down "
            f"{a.get('missed_while_down', 0)}, missed_history {a.get('missed_history', 0)}",
            f"    decisions TAKE {d.get('TAKE', 0)}, SKIP {d.get('SKIP', 0)}, MISSED {d.get('MISSED', 0)}, "
            f"undecided {e.undecided}" + (f" (+{e.revisions} later replies, not counted)" if e.revisions else ""),
            f"    outcomes  tp {o.get('tp', 0)}, sl {o.get('sl', 0)}, timeout {o.get('timeout', 0)}, "
            f"unscorable {o.get('unscorable', 0)}, open {e.open_outcomes}; sum R {e.sum_r:+.2f}",
        ]
    lines.append(f"  open outcomes, whole journal: {s.open_outcomes_all}")
    return "\n".join(lines)


def _day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (YYYY-MM-DD): {text!r}") from None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Daily summary of the scanner decision journal (read-only).")
    ap.add_argument("--db", default=os.environ.get("SCANNER_DB", str(DEFAULT_DB)),
                    help="journal path (default: $SCANNER_DB, else database/scanner_decisions.sqlite)")
    ap.add_argument("--date", type=_day, default=None, help="UTC day, YYYY-MM-DD (default: today)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    args = ap.parse_args(argv)
    day = args.date or datetime.now(timezone.utc).date()
    try:
        con = open_readonly(args.db)
    except FileNotFoundError as e:
        print(f"journal_summary: {e}", file=sys.stderr)
        return 2
    try:
        s = summarize(con, day)
    finally:
        con.close()
    print(json.dumps(asdict(s), sort_keys=True) if args.json else format_summary(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
