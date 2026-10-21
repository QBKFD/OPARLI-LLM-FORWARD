"""Volatility block L, S, S_hd, T for live code, plus sigma over a horizon.

THE MATHS IS RESEARCH'S
    fit() and transform() are research/vol_block/vol_block.py (VolBlock), round 3
    definitions, copied operation for operation so the outputs are bit-identical
    on the same input (tests/datafeed/test_features_vol_block.py). research/vol_block stays
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

SIGMA OVER H MINUTES: sigma_over IS HAR (research/sigma_compare)
        var(t, H) = P(t, H) × exp(β · [1, L, S_hd, T]) × c
        P(t, H)   = Σ profile[bucket(u)] over the first H market-open minutes u >= t
        sigma_ret = √var,  sigma_px = close_ref × sigma_ret
    β and c per H come from a shipped model file (models/), whose SHA-256 is
    checked against a value pinned here on every load. Only H = 60 and 240 were
    fitted; any other H raises. The model carries its own VolParams: compute the
    state with model.params (state_at / read_state).
    Evidence: the pre-registered study research/sigma_compare (PREREG c75b24f)
    chose HAR over the plug-in (QLIKE -18 % / -16 % on 2007-2020, 24/24 folds).
    Post-hoc checks (its POSTHOC_RESULT.md): frozen on 2007-2020, HAR is still
    better on 2021-2023, and its calibration on 2022-2023 lies within [0.9, 1.1].
    Gap returns are dropped by the definitions above, so sigma holds no
    daily-break or weekend gap; `crosses_close` says the window spans a close.
    Whether such a trade is allowed is the caller's decision.
    Two fits ship, chosen by name or $DATAFEED_SIGMA_MODEL: parquet2021_2023
    (default: lower QLIKE on 2022-2023, the live data source) and txt2007_2023
    (2007-2023). See features/DECISIONS.md.
    The state must come from model.params: state_at stamps VolParams.sha256 on
    every VolState, and sigma_over raises when it differs from the model's.

    sigma_plugin is the former plug-in, an engineering decision (owner,
    2026-10-14): exp(L) × the profile summed over the market-open minutes of
    the calendar window [t, t+H), closed minutes counting zero. It is kept for
    monitoring and comparison. Its calibration check: tools/sigma_calibration/.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from backend.datafeed.ingest.xauusd_bars import SEAL_START, _market_open
from backend.datafeed.store.query import DEFAULT_KINDS, bars_before

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

    @property
    def sha256(self) -> str:
        """SHA-256 of to_json(): identifies these parameters. state_at stamps it
        on every VolState, and sigma_over checks it against the model's."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()

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
    params_sha256: str | None = None   # VolParams.sha256 of the params that computed it


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
    nan, h = math.nan, params.sha256

    def out(*fields) -> VolState:
        return VolState(t.to_pydatetime(), *fields, params_sha256=h)

    if m.empty:
        return out(None, nan, nan, nan, nan, None, "no_bars_before_t")
    close_ref, last_ts = float(m["close"].iat[-1]), m.index[-1].to_pydatetime()
    f = transform(params, m)
    f = f[f.index <= t]
    if f.empty:
        return out(None, nan, nan, nan, close_ref, last_ts, "no_label")
    i, label = len(f) - 1, f.index[-1].to_pydatetime()
    if i < WINDOWS[-1]:          # the longest window would reach row 0, which may be partial
        return out(label, nan, nan, nan, close_ref, last_ts, "insufficient_history")
    L, S_hd, T = (float(f[c].iat[i]) for c in ("L", "S_hd", "T"))
    if not all(math.isfinite(x) for x in (L, S_hd, T)):
        return out(label, nan, nan, nan, close_ref, last_ts, "nan_features")
    return out(label, L, S_hd, T, close_ref, last_ts, None)


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
    open_minutes: int      # market-open minutes the window covers (H for sigma_over)
    crosses_close: bool    # the window spans closed minutes; gap risk is not in sigma


def sigma_plugin(params: VolParams, state: VolState, H: int) -> Sigma:
    """The former plug-in: sigma over the minutes opening in [state.t, state.t + H).
    Engineering decision, see the module docstring. Raises without a usable state."""
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


# ---------------------------------------------------------------- sigma: HAR

