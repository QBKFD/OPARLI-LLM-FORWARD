"""Synthetic 1-min XAUUSD-like bars for the level tests: a seeded random walk
over market hours only (no bars from Friday 21:00 to Sunday 22:00 UTC, none
in the daily 21:00-22:00 break), so CI needs no data files."""
import numpy as np
import pandas as pd

START = pd.Timestamp("2025-06-02", tz="UTC")       # a Monday


def bars(days: int = 21, seed: int = 7, start: pd.Timestamp = START) -> pd.DataFrame:
    idx = pd.date_range(start, start + pd.Timedelta(days=days), freq="1min", tz="UTC", inclusive="left")
    wd, h = idx.weekday, idx.hour
    open_ = ~((wd == 5) | ((wd == 4) & (h >= 21)) | ((wd == 6) & (h < 22)) | (h == 21))
    idx = idx[open_]
    rng = np.random.default_rng(seed)
    close = 3300 + np.cumsum(rng.normal(0, 0.6, len(idx)))
    open_px = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.4, len(idx)))
    return pd.DataFrame({"open": open_px, "high": np.maximum(open_px, close) + spread,
                         "low": np.minimum(open_px, close) - spread, "close": close}, index=idx).round(3)
