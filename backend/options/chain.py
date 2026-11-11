"""COMEX gold options (OG on GC): chain discovery and batched market data, read-only.

Factored out of tools/options_probe.py (research/gex_feasibility) unchanged in
behaviour, so the probe and the daily snapshot (backend.options.snapshot) ask
IBKR the same way:

1. COMEX hours: refuse weekends, the daily 17:00-18:00 New York break and CME
   holidays (HOLIDAYS covers 2026-2027; outside them, refuse too).
2. List the GC futures and ask for their option chains (reqSecDefOptParams)
   one by one, in expiry order, until the next N OG expiries at least 2
   calendar days away are found (at most CHAIN_FUTURES futures; IBKR lists GC
   months consecutively, and OG options sit on only some of them). Fewer
   than N: the next 2 are used and the fallback is recorded; fewer than 2: stop.
3. List every OG contract of those expiries (contract details: strike, right,
   underConId, expiry time), and their underlying GC futures.
4. Request market data in batches of at most `batch` lines: options with
   generic tick 101 (open interest), futures with 588. Live data (type 1)
   first; a contract with no tick at all within the live timeout is requested
   again as frozen data (type 2). Every value is recorded with the UTC time it
   was first received and last changed. Each batch is cancelled before the
   next; cancel_all() cancels whatever is left.

Stdlib only; the caller passes the ib_insync IB object and contract classes.
"""

from __future__ import annotations

import math
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
SYMBOL, EXCHANGE, CURRENCY, TRADING_CLASS = "GC", "COMEX", "USD", "OG"
N_EXPIRIES = 3
MIN_EXPIRIES = 2           # fewer than 3 eligible expiries: use 2, recorded (PREREG Amendment 1)
MIN_DAYS_TO_EXPIRY = 2
CHAIN_FUTURES = 12         # at most this many GC months are asked for their option chains
BATCH = 50                 # market data lines at a time (IBKR's default limit is 100)
MAX_BATCH = 90
LIVE_TIMEOUT_S = 15.0
FROZEN_TIMEOUT_S = 15.0
POLL_S = 0.25
OPTION_TICKS = "101"       # option open interest
FUTURE_TICKS = "588"       # futures open interest

# CME holidays on which nothing runs: full closures and the abbreviated,
# no-settlement sessions (keyed by CME trade date).
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

# option ticker fields recorded, by name in the output -> how to read them
PRICE_FIELDS = ("bid", "ask", "last", "close")
GREEK_FIELDS = (("model_iv", "impliedVol"), ("model_gamma", "gamma"), ("model_delta", "delta"),
                ("model_und_price", "undPrice"), ("model_opt_price", "optPrice"))


