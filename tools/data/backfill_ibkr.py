"""IBKR download for the XAUUSD 1-min backfill, 2026-02-01 .. 2026-09-30.

docs/BACKFILL_2026_PROPOSAL.md has the plan. This file is the download half:
it runs on the EC2 paper gateway and only talks to IBKR. The other half,
tools/data/backfill_build.py, runs where XAUUSD.txt is (the Mac): it picks
whatToShow on the overlap, validates, and writes the parquet and its manifest
entry.

One self-contained file (stdlib + ib_insync, Python 3.12), so it runs from
stdin in the scanner image already on the host. On the EC2 host, from
~/oparli/deploy/scanner:

    docker compose run --rm -T --no-deps --entrypoint python scanner - --probe-overlap \\
        < /tmp/backfill_ibkr.py > ~/backfill/overlap.jsonl
    docker compose run --rm -T --no-deps --entrypoint python scanner - --download MIDPOINT \\
        < /tmp/backfill_ibkr.py > ~/backfill/download_MIDPOINT.jsonl

Modes:
    --plan                  print every request, connect to nothing
    --probe-overlap         the last 10 trade days of XAUUSD.txt
                            (2026-01-19 .. 2026-01-30) as MIDPOINT, BID, ASK
                            and TRADES. A type IBKR refuses (error 162 "no
                            data", e.g. TRADES for CMDTY) is recorded, not fatal
    --download TYPE         2026-02-01 .. 2026-09-30 as TYPE (the one
                            backfill_build.py picked)

Requests: Contract XAUUSD CMDTY SMART USD (qualified; conId recorded), 1 min,
useRTH=False, formatDate=2 (UTC epoch, so the login's time zone can't leak
in), durationStr "1 D", endDateTime "<next day>-00:00:00" (UTC), one per
calendar day except Saturdays (no trading), walking backwards. At most 50
requests per 10 minutes (IBKR allows 60). Nothing at or after 2026-10-01 is
requested, and a bar at or after it stops the run (exit 3).

Output (stdout, JSON lines): one "meta" line, one "request" line per request
(its parameters, the bar count, any IBKR errors), one "bar" line per bar
(type, epoch seconds UTC, open, high, low, close, volume), and an "end" line.
A file without the "end" line is incomplete: backfill_build.py refuses it.
Logs go to stderr.

Exit status: 0 done; 2 bad arguments; 3 a bar in the sealed period;
4 error 10197 (a competing live session; log out everywhere, run again);
5 a pacing violation (wait 10 minutes, run again); 1 anything else. Never
retries in a loop: the person running it decides.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

SCHEMA = "backfill_ibkr/1"
CLIENT_ID = 63                     # 61 options probe, 62 options snapshot, 11/21 scanner, 31 feed
SEAL = datetime(2026, 10, 1, tzinfo=timezone.utc)
RANGE = (date(2026, 2, 1), date(2026, 9, 30))           # inclusive, calendar days (UTC)
OVERLAP = (date(2026, 1, 19), date(2026, 1, 30))        # the last 10 trade days of XAUUSD.txt
PROBE_TYPES = ("MIDPOINT", "BID", "ASK", "TRADES")
BAR_SIZE, DURATION = "1 min", "1 D"
MAX_PER_WINDOW, WINDOW_S = 50, 600
TIMEOUT_S = 120


class Stop(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%H:%M:%S} backfill_ibkr: {msg}", file=sys.stderr, flush=True)


def days(first: date, last: date) -> list[date]:
    """Calendar days to request, newest first, Saturdays left out."""
    out, d = [], last
    while d >= first:
        if d.weekday() != 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def end_of(d: date) -> str:
    """The request's endDateTime: the next day 00:00:00 UTC, in IBKR's UTC form."""
    return (d + timedelta(days=1)).strftime("%Y%m%d-00:00:00")


def plan(mode: str, what: str | None) -> list[tuple[str, date]]:
    if mode == "probe":
        return [(t, d) for t in PROBE_TYPES for d in days(*OVERLAP)]
    return [(what, d) for d in days(*RANGE)]


class Pacer:
    """At most MAX_PER_WINDOW requests in any WINDOW_S seconds."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep, self.sent = clock, sleep, []

    def wait(self) -> None:
        while True:
            now = self.clock()
            self.sent = [t for t in self.sent if now - t < WINDOW_S]
            if len(self.sent) < MAX_PER_WINDOW:
                self.sent.append(now)
                return
            self.sleep(WINDOW_S - (now - self.sent[0]) + 0.5)


def num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def epoch(t) -> int:
    if isinstance(t, datetime):
        if t.tzinfo is None:
            raise Stop(f"IBKR returned a naive bar time {t!r}; formatDate=2 should give UTC", 1)
        return int(t.timestamp())
    if isinstance(t, (int, float)) or (isinstance(t, str) and t.isdigit()):
        return int(t)
    raise Stop(f"unexpected bar time {t!r}", 1)


