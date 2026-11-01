"""research/news_vol (Study A): data access, the only module that reads files.

- Bars: XAUUSD dev [2016-01-01, 2024-01-01), XAUUSD.txt through the audit_long
  cache (as research/variance_ratio and research/pullback_structure read it),
  through research/data_guard, the data manifest and sealing.assert_period.
- Events: the clean macro calendar of PR #60 (merged as 0988ca3), through
  data_guard (PREREG.md names it) and its pinned SHA-256; usd_tier1, timed.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "vol_features", ROOT / "research" / "vol_features_r3", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_guard  # noqa: E402
import nv  # noqa: E402
import nv_params as V  # noqa: E402
import run_r3 as r3  # noqa: E402
import sealing  # noqa: E402

from backend.datafeed.store import manifest  # noqa: E402


def _check_bars_file() -> dict:
    path = ROOT / V.DATA_KEY
    data_guard.assert_may_read(path, study=V.STUDY)
    got = manifest.verify(path)
    if got != V.DATA_KEY:
        raise AssertionError(f"{path} matched manifest entry {got!r}")
    return {"manifest_key": V.DATA_KEY, "manifest_sha256": manifest.load()["files"][V.DATA_KEY]["sha256"]}


def _dev_index(idx: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx).tz_localize("UTC")          # the file is UTC (DATA_AUDIT_LONG.md)
    return idx


def bar_times() -> tuple[pd.DatetimeIndex, dict]:
    """Bar open times only (no price column is read): for the counts (PREREG section 5)."""
    checks = _check_bars_file()
    idx = _dev_index(pd.read_parquet(r3.CACHE, columns=[]).index)
    idx = idx[(idx >= V.DEV_START) & (idx < V.DEV_END_EXCL)].sort_values()
    idx = idx[~idx.duplicated(keep="first")]
    sealing.assert_period(V.MARKET, idx, "dev", "XAUUSD dev bar times")
    checks["rows"] = int(len(idx))
    return idx, checks


def bars() -> tuple[pd.DataFrame, dict]:
    """XAUUSD dev 1-min OHLC (UTC, sorted, unique). Reads prices: only in the single run."""
    checks = _check_bars_file()
    df = pd.read_parquet(r3.CACHE).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = _dev_index(df.index)
    df = df[(df.index >= V.DEV_START) & (df.index < V.DEV_END_EXCL)]
    r3.assert_sealed(df, "XAUUSD dev 1-min", end=V.DEV_END_EXCL)
    m = df[["open", "high", "low", "close"]].astype(float)
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise AssertionError("bars must be sorted and unique")
    sealing.assert_period(V.MARKET, m.index, "dev", "XAUUSD dev 1-min bars")
    m.index.name = "timestamp"
    checks["rows"] = int(len(m))
    return m, checks


def events() -> tuple[pd.DataFrame, dict]:
    """[D2] usd_tier1 rows with a timestamp in dev, grouped by release minute."""
    path = ROOT / V.CALENDAR_KEY
    data_guard.assert_may_read(path, study=V.STUDY)
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    if h != V.CALENDAR_SHA256:
        raise AssertionError(f"{path}: SHA-256 {h}, the PREREG pins {V.CALENDAR_SHA256}")
    c = pd.read_parquet(path, columns=["ts_utc", "time_flag", "date_local", "currency", "event", "impact"])
    t1 = c[c["currency"].eq("USD") & c["impact"].eq("High") & c["event"].isin(V.TIER1)]
    dev_day = (t1["date_local"] >= str(V.DEV_START.date())) & (t1["date_local"] < str(V.DEV_END_EXCL.date()))
    t1 = t1[(t1["ts_utc"].isna() & dev_day) | ((t1["ts_utc"] >= V.DEV_START) & (t1["ts_utc"] < V.DEV_END_EXCL))]
    untimed = t1[t1["ts_utc"].isna()]
    timed = t1[t1["ts_utc"].notna()]
    sealing.assert_period(V.MARKET, pd.DatetimeIndex(timed["ts_utc"]), "dev", "calendar rows")
    ev = nv.group_events(timed, V.DATA_EVENTS, V.FOMC_EVENTS)
    info = {"calendar_key": V.CALENDAR_KEY, "calendar_sha256": h, "calendar_commit": V.CALENDAR_COMMIT,
            "tier1_rows_timed": int(len(timed)),
            "tier1_rows_untimed_dev": int(len(untimed)),
            "untimed_flags": {str(k): int(v) for k, v in untimed["time_flag"].value_counts().items()},
            "events_grouped": int(len(ev)),
            "mixed_minutes": int(((ev["type"] == "FOMC") & ev["events"].str.contains("CPI|Non-Farm")).sum())}
    return ev, info
