"""The one IBKR connection (the live bar source on top of it is
datafeed.ingest.xauusd_bars.IbkrXauusdSource).

Crash-only, like the scanner: there is no in-place reconnect. A failed
connect, a failed history load or a dropped connection raises SystemExit(1)
after RECONNECT_BACKOFF_S, and the container's restart policy starts a clean
process (history reload + resubscribe). One code path to get right.

Config (env only, no defaults, so nothing points at a gateway by accident):
    IB_HOST       gateway host
    IB_PORT       gateway API port (the only port variable read)
    IB_CLIENT_ID  API client id; must differ from every other client of the
                  same gateway (taken: 11 and 21 by the scanner in ibkr mode,
                  999 by its entrypoint probe, 996 by the host watchdog probe)

Which gateway (owner decision 2026-10-14): in development and tests only a
local paper gateway (or the replay source). In production with the scanner in
stream mode (SCANNER_BAR_SOURCE=stream) this is the ONLY client of the
production gateway; the scanner in stream mode never connects.
"""

from __future__ import annotations

import logging
import os
import time as _time
from dataclasses import dataclass
from datetime import timezone

from datafeed.ingest.xauusd_bars import Bar
from datafeed.instrument import XAUUSD, Instrument

log = logging.getLogger(__name__)

RECONNECT_BACKOFF_S = 20     # same as the scanner: don't hammer-restart a gateway that is down
HISTORY_DAYS = 10            # same as the scanner's _load_recent_history
CONNECT_TIMEOUT_S = 10


@dataclass(frozen=True)
class IbkrConfig:
    host: str
    port: int
    client_id: int

    @classmethod
    def from_env(cls, env=os.environ) -> "IbkrConfig":
        missing = [k for k in ("IB_HOST", "IB_PORT", "IB_CLIENT_ID") if not env.get(k)]
        if missing:
            raise RuntimeError(f"set {', '.join(missing)} (no defaults: see datafeed/README.md)")
        return cls(env["IB_HOST"], int(env["IB_PORT"]), int(env["IB_CLIENT_ID"]))


def crash(msg: str, sleep=_time.sleep):
    log.error("%s; backing off %ss then exiting for restart", msg, RECONNECT_BACKOFF_S)
    sleep(RECONNECT_BACKOFF_S)
    raise SystemExit(1)


class IbkrConnection:
    """Thin wrapper: one readonly ib_insync connection, the instrument's
    contract (default XAUUSD CMDTY SMART USD), its whatToShow history (default
    MIDPOINT), and a streaming quote subscription.
    `ib` can be injected (tests use a fake; nothing here needs a gateway)."""

    def __init__(self, cfg: IbkrConfig, instrument: Instrument = XAUUSD, ib=None) -> None:
        self.cfg, self.instrument, self.symbol = cfg, instrument, instrument.name
        if ib is None:
            from ib_insync import IB
            ib = IB()
        self.ib = ib
        self.contract = None

    def connect(self) -> None:
        self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id,
                        timeout=CONNECT_TIMEOUT_S, readonly=True)
        from ib_insync import Contract
        c = Contract(**self.instrument.contract_fields())
        self.contract = self.ib.qualifyContracts(c)[0]

    def history(self, days: int = HISTORY_DAYS) -> list[Bar]:
        """1-min bars of the instrument's whatToShow (MIDPOINT for XAUUSD, the
        scanner's _load_recent_history request).
        bar_ts = IBKR's bar date (start of the minute, UTC with formatDate=2).
        n_ticks is None: IBKR history has no tick count."""
        bars = self.ib.reqHistoricalData(self.contract, endDateTime="", durationStr=f"{days} D",
                                         barSizeSetting="1 min", whatToShow=self.instrument.what_to_show,
                                         useRTH=False, formatDate=2)
        out = []
        for b in bars:
            ts = b.date if b.date.tzinfo else b.date.replace(tzinfo=timezone.utc)
            out.append(Bar(self.symbol, ts.astimezone(timezone.utc), float(b.open),
                           float(b.high), float(b.low), float(b.close), None))
        return sorted(out, key=lambda x: x.bar_ts)

    def subscribe(self, on_tickers) -> None:
        self.ib.pendingTickersEvent += on_tickers
        self.ib.reqMktData(self.contract, genericTickList="", snapshot=False)

    def connected(self) -> bool:
        return self.ib.isConnected()

    def sleep(self, s: float) -> None:
        self.ib.sleep(s)          # runs the ib_insync event loop

    def disconnect(self) -> None:
        try:
            self.ib.disconnect()
        except Exception:
            pass
