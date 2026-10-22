"""Read-only snapshot of COMEX gold options (OG on GC) for research/gex_feasibility.

One self-contained file (stdlib + ib_insync, Python 3.12), so it runs inside
the existing scanner image from stdin. On the EC2 host, from deploy/scanner/:

    docker compose run --rm -T --no-deps --entrypoint python scanner - \\
        < /tmp/options_probe.py > ~/gex_probe_$(date -u +%Y%m%dT%H%MZ).json

What it does, in order (PREREG.md, "Data: one snapshot"):
1. Refuses to start unless COMEX is open on a regular trading day: not on a
   weekend, in the daily 17:00-18:00 New York break, or on a CME holiday
   (HOLIDAYS covers 2026-2027; outside them it refuses too).
2. Connects read-only with client id 61 (CLIENT_ID; 1, 2, 11, 21, 31, 43, 44,
   97, 98, 996 and 999 are taken or reserved). Host and port come from
   IB_HOST / IB_PORT, as in the scanner container.
3. Lists the GC futures, finds the OG expiries with reqSecDefOptParams, and
   picks the next 3 that are at least 2 calendar days away.
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
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SCHEMA = "options_probe/1"
CLIENT_ID = 61
NY = ZoneInfo("America/New_York")
SYMBOL, EXCHANGE, CURRENCY, TRADING_CLASS = "GC", "COMEX", "USD", "OG"
N_EXPIRIES = 3
MIN_DAYS_TO_EXPIRY = 2
CHAIN_FUTURES = 4          # GC months whose option chains are asked for expirations
BAR_FUTURES = 8            # GC months whose daily bars are fetched
BATCH = 50                 # market data lines at a time (IBKR's default limit is 100)
LIVE_TIMEOUT_S = 15.0
FROZEN_TIMEOUT_S = 15.0
POLL_S = 0.25
OPTION_TICKS = "101"       # option open interest
FUTURE_TICKS = "588"       # futures open interest

# CME holidays on which this probe refuses to run: full closures and the
# abbreviated, no-settlement sessions (keyed by CME trade date).
HOLIDAYS = {
    date(2026, 1, 1): "New Year's Day", date(2026, 1, 19): "Martin Luther King Jr. Day",
    date(2026, 2, 16): "Presidents' Day", date(2026, 4, 3): "Good Friday",
    date(2026, 5, 25): "Memorial Day", date(2026, 6, 19): "Juneteenth",
    date(2026, 7, 3): "Independence Day (observed)", date(2026, 9, 7): "Labor Day",
    date(2026, 11, 26): "Thanksgiving", date(2026, 12, 25): "Christmas",
    date(2027, 1, 1): "New Year's Day", date(2027, 1, 18): "Martin Luther King Jr. Day",
    date(2027, 2, 15): "Presidents' Day", date(2027, 3, 26): "Good Friday",
    date(2027, 5, 31): "Memorial Day", date(2027, 6, 18): "Juneteenth (observed)",
    date(2027, 7, 5): "Independence Day (observed)", date(2027, 9, 6): "Labor Day",
    date(2027, 11, 25): "Thanksgiving", date(2027, 12, 24): "Christmas (observed)",
}
HOLIDAY_YEARS = {d.year for d in HOLIDAYS}

# option ticker fields recorded, by name in the JSON -> how to read them
PRICE_FIELDS = ("bid", "ask", "last", "close")
GREEK_FIELDS = (("model_iv", "impliedVol"), ("model_gamma", "gamma"), ("model_delta", "delta"),
                ("model_und_price", "undPrice"), ("model_opt_price", "optPrice"))


class ProbeError(Exception):
    """A clean stop with a message and an exit status."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%H:%M:%S} options_probe: {msg}", file=sys.stderr, flush=True)


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --- market hours -----------------------------------------------------------------

def trade_date_of(now_utc: datetime) -> date:
    """The CME trade date a moment belongs to: a session opens at 18:00 New York
    for the next calendar day's trade date."""
    et = now_utc.astimezone(NY)
    return et.date() + timedelta(days=1) if et.hour >= 18 else et.date()


def closed_reason(now_utc: datetime) -> str | None:
    """None when COMEX is open on a regular trading day, else why not."""
    et = now_utc.astimezone(NY)
    wd, hm = et.weekday(), et.hour + et.minute / 60
    if wd == 5 or (wd == 6 and hm < 18) or (wd == 4 and hm >= 17):
        return f"COMEX is closed for the weekend ({et:%a %Y-%m-%d %H:%M} New York)"
    if 17 <= hm < 18:
        return f"COMEX is in its daily 17:00-18:00 New York break ({et:%H:%M} New York)"
    td = trade_date_of(now_utc)
    if td.year not in HOLIDAY_YEARS:
        return f"the holiday calendar covers {sorted(HOLIDAY_YEARS)} only, not trade date {td}; update HOLIDAYS"
    if td in HOLIDAYS:
        return f"trade date {td} is a CME holiday ({HOLIDAYS[td]})"
    return None


