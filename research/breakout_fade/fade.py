"""research/breakout_fade: PREREG.md B1-B20 on top of research/pullback_structure's
pipeline, used unchanged (B3): its candles, swings, state machine and labels come
from pullback_structure/pullback.py and its definitional constants from
pullback_structure/params.py. Only what this study adds lives here: the H2 fade
trade (B7-B9), the break-even spread (B10), the short-day exclusion (B16), the
H1 one-sided test (B6), the verdict (B13) and the descriptive moves. Aggregates
only: nothing here prints or returns a price row.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
PS = HERE.parent / "pullback_structure"
for _p in (str(HERE), str(PS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import params as P0  # noqa: E402  (pullback_structure/params.py: the definitions, B3)
import pullback as pb  # noqa: E402  (pullback_structure/pullback.py: the pipeline, B3)

import fade_params as F  # noqa: E402

RUN = dict(k=P0.K, x_sigma=P0.X_SIGMA, w=P0.W, horizon=P0.HORIZON, w_alt=P0.W_ALT, n_swings=P0.N_SWINGS)


def trade_dates(index) -> np.ndarray:
    """New York trade date (the session from 18:00 NY) of each timestamp, as day numbers."""
    et = pd.DatetimeIndex(index).tz_convert("America/New_York").tz_localize(None)
    return np.asarray((et + pd.Timedelta(hours=6)).normalize().asi8 // (86400 * 10**9))


def short_dates(m: pd.DataFrame, min_rows: int) -> set:
    """B16: trade dates with fewer than `min_rows` one-minute rows."""
    td = trade_dates(m.index)
    u, n = np.unique(td, return_counts=True)
    return {int(d) for d, c in zip(u, n) if c < min_rows}


def first_passage_le_ge(s: pb.Series, r0: int, r1: int, target: float, stop: float) -> tuple[str, int]:
    """B8: 'hit' if a 1-min low <= target comes strictly before any high >= stop;
    'stop' if the stop comes first or in the same bar; 'none' if neither."""
    lo = s.ml[r0:r1] <= target
    hi = s.mh[r0:r1] >= stop
    a = int(np.argmax(lo)) if lo.any() else -1
    b = int(np.argmax(hi)) if hi.any() else -1
    if a < 0 and b < 0:
        return "none", -1
    if a >= 0 and (b < 0 or a < b):
        return "hit", r0 + a
    return "stop", r0 + b


def h2_trade(s: pb.Series, e: pb.Event, horizon: int) -> dict:
    """B7-B9, in the event's bear view (a bull event's prices are negated): enter
    against the break at its close, target = the swing low, stop = entry + 1 sigma
    (sigma_240 as of the break close)."""
    n = len(s.c)
    b = e.brk
    entry = float(s.c[b])
    stop_d = F.STOP_SIGMA * e.sigma_brk
    target_d = entry - e.sl
    p = stop_d / (stop_d + target_d)
    last = b + horizon
    kind, _ = first_passage_le_ge(s, s.i1[b], s.i1[min(last, n - 1)], e.sl, entry + stop_d)
    if kind == "none":
        return {"h2": "truncated" if last >= n else "unresolved", "p": p, "target_sigma": target_d / e.sigma_brk}
    return {"h2": kind, "hit": int(kind == "hit"), "p": p, "target_sigma": target_d / e.sigma_brk}


def move_sigma(s: pb.Series, e: pb.Event, k: int) -> float | None:
    """Descriptive: the close k candles after the break minus the break close, in
    sigma, in the break's direction (positive = follow-through)."""
    j = e.brk + k
    if j >= len(s.c):
        return None
    return float((s.c[j] - s.c[e.brk]) / e.sigma_brk)


def collect(cd: pd.DataFrame, mt, mh, ml, sig, short: set, events_from_ns: int) -> tuple[pd.DataFrame, dict]:
    """One row per Q2 break (B or C) that isn't truncated, with its H1 label, its H2
    trade, the short-day flag (B16: the break candle, the W window or the H2 horizon
    touches a short trade date) and the descriptive moves."""
    starts = pd.DatetimeIndex(cd["start"])
    ctd = trade_dates(starts)
    rows, refused = [], {}
    for bull in (False, True):
        s = pb.series(cd, mt, mh, ml, sig, bull)
        evs, cnt = pb.run_events(s, events_from_ns=events_from_ns, **RUN)
        refused["bull" if bull else "bear"] = cnt
        for e in evs:
            if e.kind == "truncated" or e.bc not in ("B", "C"):
                continue
            span = ctd[e.brk:min(len(ctd), e.brk + max(P0.W, P0.HORIZON) + 1)]
            rows.append({"dir": "bull" if bull else "bear", "bc": e.bc, "is_b": e.bc == "B", "month": e.q2_month,
                         "brk_from": e.brk_from, "short": bool(any(int(d) in short for d in span)),
                         **{f"bc_w{w}": e.bc_w.get(w) for w in P0.W_ALT}, **h2_trade(s, e, P0.HORIZON),
                         "move5": move_sigma(s, e, 5), "move30": move_sigma(s, e, 30)})
    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["dir", "bc", "is_b", "month", "short", "h2", "hit", "p", "target_sigma"])
    return df, refused


# ----------------------------------------------------------------- statistics


def h1_test(month: np.ndarray, is_b: np.ndarray, null: float, b: int, seed: int) -> dict:
    """B6: the difference (share B - the random walk's) with its 95 % CI, and the
    one-sided p for difference < 0 (the share of resamples with difference >= 0, +1)."""
    rng = np.random.default_rng(seed)
    bs = pb.boot_mean(month, is_b.astype(float), b, rng) - null
    return {"n": int(len(is_b)), "B": int(is_b.sum()), "share_B": float(is_b.mean()), "random_walk_share_B": float(null),
            "difference": float(is_b.mean() - null),
            "ci95": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))],
            "p_one_sided": float(((bs >= 0).sum() + 1) / (b + 1))}


def h2_test(month: np.ndarray, hit: np.ndarray, p: np.ndarray, b: int, seed: int) -> dict:
    """B9: mean(hit - P), one-sided > 0 (pullback.excess_test)."""
    r = pb.excess_test(month, hit - p, b, seed)
    return {**r, "hit_rate": float(hit.mean()), "mean_P": float(p.mean())}


def break_even_sigma(hit: np.ndarray, target_sigma: np.ndarray) -> float:
    """B10: the spread, in sigma, at which the mean fade payoff is zero: the mean
    gross payoff hit * target/sigma - (1 - hit) * stop/sigma (stop = STOP_SIGMA)."""
    return float(np.mean(hit * target_sigma - (1 - hit) * F.STOP_SIGMA))


def verdict(h1: dict, bh: dict, min_events: int) -> str:
    """B13, on H1."""
    if h1.get("n", 0) < min_events:
        return "INCONCLUSIVE (too few breaks)"
    lo, hi = h1["ci95"]
    if h1["difference"] < 0 and hi < 0 and bh["H1"]["p_adj"] < F.BH_Q:
        return "PASS"
    if lo > -F.FAIL_LOWER:
        return "FAIL"
    return "INCONCLUSIVE"
