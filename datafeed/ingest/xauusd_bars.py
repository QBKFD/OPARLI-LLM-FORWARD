"""XAUUSD ticks -> 1-min bars (pure logic, no network).

The bar contract is the production scanner's (tws_connector.py CMDTY path +
deterministic_scanner.py filters, see docs/DATAFEED_NOTES.md section 1), with five
approved deviations, each counted in health():

  #1 one-sided quote  The old code fell back to ticker.close (the previous
                      session's close) when bid or ask was missing. Here the
                      tick is skipped.                      -> skipped_one_sided
  #2 late tick        The old code folded a tick from an earlier minute into the
                      current bar. Here it is dropped. A tick is late if its
                      minute is already closed, older than the open bar, or
                      past its minute's end + GRACE_S.      -> late_ticks
  #3 bar close timer  The old code closed a bar only when the next minute's
                      first tick arrived. Here a bar also closes at minute
                      end + GRACE_S, via advance(now).      -> timer_closes
  #4 missing tick ts  The old code used naive local datetime.now(), later
                      labelled UTC. Here it is the injected UTC `now`. Naive
                      tick times are rejected.              -> ticks_without_ts
  #7 history overlap  History bars from the forming minute onward are
                      dropped (trim_history).

Unchanged: price = (bid + ask) / 2 when both > 0; bar_ts = minute OPEN time
(UTC); open/high/low/close = first/max/min/last tick; minutes without ticks
produce no bar; n_ticks counts quote-update events, including ones where the
midpoint didn't change (#5, documented, not a price-change count). No partial
(is_final=False) bars are produced (#6).

Sources (both publish bar messages to STREAM, see bar_message):
  IbkrXauusdSource  live: history warm-up (history=True), then tick-built bars
  ReplaySource      parquet replay for local runs without IBKR

Volume: none. Live CMDTY XAUUSD carries no usable volume (0 in the old bars),
so bar messages have no volume field rather than an invented one.
"""

from __future__ import annotations

import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from datafeed.ingest.base import Publish, make_message

log = logging.getLogger(__name__)

STREAM = "bars.xauusd.1m"
SCHEMA_VERSION = 1
BAR_S = 60
LOOP_S = 0.25            # live event-loop slice; the bar timer (advance) runs this often
GRACE_S = 2.0            # bar closes at minute end + GRACE_S; later ticks for it are late

_NY = ZoneInfo("America/New_York")


# ── Lifted from backend/deterministic_scanner.py (behaviour unchanged) ──────
def _market_open(now) -> bool:
    """Approximate XAUUSD (spot gold) trading window. Sun 18:00 -> Fri 17:00 New
    York time, minus the daily 17:00-18:00 break, evaluated in New York time so
    it is right on both sides of US DST. Holidays aren't modelled. A naive
    timestamp raises, as the original's tz_convert does.
    (deterministic_scanner.py:497-513; astimezone instead of pandas tz_convert,
    so this module does not need pandas.)"""
    if now.tzinfo is None or now.utcoffset() is None:
        raise TypeError("_market_open needs a tz-aware timestamp")
    et = now.astimezone(_NY)
    wd, h = et.weekday(), et.hour       # Mon=0 .. Sun=6
    if wd == 5:                          # Saturday
        return False
    if wd == 6:                          # Sunday: reopens 18:00 ET
        return h >= 18
    if wd == 4 and h >= 17:              # Friday close
        return False
    if h == 17:                          # daily maintenance break
        return False
    return True


def _valid_bar(ts, o, h, l, c) -> bool:
    """Only real prices inside trading hours: drops IBKR's -1 "no quote", NaN,
    non-positive prices, low > high, and bars opening outside _market_open.
    (deterministic_scanner.py:524-530)"""
    return _market_open(ts) and all(x == x and x > 0 for x in (o, h, l, c)) and l <= h


def valid_bar(ts, o, h, l, c, calendar: str = "xauusd") -> bool:
    """_valid_bar for any instrument calendar (datafeed.instrument.CALENDARS).
    For "xauusd" it is exactly _valid_bar; "24/7" drops no bar for its time."""
    if calendar == "xauusd":
        return _valid_bar(ts, o, h, l, c)
    from datafeed.instrument import market_open
    return market_open(ts, calendar) and all(x == x and x > 0 for x in (o, h, l, c)) and l <= h


def _px(x) -> bool:
    """A usable quote: not None, not NaN, > 0 (IBKR sends -1 or 0 for "no quote")."""
    return x is not None and x == x and x > 0


