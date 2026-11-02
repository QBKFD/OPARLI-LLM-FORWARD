"""research/news_vol_rep: data access, the only module that reads files.

- Bars (R1): each market's IBKR parquet, through research/data_guard (the
  committed PREREG names it), the data manifest and sealing. The read itself
  is filtered to dev [2016-01-01, 2024-01-01), so no holdout row is loaded,
  and every kept row is checked with sealing.assert_period(market, ts, "dev").
- Events (R2): the clean macro calendar of PR #60 (merged as 0988ca3), through
  data_guard and Study A's pinned SHA-256; usd_tier1, timed, as Study A.
  R5: EUR High-impact timestamps are read for a count only.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "news_vol", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_guard  # noqa: E402
import nv  # noqa: E402
import rep_params as V  # noqa: E402
import sealing  # noqa: E402

from backend.datafeed.store import manifest  # noqa: E402

# Manifest keys, next to the data_guard calls (research/test_data_guard.py checks this statically)
BAR_KEYS = {"XAGUSD": "database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet",
            "EURUSD": "database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet"}
CALENDAR_KEY = "database/historical_news/macro_calendar_utc.parquet"


def _check_bars_file(market: str) -> tuple[Path, dict]:
    key = BAR_KEYS[market]
    path = ROOT / key
    data_guard.assert_may_read(path, study=V.STUDY)
    got = manifest.verify(path)
    if got != key:
        raise AssertionError(f"{path} matched manifest entry {got!r}")
    return path, {"manifest_key": key, "manifest_sha256": manifest.load()["files"][key]["sha256"]}


def _read_dev(path: Path, columns: list[str]) -> pd.DataFrame:
    return pd.read_parquet(path, columns=columns,
                           filters=[("timestamp", ">=", V.DEV_START), ("timestamp", "<", V.DEV_END_EXCL)])


def bar_times(market: str) -> tuple[pd.DatetimeIndex, dict]:
    """Bar open times only (no price column is read): for the counts and the machinery check."""
    path, checks = _check_bars_file(market)
    idx = pd.DatetimeIndex(_read_dev(path, ["timestamp"])["timestamp"])
    if not idx.is_monotonic_increasing or idx.has_duplicates:
        raise AssertionError(f"{market} bar times must be sorted and unique")
    sealing.assert_period(market, idx, "dev", f"{market} dev bar times")
    idx = idx.as_unit("ns")
    checks.update(rows=int(len(idx)), first=str(idx[0]), last=str(idx[-1]))
    return idx, checks


def bars(market: str) -> tuple[pd.DataFrame, dict]:
    """The market's dev 1-min OHLC (UTC, sorted, unique). Reads prices: only in the single run."""
    path, checks = _check_bars_file(market)
    df = _read_dev(path, ["timestamp", "open", "high", "low", "close"])
    idx = pd.DatetimeIndex(df["timestamp"]).as_unit("ns")
    m = df[["open", "high", "low", "close"]].astype(float).set_axis(idx)
    m.index.name = "timestamp"
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise AssertionError(f"{market} bars must be sorted and unique")
    sealing.assert_period(market, m.index, "dev", f"{market} dev 1-min bars")
    checks.update(rows=int(len(m)), first=str(m.index[0]), last=str(m.index[-1]))
    return m, checks


def _calendar() -> tuple[pd.DataFrame, str]:
    path = ROOT / CALENDAR_KEY
    data_guard.assert_may_read(path, study=V.STUDY)
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    if h != V.CALENDAR_SHA256:
        raise AssertionError(f"{path}: SHA-256 {h}, the PREREG pins {V.CALENDAR_SHA256}")
    return pd.read_parquet(path, columns=["ts_utc", "time_flag", "date_local", "currency", "event", "impact"]), h


def events() -> tuple[pd.DataFrame, dict]:
    """[R2, Study A D2] usd_tier1 rows with a timestamp in dev, grouped by release minute."""
    c, h = _calendar()
    t1 = c[c["currency"].eq("USD") & c["impact"].eq("High") & c["event"].isin(V.TIER1)]
    dev_day = (t1["date_local"] >= str(V.DEV_START.date())) & (t1["date_local"] < str(V.DEV_END_EXCL.date()))
    t1 = t1[(t1["ts_utc"].isna() & dev_day) | ((t1["ts_utc"] >= V.DEV_START) & (t1["ts_utc"] < V.DEV_END_EXCL))]
    untimed = t1[t1["ts_utc"].isna()]
    timed = t1[t1["ts_utc"].notna()]
    for mk in V.MARKETS:
        sealing.assert_period(mk, pd.DatetimeIndex(timed["ts_utc"]), "dev", "calendar rows")
    ev = nv.group_events(timed, V.DATA_EVENTS, V.FOMC_EVENTS)
    info = {"calendar_key": CALENDAR_KEY, "calendar_sha256": h, "calendar_commit": V.CALENDAR_COMMIT,
            "tier1_rows_timed": int(len(timed)),
            "tier1_rows_untimed_dev": int(len(untimed)),
            "untimed_flags": {str(k): int(v) for k, v in untimed["time_flag"].value_counts().items()},
            "events_grouped": int(len(ev)),
            "mixed_minutes": int(((ev["type"] == "FOMC") & ev["events"].str.contains("CPI|Non-Farm")).sum())}
    return ev, info


def eur_release_minutes() -> pd.DatetimeIndex:
    """[R5] Minutes of timed EUR High-impact rows in dev: counted, never flagged."""
    c, _ = _calendar()
    e = c[c["currency"].eq(V.EUR_COUNT_CURRENCY) & c["impact"].eq(V.EUR_COUNT_IMPACT) & c["ts_utc"].notna()]
    ts = pd.DatetimeIndex(e["ts_utc"]).tz_convert("UTC")
    ts = ts[(ts >= V.DEV_START) & (ts < V.DEV_END_EXCL)]
    sealing.assert_period("EURUSD", ts, "dev", "EUR calendar rows")
    return pd.DatetimeIndex(ts.floor("1min").unique()).sort_values()
