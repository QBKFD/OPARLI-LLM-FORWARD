"""A fake IBKR client for the probe's tests: the subset of ib_insync.IB the
probe uses, with scripted answers and a clock that moves only on sleep().

Contracts (by conId) can be scripted to answer live, only to frozen data,
never, or to raise an IBKR error when subscribed. The fake tracks open
subscriptions, so a test can check the line limit and that everything was
cancelled.
"""

from __future__ import annotations

import math
from collections import namedtuple
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

OptionChain = namedtuple("OptionChain", "exchange underlyingConId tradingClass multiplier expirations strikes")
BarData = namedtuple("BarData", "date open high low close volume average barCount")
OptionComputation = namedtuple("OptionComputation",
                               "tickAttrib impliedVol delta optPrice pvDividend gamma vega theta undPrice")


@dataclass
class Contract:
    conId: int = 0
    symbol: str = ""
    secType: str = ""
    lastTradeDateOrContractMonth: str = ""
    strike: float = 0.0
    right: str = ""
    multiplier: str = ""
    exchange: str = ""
    currency: str = ""
    localSymbol: str = ""
    tradingClass: str = ""


def Future(**kw):
    return Contract(secType="FUT", **kw)


def FuturesOption(**kw):
    return Contract(secType="FOP", **kw)


class Contracts:
    """Stands in for the probe's _Contracts (ib_insync's classes)."""
    Contract, Future, FuturesOption = Contract, staticmethod(Future), staticmethod(FuturesOption)


@dataclass
class Details:
    contract: Contract
    underConId: int = 0
    realExpirationDate: str = ""
    lastTradeTime: str = ""
    timeZoneId: str = ""


@dataclass
class Ticker:
    contract: Contract
    marketDataType: int = 1
    bid: float = math.nan
    ask: float = math.nan
    last: float = math.nan
    close: float = math.nan
    callOpenInterest: float = math.nan
    putOpenInterest: float = math.nan
    futuresOpenInterest: float = math.nan
    modelGreeks: object = None


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def emit(self, *a):
        for h in self.handlers:
            h(*a)


@dataclass
class Script:
    """How one contract answers: `answers` is "live", "frozen" (only to type 2) or
    "never"; `values` are the Ticker fields set when it answers; `error` an IBKR
    error code raised when it is subscribed."""
    answers: str = "live"
    values: dict = field(default_factory=dict)
    error: int | None = None


class FakeIB:
    def __init__(self, start: datetime, futures: list[Details], chains: dict[int, list], options: dict[str, list],
                 scripts: dict[int, Script], bars: dict[int, list] | None = None):
        self.t = start
        self.futures, self.chains, self.options_by_exp = futures, chains, options
        self.scripts, self.bars = scripts, bars or {}
        self.errorEvent = Event()
        self.connected = None
        self.disconnected = False
        self.data_type = 1
        self.data_types_requested: list[int] = []
        self.active: dict[int, Ticker] = {}
        self.max_active = 0
        self.subscribed: list[tuple[int, str, int]] = []      # (conId, generic ticks, data type)
        self.cancelled: list[int] = []
        self._answer_at: dict[int, datetime] = {}

    def now(self) -> datetime:
        return self.t

    # connection
    def connect(self, host, port, clientId, readonly=False, timeout=4):
        self.connected = dict(host=host, port=port, clientId=clientId, readonly=readonly)

    def disconnect(self):
        self.disconnected = True

    def sleep(self, s):
        self.t += timedelta(seconds=s)
        for con_id, tk in self.active.items():
            sc = self.scripts.get(con_id, Script())
            if self.t < self._answer_at[con_id]:
                continue
            if sc.answers == "live" or (sc.answers == "frozen" and self.data_type == 2):
                tk.marketDataType = self.data_type
                for k, v in sc.values.items():
                    setattr(tk, k, v)

    # reference data
    def reqContractDetails(self, c: Contract):
        if c.secType == "FUT":
            return list(self.futures)
        if c.secType == "FOP":
            return list(self.options_by_exp.get(c.lastTradeDateOrContractMonth, []))
        return [d for d in self.futures if d.contract.conId == c.conId]

    def reqSecDefOptParams(self, sym, exch, sec_type, con_id):
        return list(self.chains.get(con_id, []))

    # market data
    def reqMarketDataType(self, t):
        self.data_type = t
        self.data_types_requested.append(t)

    def reqMktData(self, contract, genericTickList="", snapshot=False, regulatorySnapshot=False):
        tk = Ticker(contract)
        self.active[contract.conId] = tk
        self.max_active = max(self.max_active, len(self.active))
        self.subscribed.append((contract.conId, genericTickList, self.data_type))
        self._answer_at[contract.conId] = self.t + timedelta(seconds=0.5)
        sc = self.scripts.get(contract.conId)
        if sc and sc.error:
            self.errorEvent.emit(1, sc.error, f"fake error {sc.error}", contract)
        return tk

    def cancelMktData(self, contract):
        self.active.pop(contract.conId, None)
        self.cancelled.append(contract.conId)

    def reqHistoricalData(self, contract, endDateTime, durationStr, barSizeSetting, whatToShow, useRTH,
                          formatDate=1):
        return list(self.bars.get(contract.conId, []))


