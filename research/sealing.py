"""Global data seal for all research code (see research/SEALING.md)."""

import numpy as np
import pandas as pd

GLOBAL_SEAL = pd.Timestamp("2026-10-01", tz="UTC")


def assert_before_global_seal(ts, what: str = "data") -> None:
    """Fail if any timestamp is at or after 2026-10-01 (UTC).

    Accepts a DatetimeIndex (tz-aware or naive UTC), or an array of int64 ns.
    """
    if isinstance(ts, pd.DatetimeIndex):
        vals = ts.tz_localize("UTC").asi8 if ts.tz is None else ts.tz_convert("UTC").asi8
    else:
        vals = np.asarray(ts, dtype=np.int64)
    if len(vals) and vals.max() >= GLOBAL_SEAL.value:
        raise AssertionError(f"SEALED DATA: {what} contains timestamps >= {GLOBAL_SEAL.date()} "
                             "(reserved for the final strategy test)")


# --------------------------------------------------------------------------- per-instrument periods
# SEALING.md "Instrument periods" (owner, 2026-10-28). Every instrument has the
# same three periods: dev, holdout (read once, by a study whose PREREG names it)
# and sealed (GLOBAL_SEAL onward, never read).

INSTRUMENTS = ("XAUUSD", "XAGUSD", "EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF", "DXY_PROXY")
PERIODS = {
    "dev": (pd.Timestamp("2016-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "holdout": (pd.Timestamp("2024-01-01", tz="UTC"), GLOBAL_SEAL),
}
SEAL_REGISTRY = {sym: dict(PERIODS) for sym in INSTRUMENTS}


def assert_period(symbol: str, ts, period: str, what: str = "data") -> None:
    """Fail unless every timestamp lies in `symbol`'s `period` ([start, end), UTC)
    and before the global seal. An unknown symbol or period fails too."""
    if symbol not in SEAL_REGISTRY:
        raise AssertionError(f"{symbol!r} is not in the seal registry (research/SEALING.md)")
    if period not in SEAL_REGISTRY[symbol]:
        raise AssertionError(f"period {period!r} is not one of {sorted(SEAL_REGISTRY[symbol])}")
    idx = pd.DatetimeIndex(ts)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    assert_before_global_seal(idx, what)
    lo, hi = SEAL_REGISTRY[symbol][period]
    if len(idx) and ((idx < lo).any() or (idx >= hi).any()):
        raise AssertionError(f"{what}: {symbol} rows outside its {period} period [{lo.date()}, {hi.date()})")
