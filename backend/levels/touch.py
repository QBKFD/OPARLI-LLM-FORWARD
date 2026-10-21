"""Touch detection: arming, first-touch-only, the touch tolerance and the
15-min collapse into alerts. Pure: no IO, no env, no clock, no network.

Calibrated on 2023-2025 (notebooks/backtest.ipynb); do not retune.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import pandas as pd

from backend.levels.build import Level, session_of

Alert = dict[str, Any]           # alert_id, alert_ts_utc, level_types, level_price, price_at_alert, session, confluence_count

TOL = 1.00                       # touch tolerance ($)
WINDOW_MIN = 15                  # collapse alerts within the same 15-min bucket


# ── Streaming scanner (identical code for replay and live) ─────────────────
class Scanner:
    """Feeds one bar at a time. First-touch-only per level instance: once a
    level is touched it is dead forever (no cooldown re-arm — that floods to
    ~15/day; first-touch-only holds ~4.5/day). Raw touches landing in the same
    15-min bucket and at the same price (within TOL) collapse into one alert
    carrying a confluence_count and every contributing level type."""

    def __init__(self, levels: list[Level], tol: float = TOL, window_min: int = WINDOW_MIN) -> None:
        self.tol = tol
        self.window = pd.Timedelta(minutes=window_min)
        self.by_day: defaultdict[pd.Timestamp, list[Level]] = defaultdict(list)
        for L in levels:
            d, d1 = L["active_from"].normalize(), L["active_to"].normalize()
            while d <= d1:
                self.by_day[d].append(L)
                d += pd.Timedelta(days=1)
        # per-level runtime state: 'open' levels start UNARMED (price sits at the
        # open at creation); static levels are armed from birth.
        self.state: dict[int, dict[str, bool]] = {L["id"]: {"armed": L["arm"] != "open", "alerted": False} for L in levels}
        self._cur_day: pd.Timestamp | None = None
        self._active: list[Level] = []
        self._bucket_start: pd.Timestamp | None = None
        self._bucket_touches: list[dict[str, Any]] = []
        # per-day counter, so an alert's id depends only on that day's alerts, not
        # on how far back the history (and a live warm-up) starts
        self._seq: defaultdict[str, int] = defaultdict(int)

    def on_bar(self, ts: pd.Timestamp, o: float, h: float, l: float, c: float) -> list[Alert]:
        finalized: list[Alert] = []
        # flush the previous 15-min bucket once we cross into a new one
        if self._bucket_start is not None and ts - self._bucket_start >= self.window:
            finalized += self._flush()
        if self._bucket_start is None or ts - self._bucket_start >= self.window:
            self._bucket_start = ts.floor(f"{int(self.window.total_seconds() // 60)}min")

        d = ts.normalize()
        if d != self._cur_day:
            self._cur_day = d
            self._active = self.by_day.get(d, [])

        for L in self._active:
            if not (L["active_from"] <= ts < L["active_to"]):
                continue
            s = self.state[L["id"]]
            if s["alerted"]:
                continue
            v = L["value"]
            in_zone = (l - self.tol) <= v <= (h + self.tol)
            left = (h < v - 2 * self.tol) or (l > v + 2 * self.tol)
            if not s["armed"]:
                if left:
                    s["armed"] = True
                continue
            if in_zone:
                self._bucket_touches.append(dict(ts=ts, type=L["type"], value=v, price=c))
                s["alerted"] = True     # first-touch-only: dead forever
        return finalized

    def close(self) -> list[Alert]:
        return self._flush()

    def _flush(self) -> list[Alert]:
        touches, self._bucket_touches = self._bucket_touches, []
        if not touches:
            return []
        touches.sort(key=lambda t: t["value"])
        clusters: list[list[dict[str, Any]]] = []
        cur = [touches[0]]
        for t in touches[1:]:
            if abs(t["value"] - cur[-1]["value"]) <= self.tol:
                cur.append(t)
            else:
                clusters.append(cur)
                cur = [t]
        clusters.append(cur)
        alerts: list[Alert] = []
        for cl in clusters:
            ts0 = min(t["ts"] for t in cl)
            day = ts0.strftime("%Y%m%d")
            self._seq[day] += 1
            alerts.append(dict(
                alert_id=f"A{day}-{self._seq[day]:03d}",
                alert_ts_utc=ts0.strftime("%Y-%m-%d %H:%M:%S"),
                level_types=",".join(sorted({t["type"] for t in cl})),
                level_price=round(sum(t["value"] for t in cl) / len(cl), 3),
                price_at_alert=round(cl[0]["price"], 3),
                session=session_of(ts0),
                confluence_count=len({t["type"] for t in cl}),
            ))
        return alerts


def touches(bars: pd.DataFrame, levels: list[Level], *, tol: float = TOL,
            window_min: int = WINDOW_MIN) -> list[Alert]:
    """Every alert one Scanner emits over `bars` (UTC index; open, high, low,
    close), including the last bucket's: what the --date replay, the selftest
    and a continuous replay do bar by bar."""
    sc = Scanner(levels, tol=tol, window_min=window_min)
    out: list[Alert] = []
    for ts, o, h, l, c in bars[["open", "high", "low", "close"]].itertuples(name=None):
        out += sc.on_bar(ts, o, h, l, c)
    return out + sc.close()

