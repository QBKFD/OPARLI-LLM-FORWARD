"""Volatility block L, S, S_hd, T for live code, plus sigma over a horizon.

THE MATHS IS RESEARCH'S
    fit() and transform() are research/vol_block/vol_block.py (VolBlock), round 3
    definitions, copied operation for operation so the outputs are bit-identical
    on the same input (tests/datafeed/test_vol_block.py). research/vol_block stays
    frozen; this module does not import it.

    - 1-min log returns r = ln(C_i / C_{i-1}); dropped when the bar opens more
      than 5 min after the previous one (daily break, weekends), and for the
      first bar of the input.
    - Seasonal profile: mean r² per (US-DST flag, 5-min bucket of the UTC day),
      576 buckets, from the training rows only. Buckets with < 50 returns get
      the median of the others; floored at the 1st percentile.
      r_ds = r / sqrt(profile[bucket]).
    - 1H rows labelled by close time t hold the 1-min bars opening in [t-1h, t).
      mRV_n(t) = mean r_ds² over the last n rows (all n rows present, >= 50 % of
      the 60·n minutes).
    - L = log mRV_24;  S = log mRV_1 - log mRV_24;  T = log mRV_24 - log mRV_120.
    - S_hd = S minus the training mean of S for the row's UTC hour.

    Only the fitted state differs in form: a frozen VolParams (JSON round trip
    is exact) instead of attributes on a VolBlock object.

AS OF t (state_at, read_state)
    Bars that had not ended by t (bar_ts + 1 min > t) are dropped first. The
    features are those of the latest hourly label <= t, so every bar they use
    opened before that label. The row of a label is complete only if its window
    does not reach the first row of the input, which may be partial; otherwise
    the state is NaN with reason "insufficient_history".

SIGMA OVER H MINUTES: ENGINEERING DECISION, NOT A RESEARCH RESULT
    (owner, 2026-10-14.) No pre-registered study tested it.
        sigma_ret² = exp(L) × Σ profile[bucket(u)]
                     over the minutes u opening in [t, t+H) that are market-open
        sigma_px   = close_ref × sigma_ret
    exp(L) is the mean deseasonalized 1-min variance of the last 24 rows, so
    this assumes today's level persists and follows the usual time-of-day
    shape. It has no fitted coefficients and ignores S_hd and T. Gap returns
    are dropped by the definitions above, so sigma holds no daily-break or
    weekend gap: closed minutes count zero, and `crosses_close` says the window
    contains some. Whether such a trade is allowed is the caller's decision.
    Calibration against realised moves (pre-2024): tools/sigma_calibration/.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from datafeed.ingest.xauusd_bars import SEAL_START, _market_open
from datafeed.store.query import DEFAULT_KINDS, bars_before

GAP = pd.Timedelta(minutes=5)
N_BUCKETS = 576
MIN_BUCKET_OBS = 50
FEATURE_COVERAGE = 0.50
WINDOWS = (1, 24, 120)
BAR = pd.Timedelta(minutes=1)
# Store read for read_state. 120 rows are ~5.5 trading days; a shorter store
# or a long holiday gives "insufficient_history", never a truncated value.
LOOKBACK = timedelta(days=14)


# ---------------------------------------------------------------- research maths

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
    """Close-time labels of the 1H rows that contain at least one 1-min bar."""
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
    if str(m.index.tz) != "UTC":
        raise ValueError(f"1-min index must be UTC, got {m.index.tz}")
    if not m.index.is_monotonic_increasing or m.index.has_duplicates:
        raise ValueError("1-min index must be sorted and unique")
    return m


def _lst(m: pd.DataFrame, labels: pd.DatetimeIndex, profile: np.ndarray) -> pd.DataFrame:
    r = _returns(m).to_numpy()
    rds2 = r ** 2 / profile[_buckets(m.index)]
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


# ---------------------------------------------------------------- fitted state

def _iso(ts) -> str:
    return pd.Timestamp(ts).isoformat()


@dataclass(frozen=True)
class VolParams:
    """Fitted state. s_hour_mean[h] is NaN for a UTC hour with no training row."""
    profile: tuple[float, ...]           # N_BUCKETS
    s_hour_mean: tuple[float, ...]       # 24, by UTC hour
    train_start: datetime                # first training bar
    train_end: datetime                  # last training bar + 1 min

    def __post_init__(self):
        if len(self.profile) != N_BUCKETS or len(self.s_hour_mean) != 24:
            raise ValueError("profile needs 576 values and s_hour_mean 24")
        if pd.Timestamp(self.train_end) > SEAL_START:
            raise ValueError(f"training window ends after the seal ({SEAL_START.isoformat()})")

    def to_json(self) -> str:
        # floats are written with repr, so the round trip is exact; NaN -> null
        return json.dumps({
            "profile": list(self.profile),
            "s_hour_mean": [None if math.isnan(x) else x for x in self.s_hour_mean],
            "train_start": _iso(self.train_start), "train_end": _iso(self.train_end),
        }, allow_nan=False)

    @classmethod
    def from_json(cls, s: str) -> "VolParams":
        d = json.loads(s)
        return cls(profile=tuple(float(x) for x in d["profile"]),
                   s_hour_mean=tuple(math.nan if x is None else float(x) for x in d["s_hour_mean"]),
                   train_start=pd.Timestamp(d["train_start"]).to_pydatetime(),
                   train_end=pd.Timestamp(d["train_end"]).to_pydatetime())


def fit(train_1min: pd.DataFrame) -> VolParams:
    """Research VolBlock.fit. Uses only the rows given; a training window that
    ends after the seal raises."""
    m = _check(train_1min)
    if m.empty:
        raise ValueError("no training rows")
    train_end = m.index[-1] + BAR
    if train_end > SEAL_START:
        raise ValueError(f"training window ends after the seal ({SEAL_START.isoformat()})")
    r = _returns(m).to_numpy()
    b = _buckets(m.index)
    ok = ~np.isnan(r)
    s = np.bincount(b[ok], weights=r[ok] ** 2, minlength=N_BUCKETS)
    n = np.bincount(b[ok], minlength=N_BUCKETS)
    prof = np.where(n >= MIN_BUCKET_OBS, s / np.maximum(n, 1), np.nan)
    if np.isnan(prof).all():
        raise ValueError("no bucket has enough returns to fit the seasonal profile")
    prof = np.where(np.isnan(prof), np.nanmedian(prof), prof)
    profile = np.maximum(prof, np.quantile(prof, 0.01))

    # hour means of S over the training rows (fully inside the training data)
    f = _lst(m, _bar_labels(m.index), profile)
    f = f[f.index <= train_end]
    hm = f["S"].groupby(f.index.hour).mean()
    return VolParams(profile=tuple(float(x) for x in profile),
                     s_hour_mean=tuple(float(hm.get(h, np.nan)) for h in range(24)),
                     train_start=m.index[0].to_pydatetime(),
                     train_end=train_end.to_pydatetime())


def transform(params: VolParams, m_1min: pd.DataFrame, bar_times=None) -> pd.DataFrame:
    """Research VolBlock.transform: [L, S, S_hd, T] at every hourly label of the
    input (or reindexed to bar_times)."""
    m = _check(m_1min)
    labels = _bar_labels(m.index)
    f = _lst(m, labels, np.asarray(params.profile, dtype=float))
    hm = np.asarray(params.s_hour_mean, dtype=float)[f.index.hour]
    f["S_hd"] = f["S"] - hm
    f = f[["L", "S", "S_hd", "T"]]
    if bar_times is not None:
        f = f.reindex(pd.DatetimeIndex(bar_times))
    return f


# ---------------------------------------------------------------- as of t

def frame_from_rows(rows: list[dict]) -> pd.DataFrame:
    """bars_before() rows -> OHLC frame on a UTC DatetimeIndex of bar opens."""
    idx = pd.DatetimeIndex(pd.to_datetime([r["bar_ts"] for r in rows], utc=True), name="bar_ts")
    return pd.DataFrame({c: [float(r[c]) for r in rows] for c in ("open", "high", "low", "close")},
                        index=idx)


@dataclass(frozen=True)
class VolState:
    t: datetime                   # the as-of time asked for
    label: datetime | None        # hourly label used: the latest one <= t
    L: float                      # NaN unless reason is None
    S_hd: float
    T: float
    close_ref: float              # close of the last bar ended by t (NaN if none)
    last_bar_ts: datetime | None  # open time of that bar
    reason: str | None            # None, "no_bars_before_t", "no_label",
                                  # "insufficient_history" or "nan_features"


def _utc_ts(t) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    if ts.tz is None:
        raise ValueError(f"naive timestamp {t!r}")
    return ts.tz_convert("UTC")


def state_at(params: VolParams, m_1min: pd.DataFrame, t) -> VolState:
    """L, S_hd, T as of t from 1-min bars. Bars that had not ended by t are
    ignored, whatever the frame holds."""
    t = _utc_ts(t)
    m = _check(m_1min)
    m = m[m.index + BAR <= t]
    nan = math.nan
    if m.empty:
        return VolState(t.to_pydatetime(), None, nan, nan, nan, nan, None, "no_bars_before_t")
    close_ref, last_ts = float(m["close"].iat[-1]), m.index[-1].to_pydatetime()
    f = transform(params, m)
    f = f[f.index <= t]
    if f.empty:
        return VolState(t.to_pydatetime(), None, nan, nan, nan, close_ref, last_ts, "no_label")
    i, label = len(f) - 1, f.index[-1].to_pydatetime()
    if i < WINDOWS[-1]:          # the longest window would reach row 0, which may be partial
        return VolState(t.to_pydatetime(), label, nan, nan, nan, close_ref, last_ts,
                        "insufficient_history")
    L, S_hd, T = (float(f[c].iat[i]) for c in ("L", "S_hd", "T"))
    if not all(math.isfinite(x) for x in (L, S_hd, T)):
        return VolState(t.to_pydatetime(), label, nan, nan, nan, close_ref, last_ts, "nan_features")
    return VolState(t.to_pydatetime(), label, L, S_hd, T, close_ref, last_ts, None)


def read_state(params: VolParams, symbol: str, t, *, db=None, kinds=DEFAULT_KINDS,
               known_by=None) -> VolState:
    """state_at on the store's bars_before(symbol, t, LOOKBACK, ...)."""
    rows = bars_before(symbol, _utc_ts(t).to_pydatetime(), LOOKBACK, db=db,
                       known_by=known_by, kinds=kinds)
    return state_at(params, frame_from_rows(rows), t)