class ChainError(Exception):
    """A clean stop with a message and an exit status: 1 anything, 4 error
    10197 (a competing live session), 5 the market data line limit (error 101)."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def stderr_log(name: str):
    def log(msg: str) -> None:
        print(f"{datetime.now(timezone.utc):%H:%M:%S} {name}: {msg}", file=sys.stderr, flush=True)
    return log


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

    def __init__(self, kind: str, right: str | None = None, greeks=GREEK_FIELDS):
        self.kind, self.right, self.greeks = kind, right, greeks
        self.fields: dict[str, dict] = {}
        self.ib_market_data_type = None            # what IBKR reported at the end (1 live, 2 frozen, ...)

    def _read(self, ticker) -> dict:
        out = {f: num(getattr(ticker, f, None)) for f in PRICE_FIELDS}
        if self.kind == "option":
            oi = ticker.callOpenInterest if self.right == "C" else ticker.putOpenInterest
            out["open_interest"] = num(oi)
            g = getattr(ticker, "modelGreeks", None)
            for name, attr in self.greeks:
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


# --- the chain ------------------------------------------------------------------------

class ChainSnapshot:
    """One pass over the OG chain on an IB connection the caller opened.
    collect() does steps 2-4 of the module docstring; the caller cancels
    (cancel_all) and disconnects in a finally."""

    def __init__(self, ib, contracts, *, batch=BATCH, live_timeout=LIVE_TIMEOUT_S,
                 frozen_timeout=FROZEN_TIMEOUT_S, poll=POLL_S, now=lambda: datetime.now(timezone.utc),
                 log=stderr_log("options"), chain_futures=CHAIN_FUTURES, n_expiries=N_EXPIRIES,
                 greeks=GREEK_FIELDS):
        self.ib, self.C = ib, contracts
        self.batch, self.live_timeout, self.frozen_timeout, self.poll = batch, live_timeout, frozen_timeout, poll
        self.now, self.log = now, log
        self.chain_futures, self.n_expiries, self.greeks = chain_futures, n_expiries, greeks
        self.errors: list[dict] = []
        self.active: dict[int, object] = {}            # conId -> contract with a live subscription
        self.max_active = 0
        self.stop: ChainError | None = None
        ib.errorEvent += self.on_error

    def on_error(self, reqId, errorCode, errorString, contract=None):
        self.errors.append({"t": iso(self.now()), "req_id": reqId, "code": errorCode, "message": errorString,
                            "con_id": getattr(contract, "conId", None)})
        if errorCode == 10197:
            self.stop = ChainError("IBKR error 10197: no market data during a competing live session. COMEX "
                                   "data on the paper gateway works only while the live account is logged out "
                                   "everywhere (TWS, mobile, web). Log out there and run again.", 4)
        elif errorCode == 101:
            self.stop = ChainError(f"IBKR error 101, the market data line limit was hit: {errorString}. "
                                   f"Run again with a smaller --batch (now {self.batch}).", 5)

    def check_stop(self) -> None:
        if self.stop is not None:
            raise self.stop

    # 2. futures and expiries
    def futures(self) -> list:
        details = self.ib.reqContractDetails(self.C.Future(symbol=SYMBOL, exchange=EXCHANGE, currency=CURRENCY))
        self.check_stop()
        today = self.now().astimezone(NY).date().strftime("%Y%m%d")
        futs = [d for d in details if ymd(d.contract.lastTradeDateOrContractMonth) >= today]
        futs.sort(key=lambda d: ymd(d.contract.lastTradeDateOrContractMonth))
        if not futs:
            raise ChainError("IBKR returned no GC futures", 1)
        return futs

    def expiries(self, futs: list) -> tuple[list[str], list[dict], dict]:
        """The OG expiries to snapshot, from the GC futures' option chains, asked
        future by future in expiry order until n_expiries eligible ones are found."""
        first_ok = (self.now().astimezone(NY).date() + timedelta(days=MIN_DAYS_TO_EXPIRY)).strftime("%Y%m%d")
        chains: list[dict] = []
        sources: dict[str, list[int]] = {}             # OG expiry -> underlyings whose chain lists it
        queried: list[str] = []
        for d in futs[:self.chain_futures]:
            fut = d.contract
            got, other = set(), set()
            for ch in self.ib.reqSecDefOptParams(SYMBOL, EXCHANGE, "FUT", fut.conId):
                chains.append({"queried_con_id": fut.conId, "queried_local_symbol": fut.localSymbol,
                               "underlying_con_id": int(ch.underlyingConId), "exchange": ch.exchange,
                               "trading_class": ch.tradingClass, "multiplier": ch.multiplier,
                               "expirations": sorted(ymd(e) for e in ch.expirations), "n_strikes": len(ch.strikes)})
                if ch.tradingClass == TRADING_CLASS and ch.exchange == EXCHANGE:
                    for e in map(ymd, ch.expirations):
                        got.add(e)
                        und = int(ch.underlyingConId)      # IBKR sends it as a string
                        if und not in sources.setdefault(e, []):
                            sources[e].append(und)
                else:
                    other.add(ch.tradingClass)
            self.check_stop()
            queried.append(fut.localSymbol)
            self.log(f"chain of {fut.localSymbol} (conId {fut.conId}): OG expiries {', '.join(sorted(got)) or 'none'}"
                     + (f"; other classes {', '.join(sorted(other))}" if other else ""))
            if sum(e >= first_ok for e in sources) >= self.n_expiries:
                break
        eligible = sorted(e for e in sources if e >= first_ok)
        chosen, fallback = eligible[:self.n_expiries], None
        if len(chosen) < self.n_expiries:
            found = f"after asking {len(queried)} GC futures ({', '.join(queried)})"
            if len(chosen) < MIN_EXPIRIES:
                raise ChainError(f"only {len(chosen)} OG expiries at least {MIN_DAYS_TO_EXPIRY} days away {found}: "
                                 f"{chosen}; all OG expiries seen: {sorted(sources)}", 1)
            fallback = (f"only {len(chosen)} OG expiries at least {MIN_DAYS_TO_EXPIRY} days away {found}; "
                        f"using {len(chosen)} (PREREG Amendment 1)")
            self.log(fallback)
        return chosen, chains, {"queried": queried, "sources": {e: sources[e] for e in chosen}, "fallback": fallback}

    # 3. the chain
    def options(self, expiry: str) -> list:
        details = self.ib.reqContractDetails(self.C.FuturesOption(
            symbol=SYMBOL, lastTradeDateOrContractMonth=expiry, exchange=EXCHANGE, currency=CURRENCY,
            tradingClass=TRADING_CLASS))
        self.check_stop()
        return [d for d in details if d.contract.tradingClass == TRADING_CLASS and d.contract.right in ("C", "P")]

    # 4. market data
    def subscribe_pass(self, items: list, timeout: float) -> dict[int, Recorder]:
        """items: (contract, kind, right). Returns conId -> Recorder for every item."""
        out: dict[int, Recorder] = {}
        for part in chunks(items, self.batch):
            tickers = {}
            for contract, kind, right in part:
                out[contract.conId] = Recorder(kind, right, self.greeks)
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
                self.log(f"cancel {contract.conId}: {e!r}")

    def market_data(self, items: list) -> dict[int, tuple[str, Recorder]]:
        self.log(f"market data: {len(items)} contracts, live first, {self.batch} at a time")
        self.ib.reqMarketDataType(1)
        live = self.subscribe_pass(items, self.live_timeout)
        result = {c: ("live", r) for c, r in live.items() if r.got_any()}
        silent = [it for it in items if it[0].conId not in result]
        if silent:
            self.log(f"{len(silent)} contracts sent no live tick; asking for frozen data")
            self.ib.reqMarketDataType(2)
            frozen = self.subscribe_pass(silent, self.frozen_timeout)
            for c, r in frozen.items():
                result[c] = ("frozen" if r.got_any() else "none", r)
        return result

    def collect(self) -> dict:
        """Steps 2-4. Returns the futures list, the chosen expiries and how they
        were found, the option contract details, the underlyings (by each
        option's underConId) and conId -> (data type, Recorder) for all of them."""
        futs = self.futures()
        by_con = {d.contract.conId: d for d in futs}
        chosen, chains, found = self.expiries(futs)
        opt_details = [d for e in chosen for d in self.options(e)]
        expiry_sources = []
        for e in chosen:
            from_details = sorted({d.underConId for d in opt_details if ymd(d.contract.lastTradeDateOrContractMonth) == e})
            from_chain = found["sources"][e]
            expiry_sources.append({"expiry": e, "chain_underlying_con_ids": from_chain,
                                   "details_under_con_ids": from_details})
            names = [by_con[c].contract.localSymbol if c in by_con else str(c) for c in from_chain]
            self.log(f"OG expiry {e}: underlying {', '.join(names)} (from its chain)"
                     + ("" if set(from_details) <= set(from_chain) else
                        f"; contract details name {from_details} instead, and those are used"))
        und_ids = sorted({d.underConId for d in opt_details})
        for u in und_ids:
            if u not in by_con:                            # not in the list: ask IBKR for it by conId
                got = self.ib.reqContractDetails(self.C.Contract(conId=u))
                self.check_stop()
                if not got:
                    raise ChainError(f"underlying conId {u} not found", 1)
                by_con[u] = got[0]
        items = [(by_con[u].contract, "future", None) for u in und_ids] + \
                [(d.contract, "option", d.contract.right) for d in opt_details]
        data = self.market_data(items)
        return {"futures": futs, "by_con": by_con, "chosen": chosen, "chains": chains, "found": found,
                "expiry_sources": expiry_sources, "opt_details": opt_details, "und_ids": und_ids, "data": data}
