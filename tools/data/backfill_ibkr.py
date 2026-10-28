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
in), durationStr "2 D" (D = trading sessions), endDateTime "<next day>-00:00:00" (UTC), one per
calendar day except Saturdays (no trading), walking backwards. At most 50
requests per 10 minutes (IBKR allows 60). Nothing at or after 2026-10-01 is
requested, and a bar at or after it stops the run (exit 3).

Output (stdout, JSON lines): one "meta" line, one "request" line per request
(its parameters, the bar count, any IBKR errors), one "bar" line per bar
(type, epoch seconds UTC, open, high, low, close, volume), and an "end" line.
A file without the "end" line is incomplete: backfill_build.py refuses it.
Logs go to stderr.

Multi-market (docs/MULTI_MARKET_DATA.md), resumable, weekly chunks checkpointed
to one file per UTC day, retried through the nightly reset:
    --instrument SYM --out-dir DIR [--first 2016-01-01] [--last 2026-09-30]
    SYM: XAGUSD (CMDTY, as XAUUSD), EURUSD USDJPY GBPUSD USDCAD USDSEK USDCHF
    (CASH IDEALPRO); MIDPOINT; client id 65; exit 6 after 3 h without a gateway.
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
SCHEMA_DAY = "backfill_ibkr_day/1"
CLIENT_ID = 63                     # 61 options probe, 62 options snapshot, 11/21 scanner, 31 feed
SEAL = datetime(2026, 10, 1, tzinfo=timezone.utc)
RANGE = (date(2026, 2, 1), date(2026, 9, 30))           # inclusive, calendar days (UTC)
LAST_DAY = date(2026, 9, 30)                            # the hard end: never request past it
OVERLAP = (date(2026, 1, 19), date(2026, 1, 30))        # the last 10 trade days of XAUUSD.txt
PROBE_TYPES = ("MIDPOINT", "BID", "ASK", "TRADES")
BAR_SIZE = "1 min"
# IBKR counts D in trading sessions, not 24 h: "1 D" ending at 00:00 UTC returns only
# the session that opened the evening before (found on the multi-market run,
# 2026-10-28). "2 D" covers the whole UTC day; the overlap between requests is
# equal duplicates, which backfill_build.py drops.
DURATION = "2 D"
MAX_PER_WINDOW, WINDOW_S = 50, 600
TIMEOUT_S = 120

# --- multi-market (docs/MULTI_MARKET_DATA.md, 2026-10-28) ------------------------
# symbol -> (IBKR symbol, secType, exchange, currency). MIDPOINT for every new one.
INSTRUMENTS = {
    "XAUUSD": ("XAUUSD", "CMDTY", "SMART", "USD"),
    "XAGUSD": ("XAGUSD", "CMDTY", "SMART", "USD"),
    "EURUSD": ("EUR", "CASH", "IDEALPRO", "USD"),
    "USDJPY": ("USD", "CASH", "IDEALPRO", "JPY"),
    "GBPUSD": ("GBP", "CASH", "IDEALPRO", "USD"),
    "USDCAD": ("USD", "CASH", "IDEALPRO", "CAD"),
    "USDSEK": ("USD", "CASH", "IDEALPRO", "SEK"),
    "USDCHF": ("USD", "CASH", "IDEALPRO", "CHF"),
}
MULTI = ("XAGUSD", "EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF")   # gold is never downloaded here
MULTI_WHAT = "MIDPOINT"
MULTI_RANGE = (date(2016, 1, 1), LAST_DAY)
MULTI_CLIENT_ID = 65               # 61-64 options probe / snapshot / gold backfill / sigma_now
WEEK_MIN_BARS = 0.5 * 5 * 1380     # a week chunk with fewer bars is fetched again day by day
RECONNECT_EVERY_S, RECONNECT_FOR_S = 120, 3 * 3600   # the nightly reset: retry for up to 3 h
WAIT_10197_S, WAIT_PACING_S = 300, 660


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


