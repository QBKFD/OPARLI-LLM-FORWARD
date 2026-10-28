"""Silver dev data and sigma for research/breakout_fade (B1, B2, B4).

- Bars: the XAGUSD parquet, refused unless research/data_guard.py allows this
  study (the committed PREREG names its manifest key) and it matches the data
  manifest; only dev [2016-01-01, 2024-01-01) is kept, and
  sealing.assert_period("XAGUSD", ts, "dev") checks every row.
- sigma: research/squeeze_oco/stage1.state_table (sigma_compare's method) with
  gold's pinned model parquet2021_2023 on silver's bars (B4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "squeeze_oco", ROOT / "research" / "vol_features_r3",
           ROOT / "research" / "sigma_compare"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import data_guard  # noqa: E402
import run_sigma as rs  # noqa: E402
import sealing  # noqa: E402
import stage1  # noqa: E402

from backend.datafeed.store import manifest  # noqa: E402

import fade_params as F  # noqa: E402


def load() -> tuple[pd.DataFrame, dict]:
    path = ROOT / F.DATA_KEY
    data_guard.assert_may_read(path, study=F.STUDY)
    key = manifest.verify(path)
    if key != F.DATA_KEY:
        raise AssertionError(f"{path} matched manifest entry {key!r}")
    df = pd.read_parquet(path)
    idx = pd.DatetimeIndex(df["timestamp"])
    keep = (idx >= F.DEV_START) & (idx < F.DEV_END_EXCL)
    m = df.loc[keep, ["open", "high", "low", "close"]].astype(float)
    m.index = idx[keep]
    m.index.name = "timestamp"
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise AssertionError("XAGUSD bars must be sorted and unique")
    sealing.assert_period(F.INSTRUMENT, m.index, "dev", "XAGUSD dev 1-min bars")
    return m, {"manifest_key": key, "manifest_sha256": manifest.load()["files"][key]["sha256"], "rows": int(len(m))}


def sigma_table(m: pd.DataFrame) -> tuple[pd.DataFrame, object]:
    model = stage1.load_model(F.SIGMA_MODEL, F.SIGMA_MODEL_SHA256)
    ctx = stage1.context(m, F.DEV_END_EXCL, rs.HORIZONS, rs.COVERAGE)
    tbl = stage1.state_table(ctx, model, m.index[0], F.DEV_END_EXCL)
    return tbl[[f"sigma_px_{F.SIGMA_H}", f"sigma_ret_{F.SIGMA_H}"]], model


def at_labels(tbl: pd.DataFrame, col: str, ends: pd.DatetimeIndex) -> np.ndarray:
    return tbl[col].reindex(ends).to_numpy(float)


def per_minute_sigma(tbl: pd.DataFrame, index: pd.DatetimeIndex) -> np.ndarray:
    """B5 (as pullback_structure D12): sigma_ret_240 / sqrt(240) from the label at or
    before each minute's hour; minutes before the first state take the first one."""
    s = tbl[f"sigma_ret_{F.SIGMA_H}"].dropna()
    pos = np.searchsorted(s.index.asi8, index.floor("1h").asi8, side="right") - 1
    return s.to_numpy()[np.clip(pos, 0, None)] / np.sqrt(F.SIGMA_H)
