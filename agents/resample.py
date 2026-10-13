"""1m bars -> 15m / 1h / 4h bars. Stdlib only.

Buckets are aligned to the UTC epoch (4h bars open at 00, 04, ..., 20 UTC).
OHLC per bucket: first open, max high, min low, last close. There's no forward
fill and no empty bucket. n_1m counts the 1m bars inside, so gaps stay visible.
A bucket is `complete` when its end is <= as_of. Only the newest bucket can be
partial, and it's built from bars < as_of only, like every other bucket.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from agents.schemas import EPOCH, Bar, tf_delta


def bucket_start(ts: datetime, tf: str) -> datetime:
    return ts - (ts - EPOCH) % tf_delta(tf)


def bar_from_row(row: dict) -> Bar:
    """A datafeed bars_before row -> 1m Bar."""
    return Bar(ts=datetime.fromisoformat(row["bar_ts"]), open=row["open"], high=row["high"],
               low=row["low"], close=row["close"], kind=row["kind"])


def resample(bars_1m: Sequence[Bar], tf: str, as_of: datetime) -> tuple[Bar, ...]:
    """bars_1m: strictly increasing 1m bars, all < as_of."""
    if tf == "1m":
        return tuple(bars_1m)
    step = tf_delta(tf)
    out: list[Bar] = []
    group: list[Bar] = []

    def flush():
        if group:
            start = bucket_start(group[0].ts, tf)
            out.append(Bar(ts=start, open=group[0].open, high=max(b.high for b in group),
                           low=min(b.low for b in group), close=group[-1].close, n_1m=len(group),
                           complete=start + step <= as_of))

    for b in bars_1m:
        if b.ts >= as_of:
            raise ValueError(f"1m bar {b.ts} is not before as_of {as_of}")
        if group and bucket_start(b.ts, tf) != bucket_start(group[0].ts, tf):
            flush()
            group = []
        group.append(b)
    flush()
    return tuple(out)
