# backend/services/clock.py
"""
Clock Abstraction

Lets time be injected the same way market data is (see market_data_provider.py).
Agents that need "what time is it" depend on a Clock instead of calling
datetime.now() directly — so the identical agent code runs in:

  - LIVE:     LiveClock()          → real wall-clock (UTC)
  - BACKTEST: SimulatedClock(t)    → the replay timestamp, advanced by the loop

Without this, any agent that reads datetime.now() (e.g. Trade Manager's
time-in-trade and 10-minute pattern checks) cannot be replayed over history.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta

import pytz


class Clock(ABC):
    """Interface: the current time, tz-aware (UTC)."""

    @abstractmethod
    def now(self) -> datetime:
        ...


class LiveClock(Clock):
    """Real wall-clock time. Used in production."""

    def __init__(self, tz=pytz.utc):
        self.tz = tz

    def now(self) -> datetime:
        return datetime.now(self.tz)


class SimulatedClock(Clock):
    """
    Controllable clock for backtests. The replay loop sets/advances it so every
    agent sees the same 'now' as the bar currently being processed.
    """

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            start = pytz.utc.localize(start)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        self._now = dt

    def advance(self, **timedelta_kwargs) -> None:
        self._now += timedelta(**timedelta_kwargs)
