"""Digests for the committed golden test (tests/scanner/test_golden.py).

Each function takes the scanner module as `ds`, so the pinned values could be
computed on main before the restructure (backend/deterministic_scanner.py)
and checked against backend.scanner after it. A digest is the SHA-256 of the
canonical JSON of the alerts (every field) and counterfactuals."""
import hashlib
import json

import pandas as pd

DAYS = ["2023-03-12", "2023-03-13", "2023-06-15", "2023-11-05", "2023-11-06", "2023-11-23", "2023-12-25",
        "2024-03-08", "2024-03-10", "2024-03-11", "2024-03-29", "2024-03-31", "2024-07-04", "2024-11-03",
        "2024-11-04", "2024-12-24", "2024-12-25", "2025-01-01", "2025-03-09", "2025-03-10", "2025-06-15",
        "2025-06-16", "2025-07-03", "2025-07-04", "2025-10-14", "2025-10-26", "2025-11-02", "2025-11-03"]


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def continuous(ds, df: pd.DataFrame, start, end) -> list:
    """One Scanner over [start, end): what a continuous live run emits."""
    win = df[(df.index >= start) & (df.index < end)]
    sc, out = ds.Scanner(ds.build_levels(win)), []
    for r in win.itertuples(name=None):
        out += sc.on_bar(*r)
    return out + sc.close()


def live_started(ds, df: pd.DataFrame, start, t0, end) -> list:
    """LiveLevels warmed on [start, t0), then fed [t0, end)."""
    win = df[(df.index >= start) & (df.index < end)]
    live, out = ds.LiveLevels(win[win.index < t0].copy(), t0), []
    for r in win[win.index >= t0].itertuples(name=None):
        out += live.on_bar(*r)
    return out


def day_digest(ds, df: pd.DataFrame, day: str) -> str:
    """The day's alerts from a continuous run (warm-up as the stream scanner),
    from LiveLevels started at 07:13 that day, and the --date replay's rows."""
    d = pd.Timestamp(day, tz="UTC")
    start, end = ds.warmup_start(d), d + pd.Timedelta(days=1)
    cont = [a for a in continuous(ds, df, start, end) if a["alert_ts_utc"][:10] == day]
    live = [a for a in live_started(ds, df, start, d + pd.Timedelta(hours=7, minutes=13), end)
            if a["alert_ts_utc"][:10] == day]
    replay = ds.replay_day(day).to_dict("records")
    return sha({"continuous": cont, "live_0713": live, "replay": replay})


def synthetic_digest(ds, bars: pd.DataFrame) -> str:
    """Continuous run, LiveLevels started mid-week, and counterfactuals of every
    alert, on synthetic bars (no data files)."""
    start, end = bars.index[0], bars.index[-1] + pd.Timedelta(minutes=1)
    cont = continuous(ds, bars, start, end)
    live = live_started(ds, bars, start, start + pd.Timedelta(days=9, hours=10, minutes=37), end)
    cfs = [ds.counterfactual(bars, a["alert_ts_utc"], a["price_at_alert"]) for a in cont]
    return sha({"continuous": cont, "live": live, "counterfactuals": cfs})