class Backfill:
    def __init__(self, ib, contract_cls, out, *, pacer=None, now=lambda: datetime.now(timezone.utc)):
        self.ib, self.Contract, self.out = ib, contract_cls, out
        self.pacer, self.now = pacer or Pacer(), now
        self.req_errors: list[dict] = []
        self.stop: Stop | None = None
        ib.errorEvent += self.on_error

    def emit(self, rec: dict) -> None:
        self.out.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def on_error(self, reqId, errorCode, errorString, contract=None):
        self.req_errors.append({"code": errorCode, "message": errorString})
        if errorCode == 10197:
            self.stop = Stop("IBKR error 10197: a competing live session. Log the live account out "
                             "everywhere (TWS, mobile, web) and run again.", 4)
        elif errorCode == 162 and "pacing" in str(errorString).lower():
            self.stop = Stop(f"IBKR pacing violation: {errorString}. Wait 10 minutes and run again.", 5)

    def contract(self):
        c = self.Contract(symbol="XAUUSD", secType="CMDTY", exchange="SMART", currency="USD")
        got = self.ib.qualifyContracts(c)
        if not got:
            raise Stop("IBKR could not qualify XAUUSD CMDTY SMART USD", 1)
        return got[0]

    def run(self, mode: str, what: str | None) -> None:
        c = self.contract()
        reqs = plan(mode, what)
        self.emit({"kind": "meta", "schema": SCHEMA, "mode": mode, "what_to_show": what,
                   "started_utc": self.now().isoformat(), "client_id": CLIENT_ID,
                   "contract": {"con_id": c.conId, "symbol": c.symbol, "sec_type": c.secType,
                                "exchange": c.exchange, "currency": c.currency,
                                "local_symbol": getattr(c, "localSymbol", "")},
                   "bar_size": BAR_SIZE, "duration": DURATION, "use_rth": False, "format_date": 2,
                   "range": [str(x) for x in (OVERLAP if mode == "probe" else RANGE)],
                   "n_requests": len(reqs)})
        log(f"{mode}: {len(reqs)} requests, contract conId {c.conId}")
        n_bars = 0
        for i, (t, d) in enumerate(reqs, 1):
            self.pacer.wait()
            self.req_errors = []
            bars = self.ib.reqHistoricalData(c, endDateTime=end_of(d), durationStr=DURATION,
                                             barSizeSetting=BAR_SIZE, whatToShow=t, useRTH=False,
                                             formatDate=2, keepUpToDate=False, timeout=TIMEOUT_S)
            if self.stop:
                raise self.stop
            rows = []
            for b in bars or []:
                ts = epoch(b.date)
                if ts >= SEAL.timestamp():
                    raise Stop(f"bar at {datetime.fromtimestamp(ts, timezone.utc)} is in the sealed period", 3)
                rows.append({"kind": "bar", "type": t, "t": ts, "o": num(b.open), "h": num(b.high),
                             "l": num(b.low), "c": num(b.close), "v": num(b.volume)})
            for r in rows:
                self.emit(r)
            n_bars += len(rows)
            self.emit({"kind": "request", "type": t, "day": str(d), "end": end_of(d), "bars": len(rows),
                       "errors": self.req_errors})
            if i % 10 == 0 or i == len(reqs):
                log(f"{i}/{len(reqs)} requests, {n_bars} bars")
        self.emit({"kind": "end", "ended_utc": self.now().isoformat(), "n_bars": n_bars})


def main(argv=None, *, ib=None, contract_cls=None, out=None, pacer=None, now=None) -> int:
    ap = argparse.ArgumentParser(prog="backfill_ibkr.py", description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true")
    g.add_argument("--probe-overlap", action="store_true")
    g.add_argument("--download", metavar="TYPE", choices=PROBE_TYPES)
    ap.add_argument("--host", default=os.environ.get("IB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IB_PORT", "4004")))
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    a = ap.parse_args(argv)
    mode, what = ("probe", None) if a.probe_overlap else ("download", a.download)
    out = out or sys.stdout
    if a.plan:
        for m, w in (("probe", None), ("download", "<TYPE>")):
            reqs = plan(m, w)
            print(f"{m}: {len(reqs)} requests, {reqs[-1][1]} .. {reqs[0][1]}, "
                  f"about {len(reqs) / MAX_PER_WINDOW * WINDOW_S / 60:.0f} min", file=out)
        return 0
    if ib is None:
        from ib_insync import IB, Contract
        ib, contract_cls = IB(), Contract
    job = Backfill(ib, contract_cls, out, pacer=pacer, now=now or (lambda: datetime.now(timezone.utc)))
    try:
        log(f"connecting to {a.host}:{a.port}, client id {a.client_id}, read-only")
        ib.connect(a.host, a.port, clientId=a.client_id, readonly=True, timeout=20)
        job.run(mode, what)
    except Stop as e:
        log(str(e))
        return e.status
    finally:
        ib.disconnect()
    out.flush()
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
