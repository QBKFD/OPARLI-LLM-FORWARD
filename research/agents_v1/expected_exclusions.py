"""Expected close-crossing exclusions for the agents v1 PREREG (DRAFT), §2 and §8.

Development data only: XAUUSD.txt 2007-01-01 .. 2020-12-31 (UTC, per
research/vol_features/DATA_AUDIT_LONG.md). Nothing from 2021 on is read, so the
2021-2023 holdout, the spent 2024-01..2026-01 and the seal stay untouched.

What it does:
- Replays the production scanner's level engine (`build_levels` + `Scanner`,
  loaded from backend/deterministic_scanner.py, not edited) over the bars the
  live scanner would keep (`_valid_bar`). Live is a continuous replay
  (`LiveLevels`), so the alerts are the ones the scanner would have sent.
- Models when each alert could be traded: the scanner emits an alert when the
  first bar of a later 15-min bucket arrives; that bar is final about 1 min
  after it opens, and the trade enters at the open of the next bar. So
  entry = the first market-open minute at or after flush bar + ENTRY_DELAY.
- Flags an alert as excluded at horizon H when its holding window crosses a
  close: the first H market-open minutes from entry span more than H minutes
  (`datafeed.features.vol_block.open_window`, the rule `sigma_over` uses for
  `crosses_close`).

Other exclusions (stale snapshot, invalid HAR state, no bars) are not modelled:
they need the live pipeline. Holidays aren't modelled (as in `_market_open`).

Run: python research/agents_v1/expected_exclusions.py
Writes results/expected_exclusions.json.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

from datafeed.features.vol_block import open_window

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCANNER_PATH = ROOT / "backend" / "deterministic_scanner.py"
OUT = HERE / "results" / "expected_exclusions.json"

DEV_START = pd.Timestamp("2007-01-01", tz="UTC")
DEV_END = pd.Timestamp("2021-01-01", tz="UTC")      # exclusive: 2021-2023 is the holdout
WARMUP_START = "2006-11-01"                          # bars before DEV_START only build levels
HORIZONS = (60, 240)
ENTRY_DELAY = timedelta(minutes=2)                   # flush bar final (+1 min), entry at the next open (+1 min)


def load_scanner() -> ModuleType:
    """The production scanner module, loaded by file path as
    `deterministic_scanner` (as tests/scanner/conftest.py does), never edited."""
    mod = sys.modules.get("deterministic_scanner")
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location("deterministic_scanner", SCANNER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the scanner from {SCANNER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["deterministic_scanner"] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass(frozen=True)
class AlertTiming:
    """One scanner alert and when it could be traded."""
    alert_id: str
    alert_ts: pd.Timestamp       # first touch in the bucket
    flush_ts: pd.Timestamp       # open time of the bar whose arrival emitted the alert
    entry_ts: pd.Timestamp       # first market-open minute >= flush_ts + ENTRY_DELAY


def entry_time(flush_ts: pd.Timestamp, delay: timedelta = ENTRY_DELAY) -> pd.Timestamp:
    """The first market-open minute at or after flush_ts + delay."""
    if flush_ts.tzinfo is None:
        raise ValueError(f"naive flush_ts {flush_ts!r}")
    minutes, _ = open_window(flush_ts + delay, 1)
    return pd.Timestamp(minutes[0])


def crosses_close(entry_ts: pd.Timestamp, H: int) -> bool:
    """True when the first H market-open minutes from entry_ts span more than
    H calendar minutes, i.e. the window holds a daily break or a weekend."""
    if isinstance(H, bool) or not isinstance(H, int) or H <= 0:
        raise ValueError(f"H must be a positive int (minutes), got {H!r}")
    minutes, end = open_window(entry_ts, H)
    return (end - minutes[0]) > timedelta(minutes=H)


def closure_kind(entry_ts: pd.Timestamp, H: int) -> str | None:
    """None if the window doesn't cross a close; 'weekend' if the closed span
    inside it is a day or longer; else 'daily_break'."""
    minutes, end = open_window(entry_ts, H)
    closed = (end - minutes[0]) - timedelta(minutes=H)
    if closed <= timedelta(0):
        return None
    return "weekend" if closed >= timedelta(days=1) else "daily_break"


def replay_alerts(bars: pd.DataFrame, scanner: ModuleType | None = None) -> list[AlertTiming]:
    """Every alert the scanner emits over `bars` (UTC index; open, high, low,
    close), with its flush bar and entry time. Bars the live scanner would drop
    (`_valid_bar`) are dropped first, as in run_live. Alerts still in the last
    bucket at the end of the data are dropped: no later bar emits them."""
    ds = scanner or load_scanner()
    if bars.index.tz is None:
        raise ValueError("bars need a tz-aware UTC index")
    if not bars.index.is_monotonic_increasing:
        raise ValueError("bars must be sorted by time")
    cols = bars[["open", "high", "low", "close"]].astype(float)
    keep = [ds._valid_bar(*r) for r in cols.itertuples(name=None)]
    cols = cols[keep]
    sc = ds.Scanner(ds.build_levels(cols))
    out: list[AlertTiming] = []
    for ts, o, h, lo, c in cols.itertuples(name=None):
        for a in sc.on_bar(ts, o, h, lo, c):
            out.append(AlertTiming(a["alert_id"], pd.Timestamp(a["alert_ts_utc"], tz="UTC"), ts,
                                   entry_time(ts)))
    return out


def summarise(alerts: Iterable[AlertTiming], trading_days: Mapping[int, int],
              horizons: tuple[int, ...] = HORIZONS) -> dict:
    """Alerts per trading day and the close-crossing share per horizon, overall
    and by year (year of the alert's first touch).

    trading_days: year -> number of NY trading days with bars. A year with
    alerts but no trading days raises."""
    by_year: dict[int, list[AlertTiming]] = defaultdict(list)
    for a in alerts:
        by_year[a.alert_ts.year].append(a)
    if set(by_year) - set(trading_days):
        raise ValueError(f"alerts in years without trading days: {sorted(set(by_year) - set(trading_days))}")

    def block(rows: list[AlertTiming], days: int) -> dict:
        if days <= 0:
            raise ValueError("trading days must be > 0")
        n = len(rows)
        res: dict = {"alerts": n, "trading_days": days, "alerts_per_day": round(n / days, 4)}
        for H in horizons:
            kinds = Counter(closure_kind(a.entry_ts, H) for a in rows)
            crossing = n - kinds[None]
            res[f"H{H}"] = {
                "crossing": crossing,
                "crossing_daily_break": kinds["daily_break"],
                "crossing_weekend": kinds["weekend"],
                "crossing_share": round(crossing / n, 4) if n else None,
                "eligible_per_day": round((n - crossing) / days, 4),
            }
        return res

    years = sorted(trading_days)
    overall = block([a for y in years for a in by_year[y]], sum(trading_days[y] for y in years))
    return {"overall": overall, "by_year": {str(y): block(by_year[y], trading_days[y]) for y in years}}


def entry_hour_profile(alerts: Iterable[AlertTiming]) -> dict[str, int]:
    """Alert counts by UTC hour of entry, for reading the crossing share."""
    c = Counter(a.entry_ts.hour for a in alerts)
    return {f"{h:02d}": c[h] for h in range(24)}


def trading_days_by_year(index: pd.DatetimeIndex,
                         trading_dates: Callable[[pd.DatetimeIndex], Any]) -> dict[int, int]:
    """Number of distinct NY trading dates with at least one bar, per year.
    trading_dates: research/levels' `trading_dates` (17:00 NY roll)."""
    dates = pd.Series(pd.to_datetime(trading_dates(index))).drop_duplicates()
    return {int(y): int(n) for y, n in dates.dt.year.value_counts().sort_index().items()}


def write_json_atomic(path: Path, payload: dict) -> None:
    """Write via a temp file in the same directory, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def _research_levels() -> tuple[ModuleType, ModuleType]:
    """research/levels' modules `run_levels` (XAUUSD.txt loader) and `levels`
    (trading_dates), imported as that study's own tests do."""
    path = str(ROOT / "research" / "levels")
    if path not in sys.path:
        sys.path.insert(0, path)
    import levels  # noqa: E402
    import run_levels  # noqa: E402
    return run_levels, levels


def load_dev_bars(run_levels: ModuleType) -> pd.DataFrame:
    """XAUUSD.txt 1-min bars from WARMUP_START to DEV_END, via research/levels'
    loader (cache, dedupe, bad-bar filter, seal assert)."""
    df, _bad = run_levels.load_minutes(start=WARMUP_START)
    df = df[df.index < DEV_END]
    if len(df) == 0 or df.index.max() >= DEV_END:
        raise RuntimeError("no development bars, or bars at or after 2021-01-01")
    return df


def main() -> dict:
    """Replay 2006-11 .. 2020, summarise alerts in 2007 .. 2020, write the JSON."""
    run_levels, lv = _research_levels()
    bars = load_dev_bars(run_levels)
    alerts = [a for a in replay_alerts(bars) if DEV_START <= a.alert_ts < DEV_END]
    dev = bars[bars.index >= DEV_START]
    days = {y: n for y, n in trading_days_by_year(dev.index, lv.trading_dates).items() if 2007 <= y <= 2020}
    payload = {
        "data": "XAUUSD.txt 1-min (UTC), alerts 2007-01-01 .. 2020-12-31; level warm-up from 2006-11-01",
        "entry_delay_minutes": ENTRY_DELAY.total_seconds() / 60,
        "not_modelled": ["stale snapshot", "invalid HAR state", "no bars in the window", "holidays",
                         "missed_while_down / missed_history (live downtime)"],
        "summary": summarise(alerts, days),
        "entry_hour_utc": entry_hour_profile(alerts),
    }
    write_json_atomic(OUT, payload)
    return payload


if __name__ == "__main__":
    res = main()["summary"]["overall"]
    print(json.dumps(res, indent=2))