def refuse_past_seal(d: date) -> None:
    """The hard end: a request for a day after 2026-09-30, or whose endDateTime
    lies after 2026-10-01 00:00 UTC, is refused before IBKR is asked (exit 3)."""
    end = datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)
    if d > LAST_DAY or end > SEAL:
        raise Stop(f"request for {d} refused: data from {SEAL:%Y-%m-%d} is sealed (last day {LAST_DAY})", 3)


def plan(mode: str, what: str | None) -> list[tuple[str, date]]:
    reqs = ([(t, d) for t in PROBE_TYPES for d in days(*OVERLAP)] if mode == "probe"
            else [(what, d) for d in days(*RANGE)])
    for _, d in reqs:
        refuse_past_seal(d)
    return reqs


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


def refuse_end(end: datetime) -> None:
    """The hard end for a chunk: its endDateTime must not lie after 2026-10-01 00:00 UTC."""
    if end > SEAL:
        raise Stop(f"request ending {end:%Y-%m-%d %H:%M} UTC refused: data from {SEAL:%Y-%m-%d} is sealed", 3)


def week_chunks(first: date, last: date) -> list[datetime]:
    """Request ends, oldest first: each Saturday 00:00 UTC (a whole Sun-Fri trading
    week before it), the last one capped at the seal (2026-10-01 00:00 UTC)."""
    if last > LAST_DAY:
        raise Stop(f"last day {last} refused: data from {SEAL:%Y-%m-%d} is sealed (last day {LAST_DAY})", 3)
    d = first + timedelta(days=(5 - first.weekday()) % 7 or 7)          # the Saturday after `first`
    ends = []
    while True:
        end = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        cap = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
        ends.append(min(end, cap))
        if end >= cap:
            break
        d += timedelta(days=7)
    for e in ends:
        refuse_end(e)
    return ends


def chunk_ranges(first: date, last: date) -> list[tuple[datetime, datetime]]:
    """[start, end) of each chunk, back to back: a chunk starts where the previous
    one ended, so no day is written by two chunks (the capped last chunk used to
    start 7 days before its end and overwrite part of the week before)."""
    ends = week_chunks(first, last)
    return [(ends[i - 1] if i else ends[0] - timedelta(days=7), e) for i, e in enumerate(ends)]


DAY_DURATION = "2 D"   # IBKR counts D in trading sessions, not 24 h: "2 D" ending at the next
                       # 00:00 UTC covers the whole UTC day; the rows are then cut to that day


def ibkr_end(end: datetime) -> str:
    return end.strftime("%Y%m%d-%H:%M:%S")