def _minute(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


@dataclass(frozen=True)
class Bar:
    symbol: str
    bar_ts: datetime        # minute open, UTC
    open: float
    high: float
    low: float
    close: float
    n_ticks: int | None     # None for history/replay bars (no tick count)

    def ohlc(self):
        return (self.bar_ts, self.open, self.high, self.low, self.close)


class BarBuilder:
    """Ticks in, completed bars out. Feed on_tick() for each quote update and
    call advance(now) on a timer (about once a second) so quiet minutes still
    close. `now` is the receive time (tz-aware UTC) from the caller's clock.
    Returned bars are unfiltered; apply _valid_bar before publishing."""

    def __init__(self, symbol: str = "XAUUSD", grace_s: float = GRACE_S) -> None:
        self.symbol = symbol
        self.grace = timedelta(seconds=grace_s)
        self._cur: datetime | None = None       # open bar's minute
        self._prices: list[float] = []
        self._last_closed: datetime | None = None
        self.counters = dict(ticks_in=0, ticks_used=0, dropped_no_quote=0,
                             skipped_one_sided=0, late_ticks=0, ticks_without_ts=0,
                             timer_closes=0, bars_closed=0)

    def _deadline(self, minute: datetime) -> datetime:
        return minute + timedelta(seconds=BAR_S) + self.grace

    def _close(self) -> Bar:
        p = self._prices
        bar = Bar(self.symbol, self._cur, float(p[0]), float(max(p)), float(min(p)),
                  float(p[-1]), len(p))
        self._last_closed, self._cur, self._prices = self._cur, None, []
        self.counters["bars_closed"] += 1
        return bar

    def advance(self, now: datetime) -> list[Bar]:
        """Close the open bar if its minute ended more than grace ago (#3)."""
        if self._cur is not None and now >= self._deadline(self._cur):
            self.counters["timer_closes"] += 1
            return [self._close()]
        return []

    def on_tick(self, now: datetime, time: datetime | None, bid, ask, close=None) -> list[Bar]:
        """One quote update. `time` is the ticker's timestamp (None if IBKR gave
        none); bid/ask/close are the ticker's fields. Returns bars closed by it."""
        if now.tzinfo is None:
            raise ValueError("now must be tz-aware UTC")
        out = self.advance(now)
        c = self.counters
        c["ticks_in"] += 1
        if _px(bid) and _px(ask):
            price = (float(bid) + float(ask)) / 2.0
        elif _px(close):
            c["skipped_one_sided"] += 1                      # #1: no stale close
            return out
        else:
            c["dropped_no_quote"] += 1                       # as before: no price at all
            return out
        if not time:
            c["ticks_without_ts"] += 1                       # #4: UTC receive time
            time = now
        elif time.tzinfo is None:
            raise ValueError(f"naive tick time {time!r}")
        time = time.astimezone(timezone.utc)
        m = _minute(time)
        if ((self._last_closed is not None and m <= self._last_closed)
                or (self._cur is not None and m < self._cur)
                or now >= self._deadline(m)):
            c["late_ticks"] += 1                             # #2: never re-opened or folded
            return out
        if self._cur is None:
            self._cur = m
        elif m > self._cur:
            out.append(self._close())                        # rollover, as before
            self._cur = m
        self._prices.append(price)
        c["ticks_used"] += 1
        return out

    def health(self) -> dict:
        return {**self.counters,
                "open_bar_ts": self._cur.isoformat() if self._cur else None,
                "last_closed_bar_ts": self._last_closed.isoformat() if self._last_closed else None}


def trim_history(bars, now: datetime) -> list:
    """#7: drop history bars whose minute is the forming minute or later. Each
    item is a Bar or a tuple starting with the tz-aware bar_ts."""
    cutoff = _minute(now.astimezone(timezone.utc))
    return [b for b in bars if (b.bar_ts if isinstance(b, Bar) else b[0]) < cutoff]


def bar_message(bar: Bar, *, source: str, history: bool,
                ingest_ts: datetime | None = None) -> dict:
    """Bar -> bus message (schema_version 1). source_ts is the bar's open time.
    history=True marks warm-up bars from the IBKR history load, never live."""
    return make_message(
        schema_version=SCHEMA_VERSION, source=source, symbol=bar.symbol,
        source_ts=bar.bar_ts, ingest_ts=ingest_ts,
        payload=dict(bar_ts=bar.bar_ts.astimezone(timezone.utc).isoformat(),
                     open=bar.open, high=bar.high, low=bar.low, close=bar.close,
                     n_ticks=bar.n_ticks, history=history))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IbkrXauusdSource:
    """Live source on the one IBKR connection (datafeed.ingest.ibkr).

    1. connect; failure -> crash-only exit
    2. 10-day MIDPOINT history, trimmed at the forming minute (#7), published
       with history=True (warm-up, never live)
    3. stream quotes -> BarBuilder; the loop runs advance() every LOOP_S (#3)
    4. connection lost -> crash-only exit
    Only bars passing valid_bar for the instrument's calendar are published;
    the rest are counted. The stream and calendar come from conn.instrument
    (default XAUUSD: STREAM and _valid_bar, as before)."""

    name = "ibkr"
    stream = STREAM
    schema_version = SCHEMA_VERSION

    def __init__(self, conn, clock=_utcnow, sleep=_time.sleep, max_loops: int | None = None) -> None:
        self.conn, self.clock, self._sleep, self.max_loops = conn, clock, sleep, max_loops
        inst = getattr(conn, "instrument", None)
        self.stream = inst.stream if inst is not None else STREAM
        self.calendar = inst.calendar if inst is not None else "xauusd"
        self.builder = BarBuilder(conn.symbol)
        self.stats = dict(history_bars=0, live_bars=0, invalid_bars=0)
        self.last_bar_ts: str | None = None
        self._publish: Publish | None = None

    def _emit(self, bars, history: bool) -> None:
        for bar in bars:
            if not valid_bar(*bar.ohlc(), self.calendar):
                self.stats["invalid_bars"] += 1
                continue
            self._publish(bar_message(bar, source=self.name, history=history, ingest_ts=self.clock()))
            self.stats["history_bars" if history else "live_bars"] += 1
            self.last_bar_ts = bar.bar_ts.isoformat()

    def on_tickers(self, tickers) -> None:
        now = self.clock()
        bars = []
        for t in tickers:
            if getattr(t, "contract", None) is None or t.contract.conId != self.conn.contract.conId:
                continue
            bars += self.builder.on_tick(now, t.time, t.bid, t.ask, t.close)
        self._emit(bars, history=False)

    def run(self, publish: Publish) -> None:
        from datafeed.ingest.ibkr import crash
        self._publish = publish
        try:
            self.conn.connect()
        except Exception as e:
            crash(f"could not connect to gateway ({e!r})", self._sleep)
        try:
            hist = self.conn.history()
        except Exception as e:
            self.conn.disconnect()
            crash(f"history load failed ({e!r})", self._sleep)
        self._emit(trim_history(hist, self.clock()), history=True)
        self.conn.subscribe(self.on_tickers)
        log.info("ibkr: %d history bars published, streaming %s",
                 self.stats["history_bars"], self.conn.symbol)
        loops = 0
        while self.max_loops is None or loops < self.max_loops:
            loops += 1
            if not self.conn.connected():
                self.conn.disconnect()
                crash("gateway connection lost", self._sleep)
            self.conn.sleep(LOOP_S)
            self._emit(self.builder.advance(self.clock()), history=False)

    def health(self) -> dict:
        connected = self.conn.contract is not None and self.conn.connected()
        return {"source": self.name, "connected": connected, "last_bar_ts": self.last_bar_ts,
                **self.stats, **self.builder.health()}


# Research data seal (research/SEALING.md): nothing at or after this instant may
# be replayed. A separate constant on purpose (datafeed must not import research
# code); tests/datafeed/test_sources.py asserts it equals research/sealing.py's
# GLOBAL_SEAL. No override.
SEAL_START = datetime(2026, 10, 1, tzinfo=timezone.utc)


# First and last bar_ts (inclusive) of database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet.
# A replay window must lie inside [first, last + 1 min); anything else is an error,
# never a silently shorter replay.
PARQUET_COVERAGE = (datetime(2020, 1, 1, 23, 0, tzinfo=timezone.utc),
                    datetime(2026, 1, 30, 21, 59, tzinfo=timezone.utc))


class CoverageError(ValueError):
    """A replay window reaches outside the parquet's coverage."""


class SealedDataError(RuntimeError):
    """A replay window or row reaches the sealed period."""


def _utc_or_none(s):
    if not s:
        return None
    ts = datetime.fromisoformat(s) if isinstance(s, str) else s
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


class ReplaySource:
    """Historical 1-min bars from parquet, for local runs without IBKR.

    Same message schema as the live source: source="replay", history=False,
    n_ticks=None (no tick counts in the parquet). Only rows passing _valid_bar
    are published, in timestamp order. The parquet's vendor volume is not
    published.

    Coverage: the window must lie inside `coverage` (default PARQUET_COVERAGE,
    first and last bar_ts inclusive), else CoverageError. A missing start or
    end means the coverage bound.

    Seal guard: a window ending after SEAL_START is refused
    before anything is published, and any row at or after SEAL_START stops the
    run with SealedDataError. No override.

    Env: DATAFEED_REPLAY_PARQUET (required), DATAFEED_REPLAY_START (inclusive),
    DATAFEED_REPLAY_END (exclusive), DATAFEED_REPLAY_BARS_PER_S (0 = as fast
    as possible, the default). Dates are UTC."""

    name = "replay"
    stream = STREAM
    schema_version = SCHEMA_VERSION

    def __init__(self, path: str, symbol: str = "XAUUSD", start=None, end=None,
                 bars_per_s: float = 0.0, sleep=_time.sleep,
                 coverage: tuple = PARQUET_COVERAGE) -> None:
        self.path, self.symbol = path, symbol
        first, last = coverage
        self.coverage = (first, last)
        limit = last + timedelta(seconds=BAR_S)                  # exclusive end of coverage
        self.start = _utc_or_none(start) or first
        self.end = _utc_or_none(end) or limit
        if not (first <= self.start < self.end <= limit):
            raise CoverageError(
                f"replay window [{self.start.isoformat()}, {self.end.isoformat()}) is outside the "
                f"parquet coverage [{first.isoformat()}, {last.isoformat()}] (bar_ts, inclusive)")
        if self.end > SEAL_START:
            raise SealedDataError(f"replay window must end on or before {SEAL_START.isoformat()} "
                                  f"(research seal); got end={self.end}")
        self.bars_per_s, self._sleep = bars_per_s, sleep
        self.stats = dict(rows=0, published=0, invalid_bars=0)
        self.last_bar_ts: str | None = None
        self.done = False

    @classmethod
    def from_env(cls, env=os.environ) -> "ReplaySource":
        path = env.get("DATAFEED_REPLAY_PARQUET")
        if not path:
            raise RuntimeError("set DATAFEED_REPLAY_PARQUET")
        return cls(path, start=env.get("DATAFEED_REPLAY_START"), end=env.get("DATAFEED_REPLAY_END"),
                   bars_per_s=float(env.get("DATAFEED_REPLAY_BARS_PER_S") or 0))

    def rows(self):
        """(bar_ts, o, h, l, c) in timestamp order, inside [start, end)."""
        import pandas as pd
        filters = []
        if self.start is not None:
            filters.append(("timestamp", ">=", pd.Timestamp(self.start)))
        if self.end is not None:
            filters.append(("timestamp", "<", pd.Timestamp(self.end)))
        df = pd.read_parquet(self.path, columns=["timestamp", "open", "high", "low", "close"],
                             filters=filters or None)
        ts = pd.DatetimeIndex(df["timestamp"])
        ts = ts.tz_localize("UTC") if ts.tz is None else ts.tz_convert("UTC")
        df = df.assign(timestamp=ts).sort_values("timestamp", kind="stable")
        for t, o, h, l, c in df.itertuples(index=False, name=None):
            yield t.to_pydatetime(), float(o), float(h), float(l), float(c)

    def run(self, publish: Publish) -> None:
        gap = 1.0 / self.bars_per_s if self.bars_per_s > 0 else 0.0
        for ts, o, h, l, c in self.rows():
            self.stats["rows"] += 1
            if ts >= SEAL_START:
                raise SealedDataError(f"row {ts.isoformat()} is in the sealed period")
            if not _valid_bar(ts, o, h, l, c):
                self.stats["invalid_bars"] += 1
                continue
            publish(bar_message(Bar(self.symbol, ts, o, h, l, c, None),
                                source=self.name, history=False))
            self.stats["published"] += 1
            self.last_bar_ts = ts.isoformat()
            if gap:
                self._sleep(gap)
        self.done = True

    def health(self) -> dict:
        return {"source": self.name, "done": self.done, "last_bar_ts": self.last_bar_ts,
                "coverage": [t.isoformat() for t in self.coverage],
                "window": [self.start.isoformat(), self.end.isoformat()], **self.stats}