MODELS_DIR = Path(__file__).resolve().parent / "models"
SIGMA_MODEL_ENV = "DATAFEED_SIGMA_MODEL"
# name -> (file, SHA-256 of the canonical JSON of every field except "sha256"),
# as research/sigma_compare wrote them. Pinned here, so a file that is edited
# and re-hashed still fails to load.
SIGMA_MODELS = {
    "txt2007_2023": ("sigma_har_txt2007_2023_v1.json",
                     "1af79da80ce8ae4de483ff09113150ed361e7d95221f00385e361f2bdbffc1cd"),
    "parquet2021_2023": ("sigma_har_parquet2021_2023_v1.json",
                         "dc0eb3c5fd31f1ee8f6e7a534b16930fbec453833aec9872b606afe963910dde"),
}
# The parquet fit (owner, 2026-10-15; features/DECISIONS.md #12): it has the lower
# QLIKE (the study's primary metric) on 2022-2023 and comes from the same data
# source as live; its calibration gap to the txt fit (0.022 vs 0.018) is within
# noise. The txt fit stays selectable: DATAFEED_SIGMA_MODEL=txt2007_2023.
DEFAULT_SIGMA_MODEL = "parquet2021_2023"
MAX_WINDOW_SEARCH = timedelta(days=7)      # longer than any closure in the calendar


@dataclass(frozen=True)
class SigmaModel:
    name: str
    version: int
    sha256: str
    params: VolParams          # compute the state with these
    horizons: dict             # H -> ((b0, bL, bS_hd, bT), smear_c)


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sigma_model_from_file(path, expected_sha256: str, name: str = "") -> SigmaModel:
    """Load a HAR model file. Raises unless the SHA-256 of its content equals both
    the value stated in the file and expected_sha256."""
    d = json.loads(Path(path).read_text())
    stated = d.pop("sha256", None)
    got = hashlib.sha256(_canonical(d)).hexdigest()
    if got != stated or got != expected_sha256:
        raise ValueError(f"{path}: SHA-256 check failed (content {got}, stated {stated}, "
                         f"expected {expected_sha256})")
    if d.get("model") != "sigma_har":
        raise ValueError(f"{path}: not a sigma_har model")
    horizons = {int(H): (tuple(float(b) for b in v["beta"]), float(v["smear_c"]))
                for H, v in d["horizons"].items()}
    if any(len(beta) != 4 for beta, _ in horizons.values()):
        raise ValueError(f"{path}: beta must have 4 values per horizon")
    return SigmaModel(name=name, version=int(d["version"]), sha256=got,
                      params=VolParams.from_json(json.dumps(d["vol_params"])), horizons=horizons)


def load_sigma_model(name: str | None = None) -> SigmaModel:
    """The shipped model `name`, else $DATAFEED_SIGMA_MODEL, else DEFAULT_SIGMA_MODEL."""
    name = name or os.environ.get(SIGMA_MODEL_ENV) or DEFAULT_SIGMA_MODEL
    if name not in SIGMA_MODELS:
        raise ValueError(f"unknown sigma model {name!r}; choose from {sorted(SIGMA_MODELS)}")
    file, sha = SIGMA_MODELS[name]
    return sigma_model_from_file(MODELS_DIR / file, sha, name)


def open_window(t, H: int) -> tuple[list[datetime], datetime]:
    """The first H market-open minutes opening at or after t, and the window's
    end (the last of them + 1 min)."""
    u = _utc_ts(t).ceil("1min").to_pydatetime()
    stop, out = u + MAX_WINDOW_SEARCH, []
    while len(out) < H:
        if u >= stop:
            raise ValueError(f"fewer than {H} open minutes within {MAX_WINDOW_SEARCH} of {t}")
        if _market_open(u):
            out.append(u)
        u += timedelta(minutes=1)
    return out, out[-1] + timedelta(minutes=1)


def sigma_over(model: SigmaModel, state: VolState, H: int) -> Sigma:
    """HAR sigma over the next H market-open minutes from state.t (see the
    module docstring). H must be a fitted horizon (60 or 240). The state must
    come from state_at / read_state with model.params: its params hash is
    checked. Raises without a usable state."""
    if isinstance(H, bool) or not isinstance(H, int) or H not in model.horizons:
        raise ValueError(f"H must be one of {sorted(model.horizons)} (minutes), got {H!r}")
    if state.params_sha256 != model.params.sha256:
        raise ValueError(f"state was computed with other VolParams (params hash "
                         f"{state.params_sha256!r}, model {model.name!r} needs "
                         f"{model.params.sha256!r}); use state_at / read_state with model.params")
    if state.reason is not None or not all(
            math.isfinite(x) for x in (state.L, state.S_hd, state.T, state.close_ref)):
        raise ValueError(f"no sigma without a valid state (reason {state.reason!r})")
    beta, c = model.horizons[H]
    minutes, end = open_window(state.t, H)
    prof = np.asarray(model.params.profile, dtype=float)
    P = float(prof[_buckets(pd.DatetimeIndex(minutes))].sum())
    var = P * math.exp(sum(b * x for b, x in zip(beta, (1.0, state.L, state.S_hd, state.T)))) * c
    sigma_ret = math.sqrt(var)
    start = _utc_ts(state.t).ceil("1min").to_pydatetime()
    return Sigma(H=H, sigma_ret=sigma_ret, sigma_px=state.close_ref * sigma_ret, open_minutes=H,
                 crosses_close=(end - start) > timedelta(minutes=H))
