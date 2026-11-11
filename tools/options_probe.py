"""Read-only snapshot of COMEX gold options (OG on GC) for research/gex_feasibility.

Stdlib + ib_insync, Python 3.12. The chain discovery, the batched market data
and the market hours are in backend/options/chain.py (shared with the daily
snapshot, backend.options.snapshot); they were factored out of this file after
attempt 3, unchanged in behaviour. The file as it ran for the study is
`git show 343e03f:tools/options_probe.py` (self-contained, piped into the
scanner image from stdin). This version needs backend/options next to it, so
it runs inside the options image (deploy/options), from deploy/options/:

    docker compose run --rm -T --entrypoint python options-snapshot - \\
        < ../../tools/options_probe.py > ~/gex_probe_$(date -u +%Y%m%dT%H%MZ).json

What it does, in order (PREREG.md, "Data: one snapshot"):
1. Refuses to start unless COMEX is open on a regular trading day: not on a
   weekend, in the daily 17:00-18:00 New York break, or on a CME holiday
   (HOLIDAYS covers 2026-2027; outside them it refuses too).
2. Connects read-only with client id 61 (CLIENT_ID; 1, 2, 11, 21, 31, 43, 44,
   97, 98, 996 and 999 are taken or reserved). Host and port come from
   IB_HOST / IB_PORT, as in the scanner container.
3. Lists the GC futures and asks for their option chains (reqSecDefOptParams)
   one by one, in expiry order, until the next 3 OG expiries at least 2
   calendar days away are found (at most 12 futures; IBKR lists GC months
   consecutively, and OG options sit on only some of them). Each future
   queried is logged with the OG expiries its chain listed, and each expiry
   is recorded with that underlying. Fewer than 3: the next 2 are used and
   the fallback is recorded; fewer than 2: exit 1 (PREREG Amendment 1).
4. Lists every OG contract of those expiries (contract details: strike,
   right, underConId, expiry time), and their underlying GC futures.
5. Requests market data in batches of at most --batch lines: options with
   generic tick 101 (open interest), futures with 588 (futures open interest).
   Live data (type 1) first. A contract with no tick at all within
   --live-timeout is requested again as frozen data (type 2). Every value is
   recorded with the UTC time it was first received and last changed.
6. Daily GC bars (TRADES, 1 day, 30 D, useRTH=False) for the nearest 8 GC
   months, for the 20-day dollar volume.
7. Cancels every subscription, disconnects, and writes one JSON document to
   stdout (or --out). Logs go to stderr.

Exit status: 0 done; 2 bad arguments; 3 COMEX closed (nothing requested);
4 error 10197, a competing live session has the COMEX data (nothing written);
5 the market data line limit was hit (lower --batch); 1 anything else.
The open-interest date is inferred (IBKR doesn't send one): the last trade
date before the snapshot's.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The repo root, so backend.options imports when this file runs as a script or
# is loaded by path (analyze.py, the tests). From stdin (`python -`) the
# working directory, /app in the image, is already on sys.path.
_ROOT = Path(__file__).resolve().parents[1]
if (_ROOT / "backend" / "options").is_dir() and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backend.options.chain import (  # noqa: E402, F401 - HOLIDAYS, trade_date_of, num: re-exported for the tests
    BATCH, CHAIN_FUTURES, FROZEN_TIMEOUT_S, FUTURE_TICKS, HOLIDAYS, LIVE_TIMEOUT_S, MAX_BATCH,
    MIN_DAYS_TO_EXPIRY, N_EXPIRIES, OPTION_TICKS, TRADING_CLASS, ChainError, ChainSnapshot, closed_reason,
    iso, num, previous_trade_date, stderr_log, trade_date_of, ymd,
)

SCHEMA = "options_probe/1"
CLIENT_ID = 61
BAR_FUTURES = 8            # GC months whose daily bars are fetched

ProbeError = ChainError
log = stderr_log("options_probe")


class Probe(ChainSnapshot):
    """The chain pass of backend.options.chain, plus the daily GC bars and the
    probe's JSON document."""

    def __init__(self, ib, contracts, **kw):
        super().__init__(ib, contracts, log=log, **kw)

    # 6. daily bars
    def daily_bars(self, futs: list) -> list[dict]:
        out = []
        for d in futs[:BAR_FUTURES]:
            bars = self.ib.reqHistoricalData(d.contract, "", "30 D", "1 day", "TRADES", False, 1)
            self.check_stop()
            out.append({"con_id": d.contract.conId, "local_symbol": d.contract.localSymbol,
                        "expiry": ymd(d.contract.lastTradeDateOrContractMonth),
                        "bars": [{"date": str(b.date), "close": num(b.close), "volume": num(b.volume)}
                                 for b in bars]})
        return out

    def run(self) -> dict:
        start = self.now()
        got = self.collect()
        futs, by_con, chosen, found = got["futures"], got["by_con"], got["chosen"], got["found"]
        opt_details, und_ids, data = got["opt_details"], got["und_ids"], got["data"]
        bars = self.daily_bars(futs)
        end = self.now()
        td = trade_date_of(start)

        def values(con_id):
            kind, rec = data[con_id]
            return {"data_type": kind, "ib_market_data_type": getattr(rec, "ib_market_data_type", None),
                    "fields": rec.fields}

        return {
            "schema": SCHEMA,
            "snapshot_start_utc": iso(start), "snapshot_end_utc": iso(end),
            "trade_date": td.isoformat(),
            "oi_settlement_date_inferred": previous_trade_date(td).isoformat(),
            "oi_date_method": "IBKR sends open interest without a date; inferred as the last trade date "
                              "before the snapshot's trade date (CME publishes it after that date's settlement)",
            "client_id": CLIENT_ID,
            "params": {"trading_class": TRADING_CLASS, "n_expiries": N_EXPIRIES,
                       "min_days_to_expiry": MIN_DAYS_TO_EXPIRY, "batch": self.batch,
                       "live_timeout_s": self.live_timeout, "frozen_timeout_s": self.frozen_timeout,
                       "option_generic_ticks": OPTION_TICKS, "future_generic_ticks": FUTURE_TICKS},
            "max_concurrent_lines": self.max_active,
            "futures": [{"con_id": d.contract.conId, "local_symbol": d.contract.localSymbol,
                         "expiry": ymd(d.contract.lastTradeDateOrContractMonth),
                         "multiplier": d.contract.multiplier} for d in futs],
            "chains": got["chains"],
            "chain_futures_queried": found["queried"],
            "n_expiries_target": N_EXPIRIES,
            "n_expiries_used": len(chosen),
            "expiry_fallback": found["fallback"],
            "selected_expiries": chosen,
            "expiry_sources": got["expiry_sources"],
            "underlyings": [{"con_id": u, "local_symbol": by_con[u].contract.localSymbol,
                             "expiry": ymd(by_con[u].contract.lastTradeDateOrContractMonth), **values(u)}
                            for u in und_ids],
            "options": [{"con_id": d.contract.conId, "local_symbol": d.contract.localSymbol,
                         "expiry": ymd(d.contract.lastTradeDateOrContractMonth), "strike": d.contract.strike,
                         "right": d.contract.right, "multiplier": d.contract.multiplier,
                         "under_con_id": d.underConId, "real_expiration_date": d.realExpirationDate,
                         "last_trade_time": d.lastTradeTime, "time_zone_id": d.timeZoneId,
                         **values(d.contract.conId)} for d in opt_details],
            "gc_daily_bars": bars,
            "errors": self.errors,
        }


