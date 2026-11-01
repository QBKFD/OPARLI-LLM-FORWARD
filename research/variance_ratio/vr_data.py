"""Data for research/variance_ratio (V1, V2, V6, V7). EXPLORATORY, descriptive.

- Bars, dev only [2016-01-01, 2024-01-01), every market through
  research/data_guard (the committed PREREG.md names each file) and the data
  manifest, then sealing.assert_period(market, ts, "dev"):
  - XAUUSD: XAUUSD.txt through the audit_long cache (research/vol_features_r2),
    as research/pullback_structure/data.py reads it;
  - XAGUSD, EURUSD, USDJPY: their IBKR parquets, as research/breakout_fade reads silver.
- sigma_240: research/squeeze_oco/stage1.state_table with gold's pinned model
  parquet2021_2023 on each market's bars (breakout_fade B4's method).
- News days: the clean macro calendar of data/macro-calendar (PR #60, commit
  12f65c6, anchor check PASS), SHA-256 checked.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "squeeze_oco", ROOT / "research" / "vol_features_r2",
           ROOT / "research" / "vol_features_r3", ROOT / "research" / "sigma_compare", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_guard  # noqa: E402
import run_r3 as r3  # noqa: E402
import run_sigma as rs  # noqa: E402
import sealing  # noqa: E402
import stage1  # noqa: E402

from backend.datafeed.store import manifest  # noqa: E402

import vr_params as V  # noqa: E402

# V1: the manifest key of each market's file (kept next to the data_guard call, research/test_data_guard.py)
DATA_KEYS = {
    "XAUUSD": "database/ohlcv_data/XAUUSD.txt",
    "XAGUSD": "database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet",
    "EURUSD": "database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet",
    "USDJPY": "database/ohlcv_data/USDJPY_ibkr_1min_20160101_20260930.parquet",
}
CALENDAR_KEY = "database/historical_news/macro_calendar_utc.parquet"     # V7


def _dev(df: pd.DataFrame, market: str) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df.index)
    keep = (idx >= V.DEV_START) & (idx < V.DEV_END_EXCL)
    m = df.loc[keep, ["open", "high", "low", "close"]].astype(float)
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise AssertionError(f"{market} bars must be sorted and unique")
    sealing.assert_period(market, m.index, "dev", f"{market} dev 1-min bars")
    return m


def load(market: str) -> tuple[pd.DataFrame, dict]:
    key = DATA_KEYS[market]
    path = ROOT / key
    data_guard.assert_may_read(path, study=V.STUDY)
    got = manifest.verify(path)
    if got != key:
        raise AssertionError(f"{path} matched manifest entry {got!r}")
    checks = {"manifest_key": key, "manifest_sha256": manifest.load()["files"][key]["sha256"]}
    if market == "XAUUSD":
        df = pd.read_parquet(r3.CACHE).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        df.index = df.index.tz_localize("UTC")             # the file is UTC (DATA_AUDIT_LONG.md)
        df = df[(df.index >= V.DEV_START) & (df.index < V.DEV_END_EXCL)]
        r3.assert_sealed(df, "XAUUSD dev 1-min", end=V.DEV_END_EXCL)
    else:
        raw = pd.read_parquet(path)
        df = raw.set_index(pd.DatetimeIndex(raw["timestamp"]))
        df = df[(df.index >= V.DEV_START) & (df.index < V.DEV_END_EXCL)]
    m = _dev(df, market)
    m.index.name = "timestamp"
    checks["rows"] = int(len(m))
    return m, checks


def sigma_240(m: pd.DataFrame) -> pd.Series:
    """sigma_ret_240 per hourly close-time label (the state uses bars before the label)."""
    model = stage1.load_model(V.SIGMA_MODEL, V.SIGMA_MODEL_SHA256)
    ctx = stage1.context(m, V.DEV_END_EXCL, rs.HORIZONS, rs.COVERAGE)
    tbl = stage1.state_table(ctx, model, m.index[0], V.DEV_END_EXCL)
    s = tbl[f"sigma_ret_{V.SIGMA_H}"]
    sealing.assert_before_global_seal(pd.DatetimeIndex(s.index), "sigma labels")
    return s


def news_dates(calendar_path: Path) -> tuple[set, dict]:
    """UTC dates in dev with a timed, High-impact USD row of NEWS_EVENTS."""
    data_guard.assert_may_read(calendar_path, study=V.STUDY)
    h = hashlib.sha256(calendar_path.read_bytes()).hexdigest()
    if h != V.CALENDAR_SHA256:
        raise AssertionError(f"{calendar_path}: SHA-256 {h}, the plan pins {V.CALENDAR_SHA256}")
    c = pd.read_parquet(calendar_path, columns=["ts_utc", "currency", "event", "impact"])
    c = c[c["ts_utc"].notna() & c["currency"].eq("USD") & c["impact"].eq("High") & c["event"].isin(V.NEWS_EVENTS)]
    c = c[(c["ts_utc"] >= V.DEV_START) & (c["ts_utc"] < V.DEV_END_EXCL)]
    sealing.assert_before_global_seal(pd.DatetimeIndex(c["ts_utc"]), "calendar rows")
    days = set(c["ts_utc"].dt.tz_convert("UTC").dt.date)
    return days, {"calendar_key": CALENDAR_KEY, "calendar_sha256": h, "calendar_commit": V.CALENDAR_COMMIT,
                  "news_rows": int(len(c)), "news_days": len(days)}


def tercile_edges(sig: np.ndarray) -> tuple[float, float]:
    x = sig[np.isfinite(sig)]
    return float(np.quantile(x, 1 / 3)), float(np.quantile(x, 2 / 3))
