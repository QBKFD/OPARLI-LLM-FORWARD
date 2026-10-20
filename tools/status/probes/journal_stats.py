"""Scanner and journal facts for health.json. Runs INSIDE the scanner container,
fed on stdin by watchdog.sh:

    docker compose exec -T scanner python - < tools/status/probes/journal_stats.py

Prints one `key=value` fact per line (see tools/status/evaluate.py). Reads the
journal read-only (sqlite mode=ro); never writes. Stdlib plus datafeed's
_market_open (in the image). Any failure prints journal_ok=0 with the reason,
so the board shows red instead of the watchdog failing.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Callable

MarketOpen = Callable[[datetime], bool]

MAX_MINUTES = 100_000            # cap the market-open count (~10 weeks of minutes)


def utc(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def open_minutes(start: datetime, end: datetime, market_open: MarketOpen) -> int:
    """Complete market-open minutes m with start <= m and m + 1 min <= end."""
    n, t = 0, start
    while t + timedelta(minutes=1) <= end and n < MAX_MINUTES:
        n += bool(market_open(t))
        t += timedelta(minutes=1)
    return n


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


def stats(con: sqlite3.Connection, now: datetime, market_open: MarketOpen) -> dict[str, str]:
    out: dict[str, str] = {"journal_ok": "1"}
    try:
        row = con.execute("SELECT value FROM scanner_state WHERE key = 'last_live_bar_ts'").fetchone()
    except sqlite3.OperationalError:              # journal before migration 3
        row = None
    if row:
        t = utc(row[0])
        out["scanner_last_bar_ts"] = t.strftime("%Y-%m-%dT%H:%MZ")
        out["scanner_last_bar_age_min"] = str(open_minutes(t + timedelta(minutes=1), now, market_open))
    else:
        out["scanner_last_bar_age_min"] = "-1"
    a_cols, d_cols = _cols(con, "alerts"), _cols(con, "decisions")
    sent = "a.status = 'sent'" if "status" in a_cols else "1 = 1"     # status: migration 3
    last = con.execute(f"SELECT MAX(a.alert_ts_utc) FROM alerts a WHERE {sent}").fetchone()[0]
    out["scanner_last_alert_ts"] = last.replace(" ", "T") + "Z" if last else ""
    ep = "epoch" if "epoch" in d_cols and "epoch" in a_cols else None
    key = "alert_id, epoch" if ep else "alert_id"
    join = "f.alert_id = a.alert_id" + (" AND f.epoch = a.epoch" if ep else "")
    today = now.date().isoformat()
    first = (f"SELECT d.decision, d.alert_id{', d.epoch' if ep else ''} FROM decisions d "
             f"JOIN (SELECT MIN(rowid) r FROM decisions GROUP BY {key}) m ON d.rowid = m.r")
    counts = dict(con.execute(
        f"SELECT f.decision, COUNT(*) FROM alerts a JOIN ({first}) f ON {join} "
        f"WHERE substr(a.alert_ts_utc, 1, 10) = ? AND {sent} GROUP BY f.decision", (today,)).fetchall())
    for d in ("TAKE", "SKIP", "MISSED"):
        out[f"journal_{d.lower()}"] = str(counts.get(d, 0))
    und = con.execute(
        f"SELECT COUNT(*), MIN(a.alert_ts_utc) FROM alerts a LEFT JOIN decisions f ON {join} "
        f"WHERE substr(a.alert_ts_utc, 1, 10) = ? AND {sent} AND f.alert_id IS NULL", (today,)).fetchone()
    out["journal_undecided"] = str(und[0])
    out["journal_undecided_oldest_min"] = (str(int((now - utc(und[1])).total_seconds() // 60))
                                           if und[1] else "-1")
    return out


def main() -> None:
    path = os.environ.get("SCANNER_DB", "/data/scanner_decisions.sqlite")
    try:
        from datafeed.ingest.xauusd_bars import _market_open
        if not os.path.isfile(path):
            raise FileNotFoundError(f"no journal at {path}")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            facts = stats(con, datetime.now(timezone.utc), _market_open)
        finally:
            con.close()
    except Exception as e:                        # noqa: BLE001 - reported as a fact, shown red
        facts = {"journal_ok": "0", "journal_error": f"{type(e).__name__}: {e}".replace("\n", " ")[:200]}
    print("\n".join(f"{k}={v}" for k, v in facts.items()))


if __name__ == "__main__":
    main()
