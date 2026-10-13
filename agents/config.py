"""Configuration for the agents graph (pydantic, frozen, unknown keys refused)."""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.schemas import TIMEFRAMES


class _Cfg(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SnapshotConfig(_Cfg):
    symbol: str = "XAUUSD"
    # live: kinds ("live", "history") with known_by = t.
    # replay: kinds ("replay",), known_by = None. No default on purpose.
    mode: Literal["live", "replay"]
    # 1m history fetched before resampling. The window start is aligned down to
    # the largest kept timeframe, so the oldest bucket is never cut short.
    fetch_lookback: timedelta = timedelta(days=30)
    # bars kept per timeframe (the most recent ones)
    keep: dict[str, int] = Field(default_factory=lambda: {"1m": 240, "15m": 192, "1h": 120, "4h": 120})
    # newest 1m bar must have completed at most this long before as_of
    max_staleness: timedelta = timedelta(minutes=5)

    @model_validator(mode="after")
    def _check(self):
        if "1m" not in self.keep or not set(self.keep) <= set(TIMEFRAMES):
            raise ValueError(f"keep must include '1m' and use only {list(TIMEFRAMES)}")
        if any(n < 1 for n in self.keep.values()):
            raise ValueError("keep counts must be >= 1")
        if self.fetch_lookback <= timedelta(0) or self.max_staleness < timedelta(0):
            raise ValueError("fetch_lookback must be > 0 and max_staleness >= 0")
        return self