class Backfill:
    def __init__(self, ib, contract_cls, out, *, pacer=None, now=lambda: datetime.now(timezone.utc)):
        self.ib, self.Contract, self.out = ib, contract_cls, out
        self.pacer, self.now = pacer or Pacer(), now
        self.req_errors: list[dict] = []
        self.stop: Stop | None = None
        self.lost = False
        self.sleep = time.sleep
        self.conn: dict = {}
        ib.errorEvent += self.on_error

    def emit(self, rec: dict) -> None:
        self.out.write(json.dumps(rec, separators=(",", ":")) + "\n")

    def on_error(self, reqId, errorCode, errorString, contract=None):
        self.req_errors.append({"code": errorCode, "message": errorString})
        if errorCode in (1100, 1300, 2110):            # connectivity lost (the nightly reset)
            self.lost = True
        if errorCode == 10197:
            self.stop = Stop("IBKR error 10197: a competing live session. Log the live account out "
                             "everywhere (TWS, mobile, web) and run again.", 4)
        elif errorCode == 162 and "pacing" in str(errorString).lower():
            self.stop = Stop(f"IBKR pacing violation: {errorString}. Wait 10 minutes and run again.", 5)

    def contract(self, sym: str = "XAUUSD"):
        symbol, sec, exch, cur = INSTRUMENTS[sym]
        c = self.Contract(symbol=symbol, secType=sec, exchange=exch, currency=cur)
        got = self.ib.qualifyContracts(c)
        if not got:
            raise Stop(f"IBKR could not qualify {symbol} {sec} {exch} {cur}", 1)
        return got[0]

    # --- resumable multi-market download -------------------------------------------

    def reconnect(self) -> None:
        """Wait out a gateway restart or the nightly reset: retry every 2 min for 3 h."""
        waited = 0
        while True:
            try:
                self.ib.disconnect()
            except Exception:  # noqa: BLE001  (best effort before reconnecting)
                pass
            try:
                self.ib.connect(self.conn["host"], self.conn["port"], clientId=self.conn["client_id"],
                                readonly=True, timeout=20)
                if not hasattr(self.ib, "isConnected") or self.ib.isConnected():
                    self.lost = False
                    log(f"reconnected after {waited} s")
                    return
            except Exception as e:  # noqa: BLE001  (any connect failure is retried)
                log(f"reconnect failed ({type(e).__name__}); retrying in {RECONNECT_EVERY_S} s")
            if waited >= RECONNECT_FOR_S:
                raise Stop(f"no connection to IB Gateway for {waited} s; rerun the same command to resume", 6)
            self.sleep(RECONNECT_EVERY_S)
            waited += RECONNECT_EVERY_S

    def fetch(self, c, end: datetime, duration: str) -> list:
        """One request, retried through a lost connection, 10197 and pacing. The
        seal is checked before every attempt."""
        while True:
            refuse_end(end)
            self.pacer.wait()
            self.req_errors, self.stop = [], None
            try:
                bars = self.ib.reqHistoricalData(c, endDateTime=ibkr_end(end), durationStr=duration,
                                                 barSizeSetting=BAR_SIZE, whatToShow=MULTI_WHAT, useRTH=False,
                                                 formatDate=2, keepUpToDate=False, timeout=TIMEOUT_S)
            except (ConnectionError, OSError, TimeoutError) as e:
                log(f"request failed ({type(e).__name__}); reconnecting")
                self.reconnect()
                continue
            if self.stop is not None:
                if self.stop.status == 4:
                    log(f"10197 (a competing live session); waiting {WAIT_10197_S} s")
                    self.sleep(WAIT_10197_S)
                    continue
                if self.stop.status == 5:
                    log(f"pacing violation; waiting {WAIT_PACING_S} s")
                    self.sleep(WAIT_PACING_S)
                    continue
                raise self.stop
            if self.lost or (hasattr(self.ib, "isConnected") and not self.ib.isConnected()):
                log("connection lost during the request; reconnecting")
                self.reconnect()
                continue
            return list(bars or [])

    def rows(self, bars) -> list[dict]:
        out = []
        for b in bars:
            ts = epoch(b.date)
            if ts >= SEAL.timestamp():
                raise Stop(f"bar at {datetime.fromtimestamp(ts, timezone.utc)} is in the sealed period", 3)
            out.append({"kind": "bar", "t": ts, "o": num(b.open), "h": num(b.high), "l": num(b.low),
                        "c": num(b.close), "v": num(b.volume)})
        return out

    def refetch_days(self, sym: str, out_dir: str, days: list[date]) -> dict:
        """Ask again for single UTC days, one '1 D' request each, and replace a day's
        file only if the new answer has more bars (a week answer can come back
        truncated). Counts only in the log; _refetch.json records old -> new."""
        import pathlib
        base = pathlib.Path(out_dir) / sym
        c = self.contract(sym)
        out = {}
        for d in sorted(set(days)):
            refuse_past_seal(d)
            f = base / f"{d}.jsonl"
            old = 0
            if f.exists():
                tail = json.loads(f.read_text().splitlines()[-1])
                old = int(tail.get("bars", 0)) if tail.get("kind") == "day_end" else 0
            start = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            rows = [r for r in self.rows(self.fetch(c, start + timedelta(days=1), DAY_DURATION))
                    if start.timestamp() <= r["t"] < (start + timedelta(days=1)).timestamp()]
            if len(rows) > old:
                tmp = f.with_suffix(".tmp")
                with open(tmp, "w") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, separators=(",", ":")) + "\n")
                    fh.write(json.dumps({"kind": "day_end", "day": str(d), "bars": len(rows), "refetched": True}) + "\n")
                os.replace(tmp, f)
            out[str(d)] = {"old": old, "new": len(rows), "replaced": len(rows) > old}
            log(f"{sym} {d}: {old} -> {len(rows)} bars{' (replaced)' if len(rows) > old else ''}")
        prev = json.loads((base / "_refetch.json").read_text()) if (base / "_refetch.json").exists() else {}
        (base / "_refetch.json").write_text(json.dumps({**prev, **out}, indent=1) + "\n")
        return out

    def run_instrument(self, sym: str, out_dir: str, first: date, last: date) -> dict:
        """Week chunks, oldest first, each checkpointed: its bars go to one file per
        UTC day (written whole, then renamed) and a marker in _done/. A rerun skips
        every chunk with a marker. A short or failed week is fetched day by day."""
        import pathlib
        base = pathlib.Path(out_dir) / sym
        (base / "_done").mkdir(parents=True, exist_ok=True)
        c = self.contract(sym)
        ranges = chunk_ranges(first, last)
        ends = [e for _, e in ranges]
        meta = {"kind": "meta", "schema": SCHEMA_DAY, "instrument": sym, "what_to_show": MULTI_WHAT,
                "started_utc": self.now().isoformat(), "client_id": self.conn.get("client_id"),
                "contract": {"con_id": c.conId, "symbol": c.symbol, "sec_type": c.secType, "exchange": c.exchange,
                             "currency": c.currency, "local_symbol": getattr(c, "localSymbol", "")},
                "bar_size": BAR_SIZE, "use_rth": False, "format_date": 2, "range": [str(first), str(last)],
                "n_chunks": len(ends)}
        (base / "_meta.json").write_text(json.dumps(meta) + "\n")
        todo = [(st, e) for st, e in ranges if not (base / "_done" / f"{e:%Y%m%d}.json").exists()]
        log(f"{sym}: {len(ends)} week chunks, {len(ends) - len(todo)} already done, contract conId {c.conId}")
        t0, n_bars = time.monotonic(), 0
        for k, (start, end) in enumerate(todo, 1):
            errs: list = []
            bars = self.fetch(c, end, "1 W")
            errs += self.req_errors
            mode = "week"
            if len(bars) < WEEK_MIN_BARS:
                mode, bars = "day", []
                d = (end - timedelta(days=1)).date()
                while datetime(d.year, d.month, d.day, tzinfo=timezone.utc) >= start:
                    if d.weekday() != 5 and first <= d <= last:
                        bars += self.fetch(c, datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1), DAY_DURATION)
                        errs += self.req_errors
                    d -= timedelta(days=1)
            rows = self.rows(bars)
            by_day: dict = {}
            for r in rows:
                day = datetime.fromtimestamp(r["t"], timezone.utc).date()
                if first <= day <= last and start.timestamp() <= r["t"] < end.timestamp():
                    by_day.setdefault(day, []).append(r)
            for day, rs in sorted(by_day.items()):
                f = base / f"{day}.jsonl"
                tmp = f.with_suffix(".tmp")
                with open(tmp, "w") as fh:
                    for r in rs:
                        fh.write(json.dumps(r, separators=(",", ":")) + "\n")
                    fh.write(json.dumps({"kind": "day_end", "day": str(day), "bars": len(rs),
                                         "chunk_end": end.isoformat()}) + "\n")
                os.replace(tmp, f)
            n_bars += sum(len(v) for v in by_day.values())
            marker = {"chunk_end": end.isoformat(), "mode": mode, "bars": sum(len(v) for v in by_day.values()),
                      "days": len(by_day), "errors": sorted({e["code"] for e in errs})}
            (base / "_done" / f"{end:%Y%m%d}.json").write_text(json.dumps(marker) + "\n")
            if k % 10 == 0 or k == len(todo):
                rate = (time.monotonic() - t0) / k
                log(f"{sym}: {k}/{len(todo)} chunks this run, {n_bars} bars, "
                    f"ETA {rate * (len(todo) - k) / 60:.0f} min")
        done = {"kind": "complete", "instrument": sym, "ended_utc": self.now().isoformat(), "n_chunks": len(ends)}
        (base / "_complete.json").write_text(json.dumps(done) + "\n")
        log(f"{sym}: complete")
        return done

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
            refuse_past_seal(d)                          # again, per request, before IBKR is asked
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
    g.add_argument("--instrument", metavar="SYM", choices=MULTI,
                   help="resumable multi-market download (docs/MULTI_MARKET_DATA.md); needs --out-dir")
    ap.add_argument("--out-dir", help="--instrument: day files go to OUT_DIR/SYM/")
    ap.add_argument("--first", type=date.fromisoformat, default=MULTI_RANGE[0])
    ap.add_argument("--last", type=date.fromisoformat, default=MULTI_RANGE[1])
    ap.add_argument("--refetch-days", help="--instrument: comma-separated UTC days to ask for again, one by one")
    ap.add_argument("--host", default=os.environ.get("IB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IB_PORT", "4004")))
    ap.add_argument("--client-id", type=int, default=None)
    a = ap.parse_args(argv)
    a.client_id = a.client_id if a.client_id is not None else (MULTI_CLIENT_ID if a.instrument else CLIENT_ID)
    if a.instrument:
        return main_instrument(a, ib=ib, contract_cls=contract_cls, pacer=pacer, now=now)
    mode, what = ("probe", None) if a.probe_overlap else ("download", a.download)
    out = out or sys.stdout
    try:
        plan(mode, what)                                 # refuses a sealed request before connecting
    except Stop as e:
        log(str(e))
        return e.status
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


