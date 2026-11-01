"""Macro calendar, read as of t.

The clean calendar (`tools/data/macro_calendar.py`, PR #60) as a point-in-time
source, following store/query.py's pattern: where the data has its own event
time, a value is visible at t only if event_ts < t.

THE "AS OF t" RULE
    The schedule is public in advance, so every timed event is listed at any t:
    its time, currency, name, impact, forecast and previous. Its **actual**
    (and anything derived from it: the parsed number and the surprise) is
    visible only once the release has happened, ts_utc < t. At t == ts_utc
    the release is still hidden: nothing could have read it at that instant.

    forecast and previous are as scraped, not point-in-time: a forecast that
    changed in the days before the release, or a later revision of
    "previous", isn't modelled.

UNTIMED ROWS
    Rows without a usable time ("All Day", "Tentative", "Oct Data", the two
    source-time conflicts, ...; ts_utc null with a time_flag) are excluded,
    and counted per flag in Calendar.untimed. Nothing guesses a time for them.

The core is stdlib-only. Only load_calendar() needs pandas (the `replay` extra),
and it imports pandas lazily.
"""

from __future__ import annotations

import hashlib
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping

from backend.datafeed.ingest.base import parse_utc

HIDDEN_FIELDS = ("actual", "actual_num", "surprise")


@dataclass(frozen=True)
class CalendarEvent:
    """One timed calendar row. actual, actual_num and surprise are None when hidden."""
    ts_utc: datetime
    currency: str
    event: str
    impact: str
    forecast: str | None = None
    previous: str | None = None
    forecast_num: float | None = None
    previous_num: float | None = None
    actual: str | None = None
    actual_num: float | None = None
    surprise: float | None = None
    released: bool = True          # False in a view taken before ts_utc: the hidden fields are None


@dataclass(frozen=True)
class Calendar:
    events: tuple[CalendarEvent, ...]       # timed rows only, sorted by ts_utc
    untimed: Mapping[str, int]              # excluded rows per time_flag
    sha256: str | None = None

    @property
    def n_untimed(self) -> int:
        return sum(self.untimed.values())


def _utc(t) -> datetime:
    if isinstance(t, str):
        return parse_utc(t)
    if not isinstance(t, datetime):                 # pandas.Timestamp is a datetime
        raise TypeError(f"t must be a datetime or an ISO string, got {type(t).__name__}")
    if t.tzinfo is None:
        raise ValueError(f"naive timestamp {t!r}")
    return t.astimezone(timezone.utc)


def _clean(x):
    """None for missing values (None, NaN, NaT, pandas NA)."""
    if x is None:
        return None
    try:
        if x != x:                                   # NaN, NaT
            return None
    except TypeError:                                # pandas NA
        return None
    return x


def _num(x) -> float | None:
    x = _clean(x)
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else x


def _str(x) -> str | None:
    x = _clean(x)
    return None if x is None else str(x)


def from_rows(rows: Iterable[Mapping], sha256: str | None = None) -> Calendar:
    """Rows of the clean table (dicts) -> Calendar. A row without ts_utc is untimed:
    excluded and counted under its time_flag ("unflagged" if it has none)."""
    timed, untimed = [], Counter()
    for r in rows:
        ts = _clean(r.get("ts_utc"))
        if ts is None:
            untimed[_str(r.get("time_flag")) or "unflagged"] += 1
            continue
        timed.append(CalendarEvent(
            ts_utc=_utc(ts), currency=str(r["currency"]), event=str(r["event"]), impact=str(r["impact"]),
            forecast=_str(r.get("forecast")), previous=_str(r.get("previous")),
            forecast_num=_num(r.get("forecast_num")), previous_num=_num(r.get("previous_num")),
            actual=_str(r.get("actual")), actual_num=_num(r.get("actual_num")), surprise=_num(r.get("surprise"))))
    timed.sort(key=lambda e: (e.ts_utc, e.currency, e.event))
    return Calendar(events=tuple(timed), untimed=dict(sorted(untimed.items())), sha256=sha256)


LOAD_COLUMNS = ("ts_utc", "time_flag", "currency", "event", "impact", "actual", "forecast", "previous",
                "actual_num", "forecast_num", "previous_num", "surprise")


def load_calendar(path, expected_sha256: str | None = None) -> Calendar:
    """Read the clean calendar parquet (database/historical_news/macro_calendar_utc.parquet).
    expected_sha256: refuse a file with a different SHA-256. Needs pandas."""
    import pandas as pd

    path = Path(path)
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    if expected_sha256 is not None and h != expected_sha256:
        raise ValueError(f"{path.name}: SHA-256 {h} != expected {expected_sha256}")
    df = pd.read_parquet(path, columns=list(LOAD_COLUMNS))
    df["ts_utc"] = df["ts_utc"].astype(object).where(df["ts_utc"].notna(), None)
    return from_rows(df.to_dict("records"), sha256=h)


def _as_of(e: CalendarEvent, t: datetime) -> CalendarEvent:
    if e.ts_utc < t:
        return e
    return replace(e, released=False, **{f: None for f in HIDDEN_FIELDS})


def _events(source) -> tuple[CalendarEvent, ...]:
    return source.events if isinstance(source, Calendar) else tuple(source)


def events_as_of(t, calendar, start=None, end=None,
                 where: Callable[[CalendarEvent], bool] | None = None) -> list[CalendarEvent]:
    """Every timed event with start <= ts_utc < end (each bound optional), as known at t:
    actual, actual_num and surprise only if ts_utc < t, else None with released=False.
    calendar: a Calendar or an iterable of CalendarEvent (e.g. an earlier view: a field
    hidden there stays hidden). where: an optional filter (e.g. currency, impact)."""
    t = _utc(t)
    evs = _events(calendar)
    if start is not None or end is not None:
        evs = tuple(sorted(evs, key=lambda e: e.ts_utc))
        keys = [e.ts_utc for e in evs]
        lo = 0 if start is None else bisect_left(keys, _utc(start))
        hi = len(evs) if end is None else bisect_left(keys, _utc(end))
        evs = evs[lo:hi]
    return [_as_of(e, t) for e in evs if where is None or where(e)]


def news_window(t, before: timedelta, after: timedelta, events,
                where: Callable[[CalendarEvent], bool] | None = None) -> list[CalendarEvent]:
    """Events with t - before <= ts_utc <= t + after (both ends included), as known at t,
    oldest first. events: a Calendar or an iterable of CalendarEvent."""
    if before < timedelta(0) or after < timedelta(0):
        raise ValueError("before and after must be >= 0")
    t = _utc(t)
    evs = tuple(sorted(_events(events), key=lambda e: e.ts_utc))
    keys = [e.ts_utc for e in evs]
    lo, hi = bisect_left(keys, t - before), bisect_right(keys, t + after)
    return [_as_of(e, t) for e in evs[lo:hi] if where is None or where(e)]
