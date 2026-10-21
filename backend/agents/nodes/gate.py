"""Gate (v0): go / no-go after the snapshot is saved, before any agent runs.

No-go when the market is closed at as_of (datafeed's _market_open, the
scanner's rule) or when a timeframe has fewer bars than its minimum. It's also
the slot for later trigger-specific filters.
"""

from __future__ import annotations

from backend.datafeed.ingest.xauusd_bars import _market_open

from backend.agents.config import GateConfig
from backend.agents.schemas import Snapshot


def check(snapshot: Snapshot, cfg: GateConfig) -> str | None:
    """None = go; otherwise the reason for no-go."""
    if cfg.require_market_open and not _market_open(snapshot.as_of):
        return f"market closed at as_of {snapshot.as_of.isoformat()}"
    for tf, n in cfg.min_bars.items():
        have = len(snapshot.bars.get(tf, ()))
        if have < n:
            return f"{tf}: {have} bars, minimum {n}"
    return None
