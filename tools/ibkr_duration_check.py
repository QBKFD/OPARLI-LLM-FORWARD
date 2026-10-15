#!/usr/bin/env python3
"""One-off check, PAPER GATEWAY ONLY: for 1-min bars, does IBKR's
durationStr "10 D" count calendar days or trading days (sessions)?
Open question in docs/SCANNER_SWITCH_NOTES.md, 5c; the official docs don't say.

    python tools/ibkr_duration_check.py --host 127.0.0.1 --port 4002 [--client-id 41]

Guards, all checked before anything is requested:
  - the port must be a paper port: 4002 (IB Gateway paper), 4004 (the
    ib-gateway image's paper relay) or 7497 (TWS paper);
  - the connection is readonly, with its own clientId (default 41; the stack
    uses 11, 21, 31, 996, 999);
  - every managed account must be a paper account (starts with "DU").
  Otherwise it exits 3 without requesting any data.

Then one request: XAUUSD CMDTY, endDateTime "", the duration, "1 min",
MIDPOINT, useRTH False, formatDate 2 (the scanner's and the feed's exact
request). It prints JSON:
  - first and last bar;
  - the calendar span;
  - the trading sessions covered (a session rolls over at 17:00 New York);
  - a verdict: "calendar days", "trading days" or "inconclusive".
Run it on a Thursday or Friday. There the two readings differ the most (a
10-calendar-day window then starts inside the previous week).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PAPER_PORTS = (4002, 4004, 7497)
NY = ZoneInfo("America/New_York")
TOLERANCE = timedelta(hours=1)


class NotPaper(Exception):
    pass


def session_of(ts: datetime):
    """Trading session date: rolls over at 17:00 New York (the daily break)."""
    return (ts.astimezone(NY) + timedelta(hours=7)).date()


def analyse(bar_ts, now: datetime, days: int) -> dict:
    """Interpret what a `days` D request returned.

    calendar days  no bar older than now - days (with a 1 h tolerance), and
                   fewer than `days` sessions (10 calendar days always hold a
                   weekend);
    trading days   bars older than now - days, covering `days` sessions, or
                   days + 1 when the current session is still running;
    inconclusive   anything else (e.g. a holiday-shortened window): check by
                   hand."""
    ts = sorted(bar_ts)
    if not ts:
        return {"bars": 0, "verdict": "inconclusive", "why": "no bars returned"}
    first, last = ts[0], ts[-1]
    sessions = sorted({session_of(t) for t in ts})
    older = first < now - timedelta(days=days) - TOLERANCE
    if not older and len(sessions) < days:
        verdict = "calendar days"
    elif older and len(sessions) in (days, days + 1):
        verdict = "trading days"
    else:
        verdict = "inconclusive"
    return {"bars": len(ts), "first_bar": first.isoformat(), "last_bar": last.isoformat(),
            "now": now.isoformat(), "requested": f"{days} D",
            "calendar_span_days": round((now - first).total_seconds() / 86400, 2),
            "sessions": len(sessions), "first_session": str(sessions[0]),
            "last_session": str(sessions[-1]), "older_than_requested_calendar_days": older,
            "verdict": verdict}


def check_paper(port: int, accounts) -> None:
    if port not in PAPER_PORTS:
        raise NotPaper(f"port {port} is not a paper port {PAPER_PORTS}")
    accounts = list(accounts)
    if not accounts or not all(str(a).startswith("DU") for a in accounts):
        raise NotPaper(f"managed accounts {accounts} are not all paper (DU...)")


def run(host: str, port: int, client_id: int, days: int, ib_factory=None, now=None) -> dict:
    if port not in PAPER_PORTS:                         # before even connecting
        raise NotPaper(f"port {port} is not a paper port {PAPER_PORTS}")
    if ib_factory is None:
        from ib_insync import IB
        ib_factory = IB
    ib = ib_factory()
    ib.connect(host, port, clientId=client_id, timeout=15, readonly=True)
    try:
        check_paper(port, ib.managedAccounts())
        from ib_insync import Contract
        contract = ib.qualifyContracts(Contract(symbol="XAUUSD", secType="CMDTY", exchange="SMART",
                                                currency="USD"))[0]
        bars = ib.reqHistoricalData(contract, endDateTime="", durationStr=f"{days} D",
                                    barSizeSetting="1 min", whatToShow="MIDPOINT", useRTH=False,
                                    formatDate=2)
    finally:
        ib.disconnect()
    ts = [(b.date if b.date.tzinfo else b.date.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
          for b in bars]
    return analyse(ts, now or datetime.now(timezone.utc), days)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--client-id", type=int, default=41)
    ap.add_argument("--days", type=int, default=10)
    a = ap.parse_args(argv)
    try:
        out = run(a.host, a.port, a.client_id, a.days)
    except NotPaper as e:
        print(f"refused: {e}. This check runs against a paper gateway only.", file=sys.stderr)
        return 3
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