def main_instrument(a, *, ib=None, contract_cls=None, pacer=None, now=None, sleep=None) -> int:
    if not a.out_dir:
        log("--instrument needs --out-dir")
        return 2
    try:
        week_chunks(a.first, a.last)                     # refuses a sealed range before connecting
        for x in (getattr(a, "refetch_days", None) or "").split(","):
            if x:
                refuse_past_seal(date.fromisoformat(x))
    except Stop as e:
        log(str(e))
        return e.status
    if ib is None:
        from ib_insync import IB, Contract
        ib, contract_cls = IB(), Contract
    job = Backfill(ib, contract_cls, None, pacer=pacer, now=now or (lambda: datetime.now(timezone.utc)))
    if sleep is not None:
        job.sleep = sleep
    job.conn = {"host": a.host, "port": a.port, "client_id": a.client_id}
    try:
        log(f"connecting to {a.host}:{a.port}, client id {a.client_id}, read-only")
        try:
            ib.connect(a.host, a.port, clientId=a.client_id, readonly=True, timeout=20)
        except Exception as e:  # noqa: BLE001  (the first connect is retried like a reset)
            log(f"connect failed ({type(e).__name__}); retrying")
            job.reconnect()
        if getattr(a, "refetch_days", None):
            job.refetch_days(a.instrument, a.out_dir, [date.fromisoformat(x) for x in a.refetch_days.split(",") if x])
        else:
            job.run_instrument(a.instrument, a.out_dir, a.first, a.last)
    except Stop as e:
        log(str(e))
        return e.status
    finally:
        ib.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
