"""Variance ratios for research/variance_ratio (EXPLORATORY, descriptive).

No value lives here; vr_params.py has them. Pure functions on arrays and
1-min bar frames; nothing here reads a file.

Returns live on a regular slot grid (one slot per base bin, 1 h or 5 min).
r[t] is the log return from the close of bin t-1 to the close of bin t, or
NaN when excluded. A window is k consecutive valid slots; a cell is a set of
window starts (all, one UTC hour, a session, ...).

For a cell with window starts s in I, the demeaned returns e (global mean of
the market's valid returns), and m_j(t) = #{s in I : t-k+1 <= s <= t-j}, the
number of the cell's windows that hold both t and t-j:

    S   = sum_t m_0(t) e_t^2                 (k * W * the 1-period variance)
    C_j = sum_t m_j(t) e_t e_{t-j}
    VR  = sum_windows (window sum)^2 / S = 1 + 2 sum_{j=1}^{k-1} C_j / S

which is Var(k-period) / (k Var(1-period)) over the cell's windows and their
returns (Lo & MacKinlay 1988, overlapping). Under a martingale difference null
with heteroskedasticity, the products e_t e_{t-j} are uncorrelated across t
and j, so

    Var(VR) = (2 / S)^2 sum_j sum_t m_j(t)^2 e_t^2 e_{t-j}^2,   z = (VR - 1) / sqrt(Var(VR)).

On a contiguous series with every start in I, m_j = k - j inside and this is
Lo-MacKinlay's z*(q) with theta = sum_j (2(k-j)/k)^2 delta_j.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

NS_MIN = 60_000_000_000


# --------------------------------------------------------------------------- returns on the slot grid

def trade_dates(index: pd.DatetimeIndex, roll_ny_hour: int) -> pd.DatetimeIndex:
    """The trade date of each timestamp: the New York date, rolled at roll_ny_hour."""
    ny = index.tz_convert("America/New_York")
    return (ny + pd.Timedelta(hours=24 - roll_ny_hour)).tz_localize(None).normalize()


def slot_returns(m: pd.DataFrame, base_min: int, tail_min: int, gap_min: int, short_day_rows: int,
                 roll_ny_hour: int, seam: pd.Timestamp | None, start: pd.Timestamp, end_excl: pd.Timestamp
                 ) -> tuple[pd.DatetimeIndex, np.ndarray, pd.Series]:
    """(slot start times, log returns with NaN where excluded, exclusion counts).

    Slot t covers [T_t, T_t + base). Its return is log(C_t / C_{t-1}), C the
    close of the bin's last 1-min bar. It is valid only if:
    - both bins have a last bar in their final tail_min minutes;
    - no gap of more than gap_min minutes lies between two consecutive 1-min
      bars from C_{t-1} to C_t;
    - neither close's trade date is short (< short_day_rows rows);
    - the return doesn't straddle the gold seam.
    Every other slot gets one reason, the first that applies, judged on both
    bins it spans: seam, short_day, weekend (Friday 17:00 to Sunday 17:00 New
    York), daily_break (the New York hour from 17:00 on a weekday), holiday (a
    trade date without any bar), gap.
    """
    idx = m.index
    if len(idx) and ((idx < start).any() or (idx >= end_excl).any()):
        raise AssertionError("slot_returns: bars outside [start, end_excl)")
    base = pd.Timedelta(minutes=base_min)
    bins = idx.floor(f"{base_min}min")
    last = ~pd.Series(bins).duplicated(keep="last").to_numpy()             # the last bar of each bin
    b_t, b_ts, b_c = bins[last], idx[last], m["close"].to_numpy(float)[last]
    b_bar = np.flatnonzero(last)                                           # its position among the bars
    slots = pd.date_range(start, end_excl, freq=f"{base_min}min", inclusive="left")
    pos = np.full(len(slots), -1)
    pos[slots.get_indexer(b_t)] = np.arange(len(b_t))
    has = pos >= 0
    tail_ok = np.zeros(len(slots), bool)
    tail_ok[has] = np.asarray(b_ts[pos[has]] >= b_t[pos[has]] + base - pd.Timedelta(minutes=tail_min))
    big = np.diff(idx.asi8, prepend=idx.asi8[:1]) > gap_min * NS_MIN       # a gap ends at this bar
    CG = np.concatenate([[0], np.cumsum(big)])
    td = trade_dates(idx, roll_ny_hour)
    rows_per_td = pd.Series(1, index=td).groupby(level=0).size()
    short_bar = rows_per_td.reindex(td).to_numpy() < short_day_rows

    t = np.flatnonzero(has[1:] & has[:-1] & tail_ok[1:] & tail_ok[:-1]) + 1
    i0, i1 = b_bar[pos[t - 1]], b_bar[pos[t]]
    gap_x = (CG[i1 + 1] - CG[i0 + 1]) > 0                                  # bars i0+1 .. i1
    short = short_bar[i0] | short_bar[i1]
    seam_x = (idx[i0] < seam) & (idx[i1] >= seam) if seam is not None else np.zeros(len(t), bool)
    good = ~gap_x & ~short & ~seam_x
    r = np.full(len(slots), np.nan)
    r[t[good]] = np.log(b_c[pos[t[good]]] / b_c[pos[t[good] - 1]])

    reason = np.full(len(slots), "", dtype=object)
    reason[t[good]] = "valid"
    reason[0] = "first_slot"
    reason[t[seam_x]] = "seam"
    reason[t[~seam_x & short]] = "short_day"
    ny = slots.tz_convert("America/New_York")
    wd, hr = ny.dayofweek.to_numpy(), ny.hour.to_numpy()
    wk = ((wd == 4) & (hr >= roll_ny_hour)) | (wd == 5) | ((wd == 6) & (hr < roll_ny_hour))
    brk = ~wk & (hr == roll_ny_hour)
    hol = ~wk & ~np.isin(trade_dates(slots, roll_ny_hour), rows_per_td.index)
    prev = lambda x: np.concatenate([[False], x[:-1]])                     # noqa: E731
    for name, flag in (("weekend", wk | prev(wk)), ("daily_break", brk | prev(brk)), ("holiday", hol | prev(hol))):
        reason[(reason == "") & flag] = name
    reason[reason == ""] = "gap"
    counts = pd.Series(reason).value_counts()
    for name in ("valid", "seam", "short_day", "weekend", "daily_break", "holiday", "gap", "first_slot"):
        counts[name] = int(counts.get(name, 0))
    return slots, r, counts


# --------------------------------------------------------------------------- the estimator

def window_starts(r: np.ndarray, k: int) -> np.ndarray:
    """True at s when r[s .. s+k-1] are all valid."""
    v = np.isfinite(r).astype(int)
    c = np.concatenate([[0], np.cumsum(v)])
    s = np.zeros(len(r), bool)
    if len(r) >= k:
        s[: len(r) - k + 1] = (c[k:] - c[:-k]) == k
    return s


def vr_cell(e: np.ndarray, starts: np.ndarray, k: int) -> dict:
    """VR, z, p (two-sided) and n (windows) for the cell `starts` (boolean over
    slots; each start must be a valid window). e: demeaned returns, 0 where invalid."""
    W = int(starts.sum())
    if W < 2 or k < 2:
        return {"vr": np.nan, "z": np.nan, "p": np.nan, "n": W}
    P = np.concatenate([[0], np.cumsum(starts.astype(np.int64))])
    n = len(e)
    t = np.arange(n)

    def m(j):                                    # #starts in [t-k+1, t-j]
        a, b = np.clip(t - k + 1, 0, n), np.clip(t - j, -1, n - 1)
        out = P[b + 1] - P[a]
        return np.where(b >= a, out, 0)

    e2 = e * e
    S = float((m(0) * e2).sum())
    if S <= 0:
        return {"vr": np.nan, "z": np.nan, "p": np.nan, "n": W}
    num, var = 0.0, 0.0
    for j in range(1, k):
        mj = m(j)[j:]
        prod = e[j:] * e[:-j]
        num += float((mj * prod).sum())
        var += float((mj.astype(float) ** 2 * e2[j:] * e2[:-j]).sum())
    vr = 1.0 + 2.0 * num / S
    se = 2.0 / S * np.sqrt(var)
    z = (vr - 1.0) / se if se > 0 else np.nan
    return {"vr": vr, "z": z, "p": float(2 * norm.sf(abs(z))) if np.isfinite(z) else np.nan, "n": W}


def vr_direct(r: np.ndarray, starts: np.ndarray, k: int, mu: float) -> float:
    """The same VR straight from the definition (tests): mean squared demeaned
    window sum over k times the mean squared demeaned return in those windows."""
    s = np.flatnonzero(starts)
    sums = np.array([(r[i:i + k] - mu).sum() for i in s])
    sq = np.array([((r[i:i + k] - mu) ** 2).mean() for i in s])
    return float((sums ** 2).mean() / (k * sq.mean()))


def bh(p: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values; NaN stays NaN."""
    p = np.asarray(p, float)
    out = np.full(len(p), np.nan)
    ok = np.isfinite(p)
    q = p[ok]
    if not len(q):
        return out
    o = np.argsort(q)
    ranked = q[o] * len(q) / np.arange(1, len(q) + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    res = np.empty(len(q))
    res[o] = np.minimum(adj, 1.0)
    out[ok] = res
    return out


# --------------------------------------------------------------------------- cells

def session_of(hours: np.ndarray, sessions) -> np.ndarray:
    out = np.full(len(hours), "", dtype=object)
    for name, a, b in sessions:
        sel = (hours >= a) & (hours < b) if a < b else (hours >= a) | (hours < b)
        out[sel] = name
    return out
