"""Levels for consumers outside the scanner (the agents): a LevelSource
protocol, one implementation over 1-min bars, and a stable key per level.
Pure: no IO, no env, no clock, no network.

The level key. A level's `id` is its position in build_levels' list. The
Scanner keys its state by it, and it changes whenever the levels are rebuilt
over another window (the live scanner rebuilds at every session close). So
consumers get a key instead:

    key = f"{type}@{active_from:%Y-%m-%dT%H:%MZ}"     e.g. "PDH@2025-06-16T00:00Z", "ASIA_H@2025-06-16T07:00Z"

build_levels makes at most one level per type and UTC day: PDH/PDL, PWH/PWL,
DOPEN and WOPEN start at the day's 00:00, a session's H/L at that session's
close. So type and active_from identify a level, whichever window it was
built from, and the key needs neither its value nor its position. Its value
can differ between two builds only while its source period is incomplete in
one of them (the same reason the live scanner rebuilds).
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd

from backend.levels.build import Level, build_levels
from backend.levels.touch import Alert


def level_key(level: Level) -> str:
    """The stable key of a level: its type and the UTC minute it becomes active."""
    start = pd.Timestamp(level["active_from"]).tz_convert("UTC")
    return f"{level['type']}@{start:%Y-%m-%dT%H:%MZ}"


class LevelSource(Protocol):
    """What a consumer may ask about levels."""

    def levels_at(self, t: pd.Timestamp) -> list[Level]:
        """Every level active at t (active_from <= t < active_to), each with its "key"."""
        ...

    def trigger_levels(self, alert: Alert) -> list[Level]:
        """The levels an alert was raised for, each with its "key"."""
        ...


class FrameLevelSource:
    """A LevelSource over 1-min bars (UTC index; open, high, low, close).

    Levels are built once from all the bars given, so only bars known at the
    time of use may be passed (as the live scanner builds from the bars it
    has): a level is visible from its active_from on, never before."""

    def __init__(self, bars: pd.DataFrame) -> None:
        self.levels = [dict(L, key=level_key(L)) for L in build_levels(bars)]

    def levels_at(self, t: pd.Timestamp) -> list[Level]:
        t = pd.Timestamp(t)
        return [L for L in self.levels if L["active_from"] <= t < L["active_to"]]

    def trigger_levels(self, alert: Alert) -> list[Level]:
        """Per type in the alert, the active level of that type nearest the
        alert's level price (at most one level per type is active at a time)."""
        t = pd.Timestamp(alert["alert_ts_utc"], tz="UTC")
        types = alert["level_types"].split(",")
        price = float(alert["level_price"])
        out: list[Level] = []
        for typ in types:
            cands = [L for L in self.levels_at(t) if L["type"] == typ]
            if cands:
                out.append(min(cands, key=lambda L: abs(float(L["value"]) - price)))
        return out


__all__ = ["FrameLevelSource", "LevelSource", "level_key"]
