"""Level building: the level instances, each active only from the close of
its source period (no look-ahead by construction). Pure: no IO, no env, no
clock, no network.

Calibrated on 2023-2025 (notebooks/backtest.ipynb); do not retune.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

Level = dict[str, Any]           # type, value, active_from, active_to, arm, created_day, id

SESSIONS = {"ASIA": (0, 7), "LONDON": (7, 12), "NY": (12, 21)}  # UTC hours [start,end)
MIN_BARS_FULL_DAY = 300          # a full trading day needs >= this many bars
SESSION_CARRY_H = 24             # session H/L stay active +24h after close


# ── Level engine (from notebooks/backtest.ipynb, active_from construction) ──
def session_of(ts: pd.Timestamp) -> str:
    for s, (a, b) in SESSIONS.items():
        if a <= ts.hour < b:
            return s
    return "ROLLOVER"


def build_levels(df: pd.DataFrame) -> list[Level]:
    """Precompute daily/weekly/session aggregates once, emit level instances.

    Each instance carries active_from (source-period close) and active_to, so it
    is invisible outside that window — the no-look-ahead property. Types map to
    the 12 the spec asks for: PDH PDL PWH PWL DOPEN WOPEN {ASIA,LONDON,NY}_{H,L}.
    """
    date = df.index.normalize()
    daily = df.groupby(date).agg(high=("high", "max"), low=("low", "min"),
                                 open=("open", "first"), n=("open", "size"))
    full_days = daily.index[daily["n"] >= MIN_BARS_FULL_DAY]
    hour = df.index.hour
    sess_hl = {
        s: df[(hour >= h0) & (hour < h1)]
           .groupby(df[(hour >= h0) & (hour < h1)].index.normalize())
           .agg(h=("high", "max"), l=("low", "min"))
        for s, (h0, h1) in SESSIONS.items()
    }
    ic = df.index.isocalendar()
    wkkey = pd.Series(ic["year"].astype(str) + "-" + ic["week"].astype(str).str.zfill(2), index=df.index)
    wk = df.groupby(wkkey).agg(high=("high", "max"), low=("low", "min"), open=("open", "first"))

    def wkkey_of(day: pd.Timestamp) -> str:
        c = (day - pd.Timedelta(days=day.weekday())).isocalendar()
        return f"{c[0]}-{c[1]:02d}"

    levels: list[Level] = []
    for day in daily.index:
        de = day + pd.Timedelta(days=1)
        today: list[Level] = []
        i = full_days.searchsorted(day)                      # prior FULL trading day
        if i > 0:
            p = full_days[i - 1]
            today += [dict(type="PDH", value=daily.at[p, "high"], active_from=day, active_to=de, arm="static"),
                      dict(type="PDL", value=daily.at[p, "low"],  active_from=day, active_to=de, arm="static")]
        pwk = wkkey_of(day - pd.Timedelta(days=7))
        if pwk in wk.index:
            today += [dict(type="PWH", value=wk.at[pwk, "high"], active_from=day, active_to=de, arm="static"),
                      dict(type="PWL", value=wk.at[pwk, "low"],  active_from=day, active_to=de, arm="static")]
        today.append(dict(type="DOPEN", value=daily.at[day, "open"], active_from=day, active_to=de, arm="open"))
        cwk = wkkey_of(day)
        if cwk in wk.index:
            today.append(dict(type="WOPEN", value=wk.at[cwk, "open"], active_from=day, active_to=de, arm="open"))
        for s, (h0, h1) in SESSIONS.items():
            g = sess_hl[s]
            if day in g.index:
                cl = day + pd.Timedelta(hours=h1)            # active_from = session CLOSE
                ex = cl + pd.Timedelta(hours=SESSION_CARRY_H)
                today += [dict(type=f"{s}_H", value=g.at[day, "h"], active_from=cl, active_to=ex, arm="static"),
                          dict(type=f"{s}_L", value=g.at[day, "l"], active_from=cl, active_to=ex, arm="static")]
        for L in today:
            L["created_day"] = day
        levels += today
    for i, L in enumerate(levels):
        L["id"] = i
    return levels
