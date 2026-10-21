"""Timestamps and XAUUSD market hours, pure helpers shared by the live paths,
the journal and the selftest."""
from __future__ import annotations

import pandas as pd

def _utc(ts) -> pd.Timestamp:
    """Parse a stored/naive timestamp as UTC (stored alert timestamps are naive)."""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def _market_open(now) -> bool:
    """Approximate XAUUSD (spot gold) trading window, used only to suppress false
    'no data' heartbeat warnings. Sun 18:00 -> Fri 17:00 New York time, minus the
    daily 17:00-18:00 break. Evaluated in New York time because the break moves in
    UTC with US DST (21-22 UTC in summer, 22-23 UTC in winter). Holidays aren't
    modelled — a rare spurious warning is acceptable for a safety-net notification."""
    et = now.tz_convert("America/New_York")
    wd, h = et.weekday(), et.hour       # Mon=0 .. Sun=6
    if wd == 5:                          # Saturday
        return False
    if wd == 6:                          # Sunday: reopens 18:00 ET
        return h >= 18
    if wd == 4 and h >= 17:              # Friday close
        return False
    if h == 17:                          # daily maintenance break
        return False
    return True


def _valid_bar(ts, o, h, l, c) -> bool:
    """Only real prices inside trading hours may reach the level engine. IBKR sends
    -1 for "no quote" (e.g. while a data farm reconnects) and pushes frozen quotes
    while the market is closed; either becomes a fake touch or a fake session.
    The backtest data has neither: all its bars fall inside _market_open() and
    every price is positive."""
    return _market_open(ts) and all(x == x and x > 0 for x in (o, h, l, c)) and l <= h


def _open_minutes(start, end) -> pd.DatetimeIndex:
    """Complete market-open minutes between start and end: minute opens m with
    start <= m, m + 1 min <= end and _market_open(m). A vectorised copy of
    _market_open (New York weekday and hour); a test pins the two together."""
    a, b = _utc(start).ceil("min"), _utc(end).floor("min")
    if b <= a:                   # date_range(a, a, inclusive="left") would still return [a]
        return pd.DatetimeIndex([], tz="UTC")
    idx = pd.date_range(a, b, freq="1min", inclusive="left")
    et = idx.tz_convert("America/New_York")
    wd, h = et.weekday, et.hour
    closed = (wd == 5) | ((wd == 6) & (h < 18)) | ((wd == 4) & (h >= 17)) | (h == 17)
    return idx[~closed]
