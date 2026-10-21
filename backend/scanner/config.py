"""Paths and the bar-source switch: the scanner's configuration from the
environment. The live loops read their own connection settings (IB_*,
REDIS_URL, DATAFEED_DB, SCANNER_STREAM_*, SCANNER_HEARTBEAT) where they use
them (docs/decisions/refactor-backend-layout.md, BL13)."""
from __future__ import annotations

import os
from pathlib import Path

# the repo root: backend/scanner/config.py -> parents[2]; database/ is local data (database/README.md)
_ROOT = Path(__file__).resolve().parents[2]
PARQUET = _ROOT / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"
# SCANNER_DB lets the container point the append-only journal at a mounted
# volume so decisions survive restarts / redeploys.
DB_PATH = Path(os.environ.get(
    "SCANNER_DB", _ROOT / "database/scanner_decisions.sqlite"))


RECONNECT_BACKOFF_S = 20         # wait before exiting on a dropped gateway, so the
                                 # container doesn't hammer-restart while the gateway
                                 # is down for minutes (weekly 2FA window)


BAR_SOURCES = ("ibkr", "stream")


def bar_source(env=os.environ) -> str:
    """SCANNER_BAR_SOURCE, default ibkr. Anything else is a startup error, never a guess."""
    s = env.get("SCANNER_BAR_SOURCE", "ibkr")
    if s not in BAR_SOURCES:
        raise SystemExit(f"SCANNER_BAR_SOURCE={s!r}: must be one of {', '.join(BAR_SOURCES)}")
    return s
