#!/usr/bin/env python3
"""One-off check, PAPER GATEWAY ONLY: is an instrument usable on this account?
Defaults to BTC (secType CRYPTO, exchange PAXOS, currency USD).

    python tools/ibkr_instrument_check.py --host 127.0.0.1 --port 4002 [--client-id 43]
        [--symbol BTC --sec-type CRYPTO --exchange PAXOS --currency USD] [--ticks-s 30]

Self-contained (ib_insync + stdlib), so it also runs inside the scanner image,
fed on stdin, against the stack's gateway (see docs/DRYRUN.md):

    docker compose run --rm -T --no-deps --entrypoint python scanner - \\
        --host ibgateway --port 4004 < ibkr_instrument_check.py

Guards, all before any data request:
  - the port must be a paper port: 4002, 4004 (the ib-gateway image's paper
    relay) or 7497; this is checked before connecting;
  - the connection is readonly, with its own clientId (default 43; the stack
    uses 11, 21, 31, 996, 999, and the "10 D" check 41);
  - every managed account must be a paper account (starts with "DU").
  Otherwise it exits 3 without requesting anything.

What it asks, in order, and reports as JSON:
  1. reqContractDetails: conId, names, minTick, time zone, the first trading
     hours, valid exchanges; or the error.
  2. reqHistoricalData for each whatToShow in WHAT_TO_SHOW, over the last 30 min
     of 1-min bars, useRTH False: the bar count and first/last bar, or the
     error code and message.
  3. reqMktData for --ticks-s seconds: the number of ticker updates with a
     price, the market data type (1 live, 3 delayed ...), a bid/ask/last
     sample. Then cancelMktData.
  4. Every gateway error message seen, with the codes that mean a missing
     market-data subscription or trading permission flagged.
verdict: "available" (contract, at least one whatToShow with bars, live ticks),
"no contract", "no market data" (contract but no bars and no ticks), or
"partial" (anything else: read the details).
Exit codes: 0 report printed (whatever the verdict), 3 refused (not paper),
4 no connection to the gateway.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any, Callable

PAPER_PORTS = (4002, 4004, 7497)
WHAT_TO_SHOW = ("TRADES", "MIDPOINT", "BID", "ASK", "BID_ASK", "AGGTRADES")
# gateway error codes that mean "no market data subscription / permission"
ENTITLEMENT_CODES = {162: "historical data request failed (often: no permission)",
                     200: "no security definition found",
                     321: "error validating request",
                     354: "requested market data is not subscribed",
                     10089: "market data requires additional subscription",
                     10090: "part of the requested market data is not subscribed",
                     10167: "requested market data not subscribed; delayed data shown",
                     10168: "market data not subscribed, delayed data not enabled",
                     10197: "no market data during competing live session"}
INFO_CODES = {2104, 2106, 2107, 2108, 2158, 2119}          # "market data farm connection is OK" etc.


class NotPaper(Exception):
    pass


def check_paper(port: int, accounts: Any) -> None:
    if port not in PAPER_PORTS:
        raise NotPaper(f"port {port} is not a paper port {PAPER_PORTS}")
    accounts = list(accounts)
    if not accounts or not all(str(a).startswith("DU") for a in accounts):
        raise NotPaper(f"managed accounts {accounts} are not all paper (DU...)")


def verdict(details: dict[str, Any], history: dict[str, dict[str, Any]], ticks: dict[str, Any]) -> str:
    if not details.get("found"):
        return "no contract"
    with_bars = [w for w, h in history.items() if h.get("bars", 0) > 0]
    live = ticks.get("updates_with_price", 0) > 0 and ticks.get("market_data_type") == 1
    if with_bars and live:
        return "available"
    if not with_bars and not ticks.get("updates_with_price", 0):
        return "no market data"
    return "partial"


def _iso(t: Any) -> str:
    return t.isoformat() if hasattr(t, "isoformat") else str(t)


def run(host: str, port: int, client_id: int, symbol: str, sec_type: str, exchange: str,
        currency: str, ticks_s: float, ib_factory: Callable[[], Any] | None = None,
        contract_factory: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    if port not in PAPER_PORTS:                          # before even connecting
        raise NotPaper(f"port {port} is not a paper port {PAPER_PORTS}")
    if ib_factory is None or contract_factory is None:
        from ib_insync import IB, Contract
        ib_factory, contract_factory = ib_factory or IB, contract_factory or Contract
    ib = ib_factory()
    errors: list[dict[str, Any]] = []

    def on_error(req_id: int, code: int, message: str, contract: Any = None) -> None:
        if code not in INFO_CODES:
            errors.append({"req_id": req_id, "code": code, "message": message,
                           "entitlement": ENTITLEMENT_CODES.get(code)})

    ib.errorEvent += on_error
    ib.connect(host, port, clientId=client_id, timeout=15, readonly=True)
    nap = sleep or ib.sleep
    try:
        check_paper(port, ib.managedAccounts())
        c = contract_factory(symbol=symbol, secType=sec_type, exchange=exchange, currency=currency)
        details: dict[str, Any] = {"found": False}
        cds = ib.reqContractDetails(c)
        cd = cds[0] if cds else None
        found = cd.contract if cd is not None else None
        if cd is not None and found is not None:
            details = {"found": True, "matches": len(cds), "conId": found.conId,
                       "localSymbol": found.localSymbol, "longName": cd.longName,
                       "marketName": cd.marketName, "minTick": cd.minTick, "timeZoneId": cd.timeZoneId,
                       "tradingHours": (cd.tradingHours or "").split(";")[:3],
                       "validExchanges": cd.validExchanges, "orderTypes_n": len((cd.orderTypes or "").split(","))}
            c = found
        history: dict[str, dict[str, Any]] = {}
        ticks: dict[str, Any] = {}
        if details["found"]:
            for w in WHAT_TO_SHOW:
                before = len(errors)
                bars = ib.reqHistoricalData(c, endDateTime="", durationStr="1800 S", barSizeSetting="1 min",
                                            whatToShow=w, useRTH=False, formatDate=2, timeout=60)
                h: dict[str, Any] = {"bars": len(bars or [])}
                if bars:
                    h.update(first=_iso(bars[0].date), last=_iso(bars[-1].date), last_close=bars[-1].close)
                if len(errors) > before:
                    h["errors"] = [f"{e['code']}: {e['message']}" for e in errors[before:]]
                history[w] = h
            ticker = ib.reqMktData(c, "", False, False)
            updates = {"n": 0}

            def on_update(t: Any) -> None:
                if any(x is not None and x == x and x > 0 for x in (t.bid, t.ask, t.last)):
                    updates["n"] += 1

            ticker.updateEvent += on_update
            t0 = time.monotonic()
            while time.monotonic() - t0 < ticks_s:
                nap(1)
            ib.cancelMktData(c)
            ticks = {"seconds": ticks_s, "updates_with_price": updates["n"],
                     "market_data_type": ticker.marketDataType,
                     "sample": {"bid": ticker.bid, "ask": ticker.ask, "last": ticker.last,
                                "time": _iso(ticker.time) if ticker.time else None}}
    finally:
        ib.disconnect()
    return {"instrument": {"symbol": symbol, "secType": sec_type, "exchange": exchange, "currency": currency},
            "client_id": client_id, "contract": details, "historical": history, "ticks": ticks,
            "errors": errors, "verdict": verdict(details, history, ticks)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--client-id", type=int, default=43)
    ap.add_argument("--symbol", default="BTC")
    ap.add_argument("--sec-type", default="CRYPTO")
    ap.add_argument("--exchange", default="PAXOS")
    ap.add_argument("--currency", default="USD")
    ap.add_argument("--ticks-s", type=float, default=30.0, help="seconds to watch live ticks (default 30)")
    a = ap.parse_args(argv)
    try:
        out = run(a.host, a.port, a.client_id, a.symbol, a.sec_type, a.exchange, a.currency, a.ticks_s)
    except NotPaper as e:
        print(f"refused: {e}. This check runs against a paper gateway only.", file=sys.stderr)
        return 3
    except (ConnectionError, OSError, TimeoutError) as e:
        print(f"no connection to {a.host}:{a.port} ({type(e).__name__}: {e})", file=sys.stderr)
        return 4
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