# ---------------------------------------------------------------- sigma

@dataclass(frozen=True)
class Sigma:
    H: int                 # horizon, minutes
    sigma_ret: float       # log-return units
    sigma_px: float        # price units: close_ref × sigma_ret
    open_minutes: int      # minutes of [t, t+H) inside market hours
    crosses_close: bool    # the window holds closed minutes; gap risk is not in sigma


def sigma_over(params: VolParams, state: VolState, H: int) -> Sigma:
    """Sigma over the minutes opening in [state.t, state.t + H). Engineering
    decision, see the module docstring. Raises without a usable state."""
    if isinstance(H, bool) or not isinstance(H, int) or H <= 0:
        raise ValueError(f"H must be a positive int (minutes), got {H!r}")
    if state.reason is not None or not (math.isfinite(state.L) and math.isfinite(state.close_ref)):
        raise ValueError(f"no sigma without a valid state (reason {state.reason!r})")
    start = _utc_ts(state.t).ceil("1min").to_pydatetime()
    minutes = [start + timedelta(minutes=k) for k in range(H)]
    open_idx = pd.DatetimeIndex([u for u in minutes if _market_open(u)])
    if len(open_idx) == 0:
        open_idx = pd.DatetimeIndex([], tz="UTC")
    prof = np.asarray(params.profile, dtype=float)
    var = math.exp(state.L) * float(prof[_buckets(open_idx)].sum())
    sigma_ret = math.sqrt(var)
    return Sigma(H=H, sigma_ret=sigma_ret, sigma_px=state.close_ref * sigma_ret,
                 open_minutes=len(open_idx), crosses_close=len(open_idx) < H)
