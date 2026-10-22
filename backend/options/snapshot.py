"""The daily OG options snapshot: python -m backend.options.

Run once per weekday at 05:00 UTC by deploy/options/run_snapshot.sh (host
cron); a run takes about 11 minutes. Each invocation writes one option_runs
row (backend.options.store), whatever happens, and never retries:

    exit 0  ok: the next 3 OG expiries' full chain and their GC underlyings
            stored; or the trade date already had an ok snapshot (skipped,
            nothing requested)
    exit 3  skipped: COMEX closed (weekend, daily break) or a CME holiday;
            nothing requested
    exit 4  skipped: IBKR error 10197, a competing live session (the live
            account is logged in somewhere); nothing stored but the run row
    exit 5  failed: the market data line limit was hit (lower --batch)
    exit 1  failed: anything else (no gateway, too few expiries, the holiday
            calendar ran out, ...)

`--list-runs N` prints the last N runs (read-only, no IBKR).

The IBKR side is backend.options.chain, the code the GEX feasibility probe
(tools/options_probe.py) ran with: chain discovery month by month, batches of
50 lines, live then frozen per contract, everything cancelled at the end.
Client id 62 (61 is the probe's).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

from backend.options import store
from backend.options.chain import (
    BATCH, FROZEN_TIMEOUT_S, FUTURE_TICKS, GREEK_FIELDS, HOLIDAY_YEARS, LIVE_TIMEOUT_S, MAX_BATCH,
    MIN_DAYS_TO_EXPIRY, N_EXPIRIES, OPTION_TICKS, TRADING_CLASS, ChainError, ChainSnapshot, closed_reason,
    iso, previous_trade_date, stderr_log, trade_date_of, ymd,
)

CLIENT_ID = 62
GREEKS = GREEK_FIELDS + (("model_vega", "vega"),)
EXIT_OK, EXIT_FAILED, EXIT_CLOSED, EXIT_COMPETING, EXIT_LINES = 0, 1, 3, 4, 5

log = stderr_log("options_snapshot")


class _Contracts:
    """ib_insync's contract classes, imported only when a snapshot really runs."""

    def __init__(self):
        from ib_insync import Contract, Future, FuturesOption
        self.Contract, self.Future, self.FuturesOption = Contract, Future, FuturesOption


def rows(got: dict) -> tuple[list[dict], list[dict]]:
    """collect()'s result -> the option and underlying rows for store.record_run."""
    by_con, data = got["by_con"], got["data"]
    chain_und = {}
    for src in got["expiry_sources"]:
        ids = src["chain_underlying_con_ids"]
        chain_und[src["expiry"]] = ids[0] if len(ids) == 1 else None
    options = []
    for d in got["opt_details"]:
        kind, rec = data[d.contract.conId]
        e = ymd(d.contract.lastTradeDateOrContractMonth)
        options.append({"con_id": d.contract.conId, "local_symbol": d.contract.localSymbol, "expiry": e,
                        "strike": d.contract.strike, "right": d.contract.right, "multiplier": d.contract.multiplier,
                        "under_con_id": d.underConId, "chain_under_con_id": chain_und.get(e),
                        "real_expiration_date": d.realExpirationDate, "last_trade_time": d.lastTradeTime,
                        "time_zone_id": d.timeZoneId, "data_type": kind,
                        "ib_market_data_type": rec.ib_market_data_type, "fields": rec.fields})
    underlyings = []
    for u in got["und_ids"]:
        kind, rec = data[u]
        c = by_con[u].contract
        underlyings.append({"con_id": u, "local_symbol": c.localSymbol, "expiry": ymd(c.lastTradeDateOrContractMonth),
                            "data_type": kind, "ib_market_data_type": rec.ib_market_data_type, "fields": rec.fields})
    return options, underlyings


def list_runs(path: str, n: int, out=None) -> int:
    out = out or sys.stdout
    if not os.path.isfile(path):
        print(f"{path}: no store yet", file=sys.stderr)
        return 1
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows_ = con.execute("SELECT run_id, trade_date, status, exit_code, started_utc, ended_utc, n_options, n_live, "
                            "n_frozen, n_none, selected_expiries, reason FROM option_runs "
                            "ORDER BY run_id DESC LIMIT ?", (n,)).fetchall()
    finally:
        con.close()
    for r in reversed(rows_):
        run_id, td, status, code, t0, t1, n_opt, n_live, n_frozen, n_none, exps, reason = r
        counts = f" {n_opt} options ({n_live} live, {n_frozen} frozen, {n_none} none) {exps}" if n_opt else ""
        print(f"run {run_id} {td} {status} exit {code} {t0} .. {t1}{counts}{'  ' + reason if reason else ''}",
              file=out)
    return 0


