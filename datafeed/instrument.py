"""The traded instrument: one definition shared by the datafeed (feed,
recorder) and the scanner. Stdlib only.

    INSTRUMENT=XAUUSD (default) | BTCUSD        a preset
    INSTRUMENT_IB_SYMBOL, INSTRUMENT_SEC_TYPE, INSTRUMENT_EXCHANGE,
    INSTRUMENT_CURRENCY, INSTRUMENT_WHAT_TO_SHOW, INSTRUMENT_STREAM,
    INSTRUMENT_CALENDAR                         optional overrides of one field

Unset, everything is XAUUSD exactly as before: the contract XAUUSD CMDTY SMART
USD, MIDPOINT history, stream bars.xauusd.1m and the gold trading calendar.
Anything invalid is a startup error, never a guess.

Calendars:
  xauusd  Sun 18:00 -> Fri 17:00 New York, minus the daily 17:00-18:00 break
          (datafeed.ingest.xauusd_bars._market_open, unchanged)
  24/7    always open (crypto)
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Mapping

CALENDARS = ("xauusd", "24/7")
SEC_TYPES = ("CMDTY", "CRYPTO", "CASH", "STK", "FUT", "CFD", "IND")
# reqHistoricalData whatToShow values that give OHLC prices for 1-min bars
WHAT_TO_SHOW = ("MIDPOINT", "TRADES", "BID", "ASK", "BID_ASK", "AGGTRADES")
STREAM_NAME = re.compile(r"bars\.[a-z0-9]+\.1m")
OVERRIDES = {"ib_symbol": "INSTRUMENT_IB_SYMBOL", "sec_type": "INSTRUMENT_SEC_TYPE",
             "exchange": "INSTRUMENT_EXCHANGE", "currency": "INSTRUMENT_CURRENCY",
             "what_to_show": "INSTRUMENT_WHAT_TO_SHOW", "stream": "INSTRUMENT_STREAM",
             "calendar": "INSTRUMENT_CALENDAR"}


class InstrumentError(ValueError):
    """An unknown preset or an invalid field."""


@dataclass(frozen=True)
class Instrument:
    name: str            # the bars' symbol in messages, the store and the scanner, e.g. "XAUUSD"
    ib_symbol: str       # IBKR's contract symbol, e.g. "XAUUSD", "BTC"
    sec_type: str
    exchange: str
    currency: str
    what_to_show: str    # reqHistoricalData (warm-up history)
    stream: str          # bus stream, bars.<lowercase name>.1m
    calendar: str        # a key of CALENDARS

    def validate(self) -> "Instrument":
        if not re.fullmatch(r"[A-Z0-9]{2,12}", self.name):
            raise InstrumentError(f"name {self.name!r}: 2-12 upper-case letters or digits")
        if not re.fullmatch(r"[A-Z0-9.]{1,12}", self.ib_symbol):
            raise InstrumentError(f"ib_symbol {self.ib_symbol!r}")
        if self.sec_type not in SEC_TYPES:
            raise InstrumentError(f"sec_type {self.sec_type!r}: one of {', '.join(SEC_TYPES)}")
        if not re.fullmatch(r"[A-Z]{2,12}", self.exchange):
            raise InstrumentError(f"exchange {self.exchange!r}")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise InstrumentError(f"currency {self.currency!r}")
        if self.what_to_show not in WHAT_TO_SHOW:
            raise InstrumentError(f"what_to_show {self.what_to_show!r}: one of {', '.join(WHAT_TO_SHOW)}")
        if not STREAM_NAME.fullmatch(self.stream):
            raise InstrumentError(f"stream {self.stream!r}: must look like bars.<name>.1m")
        if self.calendar not in CALENDARS:
            raise InstrumentError(f"calendar {self.calendar!r}: one of {', '.join(CALENDARS)}")
        return self

    def contract_fields(self) -> dict[str, str]:
        """Keyword arguments for ib_insync.Contract."""
        return {"symbol": self.ib_symbol, "secType": self.sec_type, "exchange": self.exchange,
                "currency": self.currency}


XAUUSD = Instrument("XAUUSD", "XAUUSD", "CMDTY", "SMART", "USD", "MIDPOINT", "bars.xauusd.1m", "xauusd")
# BTC on PAXOS: whatToShow and the tick fields to be confirmed by
# tools/ibkr_instrument_check.py on the paper account (docs/DRYRUN.md step 1).
BTCUSD = Instrument("BTCUSD", "BTC", "CRYPTO", "PAXOS", "USD", "MIDPOINT", "bars.btcusd.1m", "24/7")
PRESETS = {i.name: i for i in (XAUUSD, BTCUSD)}


def from_env(env: Mapping[str, str] = os.environ) -> Instrument:
    """The configured instrument: the INSTRUMENT preset (default XAUUSD) with
    any INSTRUMENT_* overrides. Raises InstrumentError."""
    name = env.get("INSTRUMENT", "XAUUSD") or "XAUUSD"
    if name not in PRESETS:
        raise InstrumentError(f"INSTRUMENT={name!r}: one of {', '.join(PRESETS)}")
    over = {f: env[k] for f, k in OVERRIDES.items() if env.get(k)}
    unknown = sorted(k for k in env if k.startswith("INSTRUMENT_") and k not in OVERRIDES.values())
    if unknown:
        raise InstrumentError(f"unknown setting(s) {', '.join(unknown)}")
    return replace(PRESETS[name], **over).validate()


def describe(i: Instrument) -> str:
    return " ".join(f"{f.name}={getattr(i, f.name)}" for f in fields(i))


def market_open(ts: datetime, calendar: str) -> bool:
    """Is `ts` (tz-aware) inside the calendar's trading hours?"""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise TypeError("market_open needs a tz-aware timestamp")
    if calendar == "24/7":
        return True
    if calendar == "xauusd":
        from datafeed.ingest.xauusd_bars import _market_open
        return _market_open(ts)
    raise InstrumentError(f"calendar {calendar!r}")
