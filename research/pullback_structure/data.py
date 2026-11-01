"""Data and sigma for research/pullback_structure (P9, P21, P22).

- Bars: XAUUSD.txt, checked against the data manifest and research/data_guard,
  read through the audit_long cache (research/vol_features_r2), UTC,
  duplicates dropped keeping the first. Only [WARMUP_START, DEV_END_EXCL) is
  kept, and the seal guards run on it (sealing.assert_before_global_seal and
  run_r3.assert_sealed with the dev end).
- sigma: research/squeeze_oco/stage1.state_table (sigma_compare's method on
  the shipped, SHA-checked model), read at each candle's close label.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "squeeze_oco", ROOT / "research" / "vol_features_r2",
           ROOT / "research" / "vol_features_r3", ROOT / "research" / "sigma_compare"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audit_long  # noqa: E402
import data_guard  # noqa: E402
import run_r3 as r3  # noqa: E402
import run_sigma as rs  # noqa: E402
import sealing  # noqa: E402
import stage1  # noqa: E402  (research/squeeze_oco: context, state_table, load_model)

from backend.datafeed.store import manifest  # noqa: E402

import params as P  # noqa: E402


def guard(idx: pd.DatetimeIndex, start: pd.Timestamp, end_excl: pd.Timestamp, what: str) -> None:
    sealing.assert_before_global_seal(idx, what)
    r3.assert_sealed(pd.DataFrame(index=idx), what, end=end_excl)
    if len(idx) and ((idx < start).any() or (idx >= end_excl).any()):
        raise AssertionError(f"OUTSIDE [{start}, {end_excl}): {what}")


def load(start: pd.Timestamp, end_excl: pd.Timestamp, verify_cache: bool) -> tuple[pd.DataFrame, dict]:
    if end_excl > P.DEV_END_EXCL:
        raise AssertionError("this loader serves the dev period only (P22); the holdout has its own run")
    txt = ROOT / P.DATA_KEY
    data_guard.assert_may_read(txt, study=P.STUDY)
    key = manifest.verify(txt)
    if key != P.DATA_KEY:
        raise AssertionError(f"{txt} matched manifest entry {key!r}")
    checks = {"manifest_key": key, "manifest_sha256": manifest.load()["files"][key]["sha256"]}
    df = pd.read_parquet(r3.CACHE).sort_index()
    if verify_cache:
        import tempfile
        saved = audit_long.CACHE
        with tempfile.TemporaryDirectory() as d:
            audit_long.CACHE = Path(d) / "fresh.parquet"
            try:
                fresh = audit_long.load_raw().sort_index()
            finally:
                audit_long.CACHE = saved
        pd.testing.assert_frame_equal(df, fresh)
        checks["cache_equals_fresh_parse"] = True
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_localize("UTC")
    df = df[(df.index >= start) & (df.index < end_excl)]
    guard(df.index, start, end_excl, "1-min bars")
    m = df[["open", "high", "low", "close"]].astype(float)
    checks["rows"] = int(len(m))
    return m, checks


def sigma_table(m: pd.DataFrame, end_excl: pd.Timestamp) -> tuple[pd.DataFrame, object]:
    """Per hourly label: sigma_px_240 and sigma_ret_240 of the pinned model."""
    model = stage1.load_model(P.SIGMA_MODEL, P.SIGMA_MODEL_SHA256)
    ctx = stage1.context(m, end_excl, rs.HORIZONS, rs.COVERAGE)
    tbl = stage1.state_table(ctx, model, m.index[0], end_excl)
    return tbl[[f"sigma_px_{P.SIGMA_H}", f"sigma_ret_{P.SIGMA_H}"]], model


def at_labels(tbl: pd.DataFrame, col: str, ends: pd.DatetimeIndex) -> np.ndarray:
    """The value at each label (a candle's end); NaN where the grid has no row."""
    return tbl[col].reindex(ends).to_numpy(float)


def per_minute_sigma(tbl: pd.DataFrame, index: pd.DatetimeIndex) -> np.ndarray:
    """§5: per-minute sd = sigma_ret_240 / sqrt(240), from the label at or before
    the minute's hour (the state as of the start of that hour). Minutes before the
    first finite state take the first one (warm-up only; no event starts there)."""
    s = tbl[f"sigma_ret_{P.SIGMA_H}"].dropna()
    pos = np.searchsorted(s.index.asi8, index.floor("1h").asi8, side="right") - 1
    v = s.to_numpy()[np.clip(pos, 0, None)]
    return v / np.sqrt(P.SIGMA_H)
