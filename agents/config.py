"""Configuration for the agents graph (pydantic, frozen, unknown keys refused).

AgentsConfig.model_validate(dict) or AgentsConfig.from_toml(path). Durations
are seconds or ISO-8601 strings ("PT5M")."""

from __future__ import annotations

import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.hashing import canonical_json, sha256_hex
from agents.prereg import EXPERIMENT_ID
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
    # newest 1m bar may be at most this far behind the last market-open minute
    # before as_of (closed minutes don't count)
    max_staleness: timedelta = timedelta(minutes=5)
    # agents/experiments/<experiment_id>/PREREG.md. Required in live mode;
    # optional in replay. Stored on every snapshot and decision.
    experiment_id: str | None = None
    # The HAR σ model for the snapshot features (a name in
    # datafeed.features.vol_block.SIGMA_MODELS, SHA-256 checked on load). None:
    # no features, marked unavailable with the reason. It enters the epoch.
    sigma_model: str | None = None

    @model_validator(mode="after")
    def _check(self):
        if self.experiment_id is not None and not EXPERIMENT_ID.fullmatch(self.experiment_id):
            raise ValueError(f"bad experiment_id {self.experiment_id!r}")
        if "1m" not in self.keep or not set(self.keep) <= set(TIMEFRAMES):
            raise ValueError(f"keep must include '1m' and use only {list(TIMEFRAMES)}")
        if any(n < 1 for n in self.keep.values()):
            raise ValueError("keep counts must be >= 1")
        if self.fetch_lookback <= timedelta(0) or self.max_staleness < timedelta(0):
            raise ValueError("fetch_lookback must be > 0 and max_staleness >= 0")
        return self


class GateConfig(_Cfg):
    require_market_open: bool = True
    # v0: at least one bar per timeframe. Real minimums come with real agents.
    min_bars: dict[str, int] = Field(default_factory=lambda: {tf: 1 for tf in TIMEFRAMES})

    @model_validator(mode="after")
    def _check(self):
        if not set(self.min_bars) <= set(TIMEFRAMES) or any(n < 0 for n in self.min_bars.values()):
            raise ValueError(f"min_bars uses only {list(TIMEFRAMES)}, counts >= 0")
        return self


class AgentsConfig(_Cfg):
    snapshot: SnapshotConfig             # no default: snapshot.mode must be chosen
    gate: GateConfig = Field(default_factory=GateConfig)
    # per agent call, in round 1 and in every discussion round; a timeout is an
    # abstain (round 1) or the previous vote carried forward (discussion).
    # The discussion itself (rounds, early stop) is the owner's: agents.toml.
    node_timeout_s: float = Field(120.0, gt=0)

    def to_json(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    def config_hash(self) -> str:
        return sha256_hex(self.model_dump(mode="json"))

    @classmethod
    def from_toml(cls, path: str | Path) -> "AgentsConfig":
        with open(path, "rb") as f:
            return cls.model_validate(tomllib.load(f))