def main(argv=None, *, ib=None, contracts=None, now=lambda: datetime.now(timezone.utc)) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.options", description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=os.environ.get("OPTIONS_DB", store.DEFAULT_DB))
    ap.add_argument("--host", default=os.environ.get("IB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IB_PORT", "4004")))
    ap.add_argument("--client-id", type=int, default=int(os.environ.get("OPTIONS_IB_CLIENT_ID", CLIENT_ID)))
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--live-timeout", type=float, default=LIVE_TIMEOUT_S)
    ap.add_argument("--frozen-timeout", type=float, default=FROZEN_TIMEOUT_S)
    ap.add_argument("--list-runs", type=int, metavar="N",
                    help="print the last N option_runs rows and exit (read-only, no IBKR)")
    args = ap.parse_args(argv)
    if args.list_runs is not None:
        return list_runs(args.db, args.list_runs)
    if not 1 <= args.batch <= MAX_BATCH:
        ap.error(f"--batch must be 1..{MAX_BATCH} (IBKR's default market data line limit is 100)")

    start = now()
    td = trade_date_of(start)
    con = store.connect(args.db)
    base = {"trade_date": td.isoformat(), "started_utc": iso(start), "client_id": args.client_id}

    def finish(status: str, code: int, reason: str | None, **extra) -> int:
        store.record_run(con, {**base, "status": status, "exit_code": code, "reason": reason,
                               "ended_utc": iso(now()), **extra})
        log(f"{status} (exit {code}){': ' + reason if reason else ''}")
        return code

    try:
        reason = closed_reason(start)
        if reason:
            if td.year not in HOLIDAY_YEARS:               # not a closed market: the calendar ran out
                return finish("failed", EXIT_FAILED, reason)
            return finish("skipped", EXIT_CLOSED, reason)
        done = store.ok_run_for(con, td.isoformat())
        if done is not None:
            return finish("skipped", EXIT_OK, f"trade date {td} already has an ok snapshot (run {done})")

        if ib is None:
            from ib_insync import IB
            ib = IB()
        snap = ChainSnapshot(ib, contracts or _Contracts(), batch=args.batch, live_timeout=args.live_timeout,
                             frozen_timeout=args.frozen_timeout, now=now, log=log, greeks=GREEKS)
        stopped = None                                     # (status, exit code, reason) when collect() failed
        try:
            log(f"connecting to {args.host}:{args.port}, client id {args.client_id}, read-only")
            ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=15)
            got = snap.collect()
        except ChainError as e:
            stopped = ("skipped" if e.status == EXIT_COMPETING else "failed", e.status, str(e))
        except Exception as e:                             # noqa: BLE001 - recorded as a failed day, then exit 1
            stopped = ("failed", EXIT_FAILED, f"{type(e).__name__}: {e}")
        finally:
            snap.cancel_all()
            ib.disconnect()
        if stopped:
            return finish(*stopped, ib_messages=snap.errors, max_concurrent_lines=snap.max_active)

        options, underlyings = rows(got)
        found = got["found"]
        run = {**base, "status": "ok", "exit_code": EXIT_OK, "ended_utc": iso(now()),
               "oi_settlement_date": previous_trade_date(td).isoformat(),
               "selected_expiries": got["chosen"], "expiry_fallback": found["fallback"],
               "chain_futures_queried": found["queried"], "expiry_sources": got["expiry_sources"],
               "params": {"trading_class": TRADING_CLASS, "n_expiries": N_EXPIRIES,
                          "min_days_to_expiry": MIN_DAYS_TO_EXPIRY, "batch": args.batch,
                          "live_timeout_s": args.live_timeout, "frozen_timeout_s": args.frozen_timeout,
                          "option_generic_ticks": OPTION_TICKS, "future_generic_ticks": FUTURE_TICKS},
               "max_concurrent_lines": snap.max_active, "ib_messages": snap.errors}
        run_id = store.record_run(con, run, options, underlyings)
        kinds = [o["data_type"] for o in options]
        log(f"ok: run {run_id}, trade date {td}, expiries {', '.join(got['chosen'])}: {len(options)} options "
            f"({kinds.count('live')} live, {kinds.count('frozen')} frozen, {kinds.count('none')} no data), "
            f"{len(underlyings)} underlyings, {len(snap.errors)} IBKR messages")
        return EXIT_OK
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