# --- a small world: GC futures, OG options, bars ------------------------------------

def daily_bars(last_day: date, n: int, close: float, volume: float) -> list:
    out, d = [], last_day
    while len(out) < n:
        if d.weekday() < 5:
            out.append(BarData(d, close, close, close, close, volume, close, 1))
        d -= timedelta(days=1)
    return sorted(out, key=lambda b: b.date)


# GC futures as IBKR lists them: consecutive months. OG options sit only on some
# (Z6 carries the Nov and Dec options, G7 the Jan and Feb ones, J7 Mar and Apr).
GC_MONTHS = [(98, "GCV6", "20261028"), (99, "GCX6", "20261125"), (100, "GCZ6", "20261229"),
             (103, "GCF7", "20270127"), (101, "GCG7", "20270224"), (104, "GCH7", "20270326"),
             (102, "GCJ7", "20270428")]
OG_UNDERLYING = {"20260924": 98, "20261023": 100, "20261027": 100, "20261124": 100,
                 "20261228": 101, "20270126": 101, "20270223": 102, "20270325": 102}


def world(start: datetime, *, expiries=("20261023", "20261027", "20261124", "20261228", "20270126"),
          strikes=(4000.0, 4100.0, 4200.0), scripts=None, n_bars=22, bar_volume=150_000.0, months=GC_MONTHS):
    """GC futures listed month by month (GCV6 .. GCJ7). OG expiries in October and
    November sit on GCZ6, December and January on GCG7, as IBKR maps them. Only
    GCZ6, GCG7 and GCJ7 have OG chains; the other months list a weekly class or
    nothing. The first expiry is too close to be picked when the start is 2026-10-22,
    so with the default expiries GCG7 (the 5th month) must be asked to find 3."""
    gc = [Details(Future(conId=c, symbol="GC", lastTradeDateOrContractMonth=e, multiplier="100",
                         exchange="COMEX", localSymbol=sym)) for c, sym, e in months]
    under = OG_UNDERLYING
    chains = {c: [] for c, _, _ in months}
    for e in expiries:
        chains[under[e]].append(e)
    chains = {c: ([OptionChain("COMEX", c, "OG", "100", exps, list(strikes))] if exps else [])
              + ([OptionChain("COMEX", c, "G1W", "100", ["20261030"], list(strikes))] if c == 99 else [])
              for c, exps in chains.items()}
    options, con = {}, 1000
    for e in expiries:
        rows = []
        for k in strikes:
            for right in ("C", "P"):
                con += 1
                rows.append(Details(FuturesOption(conId=con, symbol="GC", lastTradeDateOrContractMonth=e, strike=k,
                                                  right=right, multiplier="100", exchange="COMEX",
                                                  localSymbol=f"OG {e} {right}{int(k)}", tradingClass="OG"),
                                    underConId=under[e], realExpirationDate=e, lastTradeTime="12:30:00",
                                    timeZoneId="US/Central"))
        options[e] = rows
    sc = dict(scripts or {})
    for d in gc:
        sc.setdefault(d.contract.conId, Script(values=dict(bid=4149.9, ask=4150.1, last=4150.0, close=4140.0,
                                                           futuresOpenInterest=322_500.0)))
    for e, rows in options.items():
        for d in rows:
            c = d.contract
            oi = "callOpenInterest" if c.right == "C" else "putOpenInterest"
            sc.setdefault(c.conId, Script(values={
                "bid": 10.0, "ask": 11.0, oi: 500.0,
                "modelGreeks": OptionComputation(0, 0.25, 0.5, 10.5, 0.0, 0.0015, 1.0, -1.0, 4150.0)}))
    last_day = start.date() - timedelta(days=1)
    bars = {d.contract.conId: daily_bars(last_day, n_bars, 4150.0, bar_volume if d.contract.conId == 100 else 1_000.0)
            for d in gc}
    return FakeIB(start, gc, chains, options, sc, bars)
