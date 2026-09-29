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