class _Contracts:
    """ib_insync's contract classes, imported only when the probe really runs."""

    def __init__(self):
        from ib_insync import Contract, Future, FuturesOption
        self.Contract, self.Future, self.FuturesOption = Contract, Future, FuturesOption


def main(argv=None, *, ib=None, contracts=None, now=lambda: datetime.now(timezone.utc), out=None) -> int:
    ap = argparse.ArgumentParser(prog="options_probe.py", description=__doc__.splitlines()[0])
    ap.add_argument("--host", default=os.environ.get("IB_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IB_PORT", "4004")))
    ap.add_argument("--client-id", type=int, default=CLIENT_ID)
    ap.add_argument("--batch", type=int, default=BATCH)
    ap.add_argument("--live-timeout", type=float, default=LIVE_TIMEOUT_S)
    ap.add_argument("--frozen-timeout", type=float, default=FROZEN_TIMEOUT_S)
    ap.add_argument("--out", default="-", help="JSON output path, - for stdout (default)")
    args = ap.parse_args(argv)
    if not 1 <= args.batch <= MAX_BATCH:
        ap.error(f"--batch must be 1..{MAX_BATCH} (IBKR's default market data line limit is 100)")

    reason = closed_reason(now())
    if reason:
        log(f"refusing to run: {reason}. Run while COMEX is open on a regular trading day "
            f"(e.g. 05:00 UTC, Monday to Friday).")
        return 3

    if ib is None:
        from ib_insync import IB
        ib = IB()
    probe = Probe(ib, contracts or _Contracts(), batch=args.batch, live_timeout=args.live_timeout,
                  frozen_timeout=args.frozen_timeout, now=now, chain_futures=CHAIN_FUTURES)
    try:
        log(f"connecting to {args.host}:{args.port}, client id {args.client_id}, read-only")
        ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=15)
        doc = probe.run()
    except ProbeError as e:
        log(str(e))
        return e.status
    finally:
        probe.cancel_all()
        ib.disconnect()

    text = json.dumps(doc, indent=1, sort_keys=False)
    if out is not None:
        out.write(text + "\n")
    elif args.out == "-":
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    else:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    kinds = [o["data_type"] for o in doc["options"]]
    log(f"done: {len(kinds)} options ({kinds.count('live')} live, {kinds.count('frozen')} frozen, "
        f"{kinds.count('none')} no data), {len(doc['errors'])} IBKR messages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
