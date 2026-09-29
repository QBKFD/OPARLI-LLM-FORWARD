"""
Volatility block for the regime model: L, S, S_hd, T.

Definitions are exactly those of research/vol_features_r3 (round 3):

- 1-min log returns r_i = ln(C_i / C_{i-1}); dropped if the 1-min bar opens
  more than 5 minutes after the previous one (daily break, weekends). No
  interpolation.
- Seasonal profile: mean of r² per (US-DST flag, 5-minute bucket of the UTC
  day), 576 buckets, estimated on the training data only. Buckets with fewer
  than 50 returns get the median of the other buckets. Floored at the 1st
  percentile across buckets. r_ds = r / sqrt(profile[bucket]).
- 1H bars labelled by close time t hold the 1-min bars opening in [t-1h, t).
  mRV_n(t) = mean of r_ds² over the available returns in the last n bar rows.
  All n rows must exist, and at least 50 % of the 60·n minutes must be present.
- L = log mRV_24;  S = log mRV_1 - log mRV_24;  T = log mRV_24 - log mRV_120.
- S_hd = S minus the mean of S for the bar's UTC hour, computed on the training
  bars (engineering decision, VOL_BLOCK_DECISION.md; not pre-registered).

Usage:
    vb = VolBlock().fit(train_1min)          # 1-min OHLC, UTC DatetimeIndex (bar open)
    feats = vb.transform(all_1min, bar_times)  # DataFrame [L, S, S_hd, T] at bar_times

Causality: feature values at bar t use only 1-min bars opening before t. fit()
uses only the rows it is given.
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

GAP = pd.Timedelta(minutes=5)
N_BUCKETS = 576
MIN_BUCKET_OBS = 50
FEATURE_COVERAGE = 0.50
WINDOWS = (1, 24, 120)


def _returns(m: pd.DataFrame) -> pd.Series:
    dt = m.index.to_series().diff()
    flag = (dt > GAP) | dt.isna()
    r = np.log(m["close"]).diff()
    r[flag.values] = np.nan
    return r


def _buckets(index: pd.DatetimeIndex) -> np.ndarray:
    local = index.tz_convert("America/New_York").tz_localize(None)
    dst = np.asarray((local - index.tz_localize(None)) == pd.Timedelta(hours=-4))
    return dst.astype(int) * 288 + (index.hour * 60 + index.minute) // 5


def _bar_labels(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Close-time labels of the 1H bars that contain at least one 1-min bar."""
    return pd.DatetimeIndex(index.floor("1h").unique() + pd.Timedelta(hours=1))


def _window_sum(index: pd.DatetimeIndex, x: np.ndarray, starts, ends):
    ok = ~np.isnan(x)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    cn = np.concatenate([[0], np.cumsum(ok)])
    t = index.asi8
    i0 = np.searchsorted(t, np.asarray(starts), "left")
    i1 = np.searchsorted(t, np.asarray(ends), "left")
    return cs[i1] - cs[i0], cn[i1] - cn[i0]


def _check(m: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(m.index, pd.DatetimeIndex) or m.index.tz is None:
        raise ValueError("1-min data needs a tz-aware DatetimeIndex (UTC)")
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise ValueError("1-min index must be sorted and unique")
    return m


class VolBlock:
    """Deseasonalized, per-minute-mean volatility block (round 3 definitions)."""

    def __init__(self):
        self.profile_: Optional[np.ndarray] = None
        self.s_hour_mean_: Optional[pd.Series] = None
        self.train_start_: Optional[pd.Timestamp] = None
        self.train_end_: Optional[pd.Timestamp] = None

    # ---------------------------------------------------------------- fit
    def fit(self, train_1min: pd.DataFrame) -> "VolBlock":
        m = _check(train_1min)
        r = _returns(m).to_numpy()
        b = _buckets(m.index)
        ok = ~np.isnan(r)
        s = np.bincount(b[ok], weights=r[ok] ** 2, minlength=N_BUCKETS)
        n = np.bincount(b[ok], minlength=N_BUCKETS)
        prof = np.where(n >= MIN_BUCKET_OBS, s / np.maximum(n, 1), np.nan)
        if np.isnan(prof).all():
            raise ValueError("no bucket has enough returns to fit the seasonal profile")
        prof = np.where(np.isnan(prof), np.nanmedian(prof), prof)
        self.profile_ = np.maximum(prof, np.quantile(prof, 0.01))
        self.train_start_ = m.index[0]
        self.train_end_ = m.index[-1] + pd.Timedelta(minutes=1)

        # hour means of S over the training bars (fully inside the training data)
        f = self._lst(m, _bar_labels(m.index))
        f = f[f.index <= self.train_end_]
        self.s_hour_mean_ = f["S"].groupby(f.index.hour).mean()
        return self

    # ---------------------------------------------------------------- transform
    def transform(self, m_1min: pd.DataFrame, bar_times=None) -> pd.DataFrame:
        if self.profile_ is None:
            raise RuntimeError("VolBlock is not fitted")
        m = _check(m_1min)
        labels = _bar_labels(m.index)
        f = self._lst(m, labels)
        hm = f.index.hour.map(self.s_hour_mean_).to_numpy(dtype=float)
        f["S_hd"] = f["S"] - hm
        f = f[["L", "S", "S_hd", "T"]]
        if bar_times is not None:
            f = f.reindex(pd.DatetimeIndex(bar_times))
        return f

    # ---------------------------------------------------------------- internals
    def _lst(self, m: pd.DataFrame, labels: pd.DatetimeIndex) -> pd.DataFrame:
        r = _returns(m).to_numpy()
        rds2 = r ** 2 / self.profile_[_buckets(m.index)]
        s1, n1 = _window_sum(m.index, rds2, (labels - pd.Timedelta(hours=1)).asi8, labels.asi8)
        sums = pd.Series(s1, index=labels, dtype=float)
        cnts = pd.Series(n1, index=labels, dtype=float)
        logm = {}
        for n in WINDOWS:
            s_n = sums.rolling(n, min_periods=n).sum()
            c_n = cnts.rolling(n, min_periods=n).sum()
            mean = (s_n / c_n).where(c_n >= FEATURE_COVERAGE * 60 * n)
            with np.errstate(divide="ignore", invalid="ignore"):
                logm[n] = np.log(mean.where(mean > 0))
        return pd.DataFrame({"L": logm[24], "S": logm[1] - logm[24],
                             "T": logm[24] - logm[120]}, index=labels)