def previous_trade_date(td: date) -> date:
    d = td - timedelta(days=1)
    while d.weekday() >= 5 or d in HOLIDAYS:
        d -= timedelta(days=1)
    return d


# --- helpers ------------------------------------------------------------------------

def num(v):
    """A float for JSON, or None for IBKR's unset values (NaN, None, -1 sentinels left as is)."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def ymd(s: str) -> str:
    return (s or "")[:8]


def chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]


class Recorder:
    """The values of one ticker as they arrive: value, first and last change time."""

    def __init__(self, kind: str, right: str | None = None):
        self.kind, self.right = kind, right
        self.fields: dict[str, dict] = {}
        self.ib_market_data_type = None            # what IBKR reported at the end (1 live, 2 frozen, ...)

    def _read(self, ticker) -> dict:
        out = {f: num(getattr(ticker, f, None)) for f in PRICE_FIELDS}
        if self.kind == "option":
            oi = ticker.callOpenInterest if self.right == "C" else ticker.putOpenInterest
            out["open_interest"] = num(oi)
            g = getattr(ticker, "modelGreeks", None)
            for name, attr in GREEK_FIELDS:
                out[name] = num(getattr(g, attr, None)) if g is not None else None
        else:
            out["open_interest"] = num(getattr(ticker, "futuresOpenInterest", None))
        return out

    def update(self, ticker, now: datetime) -> None:
        for name, v in self._read(ticker).items():
            if v is None:
                continue
            rec = self.fields.get(name)
            if rec is None:
                self.fields[name] = {"value": v, "t_first": iso(now), "t_last": iso(now)}
            elif rec["value"] != v:
                rec["value"], rec["t_last"] = v, iso(now)

    def got_any(self) -> bool:
        return bool(self.fields)

    def complete(self) -> bool:
        need = ("open_interest", "model_iv", "model_gamma", "bid", "ask") if self.kind == "option" \
            else ("open_interest", "bid", "ask")
        return all(n in self.fields for n in need)


# --- the probe ------------------------------------------------------------------------

class Probe:
    def __init__(self, ib, contracts, *, batch=BATCH, live_timeout=LIVE_TIMEOUT_S,
                 frozen_timeout=FROZEN_TIMEOUT_S, poll=POLL_S, now=lambda: datetime.now(timezone.utc)):
        self.ib, self.C = ib, contracts
        self.batch, self.live_timeout, self.frozen_timeout, self.poll = batch, live_timeout, frozen_timeout, poll
        self.now = now
        self.errors: list[dict] = []
        self.active: dict[int, object] = {}            # conId -> contract with a live subscription
        self.max_active = 0
        self.stop: ProbeError | None = None
        ib.errorEvent += self.on_error

    def on_error(self, reqId, errorCode, errorString, contract=None):
        self.errors.append({"t": iso(self.now()), "req_id": reqId, "code": errorCode, "message": errorString,
                            "con_id": getattr(contract, "conId", None)})
        if errorCode == 10197:
            self.stop = ProbeError("IBKR error 10197: no market data during a competing live session. COMEX "
                                   "data on the paper gateway works only while the live account is logged out "
                                   "everywhere (TWS, mobile, web). Log out there and run the probe again.", 4)
        elif errorCode == 101:
            self.stop = ProbeError(f"IBKR error 101, the market data line limit was hit: {errorString}. "
                                   f"Run again with a smaller --batch (now {self.batch}).", 5)

    def check_stop(self) -> None:
        if self.stop is not None:
            raise self.stop

    # 3. futures and expiries
    def futures(self) -> list:
        details = self.ib.reqContractDetails(self.C.Future(symbol=SYMBOL, exchange=EXCHANGE, currency=CURRENCY))
        self.check_stop()
        today = self.now().astimezone(NY).date().strftime("%Y%m%d")
        futs = [d for d in details if ymd(d.contract.lastTradeDateOrContractMonth) >= today]
        futs.sort(key=lambda d: ymd(d.contract.lastTradeDateOrContractMonth))
        if not futs:
            raise ProbeError("IBKR returned no GC futures", 1)
        return futs

    def expiries(self, futs: list) -> tuple[list[str], list[dict]]:
        chains, og = [], set()
        for d in futs[:CHAIN_FUTURES]:
            for ch in self.ib.reqSecDefOptParams(SYMBOL, EXCHANGE, "FUT", d.contract.conId):
                chains.append({"underlying_con_id": ch.underlyingConId, "exchange": ch.exchange,
                               "trading_class": ch.tradingClass, "multiplier": ch.multiplier,
                               "expirations": sorted(ch.expirations), "n_strikes": len(ch.strikes)})
                if ch.tradingClass == TRADING_CLASS and ch.exchange == EXCHANGE:
                    og.update(ymd(e) for e in ch.expirations)
            self.check_stop()
        first_ok = (self.now().astimezone(NY).date() + timedelta(days=MIN_DAYS_TO_EXPIRY)).strftime("%Y%m%d")
        chosen = sorted(e for e in og if e >= first_ok)[:N_EXPIRIES]
        if len(chosen) < N_EXPIRIES:
            raise ProbeError(f"only {len(chosen)} OG expiries at least {MIN_DAYS_TO_EXPIRY} days away: {chosen}", 1)
        return chosen, chains

    # 4. the chain
    def options(self, expiry: str) -> list:
        details = self.ib.reqContractDetails(self.C.FuturesOption(
            symbol=SYMBOL, lastTradeDateOrContractMonth=expiry, exchange=EXCHANGE, currency=CURRENCY,
            tradingClass=TRADING_CLASS))
        self.check_stop()
        return [d for d in details if d.contract.tradingClass == TRADING_CLASS and d.contract.right in ("C", "P")]

    # 5. market data
    def subscribe_pass(self, items: list, timeout: float) -> dict[int, Recorder]:
        """items: (contract, kind, right). Returns conId -> Recorder for every item."""
        out: dict[int, Recorder] = {}
        for part in chunks(items, self.batch):
            tickers = {}
            for contract, kind, right in part:
                out[contract.conId] = Recorder(kind, right)
                ticks = OPTION_TICKS if kind == "option" else FUTURE_TICKS
                tickers[contract.conId] = self.ib.reqMktData(contract, ticks, False, False)
                self.active[contract.conId] = contract
                self.max_active = max(self.max_active, len(self.active))
            start = self.now()
            try:
                while True:
                    self.ib.sleep(self.poll)
                    now = self.now()
                    for con_id, t in tickers.items():
                        out[con_id].update(t, now)
                    self.check_stop()
                    if all(out[c].complete() for c in tickers) or (now - start).total_seconds() >= timeout:
                        break
            finally:
                for contract, _kind, _right in part:
                    self.cancel(contract)
            for con_id, t in tickers.items():
                out[con_id].ib_market_data_type = getattr(t, "marketDataType", None)
        return out

    def cancel(self, contract) -> None:
        if self.active.pop(contract.conId, None) is not None:
            self.ib.cancelMktData(contract)

    def cancel_all(self) -> None:
        for contract in list(self.active.values()):
            try:
                self.cancel(contract)
            except Exception as e:                         # noqa: BLE001 - cancelling must not stop the others
                log(f"cancel {contract.conId}: {e!r}")

    def market_data(self, items: list) -> dict[int, tuple[str, Recorder]]:
        log(f"market data: {len(items)} contracts, live first, {self.batch} at a time")
        self.ib.reqMarketDataType(1)
        live = self.subscribe_pass(items, self.live_timeout)
        result = {c: ("live", r) for c, r in live.items() if r.got_any()}
        silent = [it for it in items if it[0].conId not in result]
        if silent:
            log(f"{len(silent)} contracts sent no live tick; asking for frozen data")
            self.ib.reqMarketDataType(2)
            frozen = self.subscribe_pass(silent, self.frozen_timeout)
            for c, r in frozen.items():
                result[c] = ("frozen" if r.got_any() else "none", r)
        return result

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
        futs = self.futures()
        by_con = {d.contract.conId: d for d in futs}
        chosen, chains = self.expiries(futs)
        log(f"OG expiries: {chosen}")
        opt_details = [d for e in chosen for d in self.options(e)]
        und_ids = sorted({d.underConId for d in opt_details})
        for u in und_ids:
            if u not in by_con:                            # not in the list: ask IBKR for it by conId
                found = self.ib.reqContractDetails(self.C.Contract(conId=u))
                self.check_stop()
                if not found:
                    raise ProbeError(f"underlying conId {u} not found", 1)
                by_con[u] = found[0]
        items = [(by_con[u].contract, "future", None) for u in und_ids] + \
                [(d.contract, "option", d.contract.right) for d in opt_details]
        data = self.market_data(items)
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
            "chains": chains,
            "selected_expiries": chosen,
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
    if not 1 <= args.batch <= 90:
        ap.error("--batch must be 1..90 (IBKR's default market data line limit is 100)")

    reason = closed_reason(now())
    if reason:
        log(f"refusing to run: {reason}. Run while COMEX is open on a regular trading day "
            f"(e.g. 05:00 UTC, Monday to Friday).")
        return 3

    if ib is None:
        from ib_insync import IB
        ib = IB()
    probe = Probe(ib, contracts or _Contracts(), batch=args.batch, live_timeout=args.live_timeout,
                  frozen_timeout=args.frozen_timeout, now=now)
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
