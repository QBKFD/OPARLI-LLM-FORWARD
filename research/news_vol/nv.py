"""research/news_vol (Study A): pure functions (no data access).

Events, window flags, the two forecast arms' OLS, QLIKE, Diebold-Mariano,
the month-block bootstrap, BH and the verdict rule. PREREG.md sections in
brackets.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

MIN_NS = 60_000_000_000


# ----------------------------------------------------------------------------- events [D2]

def group_events(rows: pd.DataFrame, data_events, fomc_events) -> pd.DataFrame:
    """Calendar rows (ts_utc, event) -> one row per release minute: type FOMC if any
    of the minute's rows is an FOMC event, else data. Columns: type, n_rows, events."""
    if rows["ts_utc"].isna().any():
        raise ValueError("untimed rows must be dropped before grouping")
    unknown = set(rows["event"]) - set(data_events) - set(fomc_events)
    if unknown:
        raise ValueError(f"events outside the set: {sorted(unknown)}")
    ts = pd.DatetimeIndex(rows["ts_utc"]).tz_convert("UTC")
    if (ts != ts.floor("1min")).any():
        raise ValueError("release times must be whole minutes")
    r = pd.DataFrame({"ts": ts, "event": rows["event"].to_numpy()})
    g = r.groupby("ts")["event"]
    out = pd.DataFrame({
        "type": g.apply(lambda e: "FOMC" if e.isin(fomc_events).any() else "data"),
        "n_rows": g.size(),
        "events": g.apply(lambda e: "|".join(sorted(set(e)))),
    })
    out.index.name = "ts"
    return out.sort_index()


def in_window(labels_ns: np.ndarray, ev_ns: np.ndarray, H: int) -> np.ndarray:
    """[D4] True where a release falls in the window of origin t: its reaction bar (the
    1-min bar opening at the release minute e) closes in (t, t+H], i.e. t <= e < t+H."""
    ev = np.sort(np.asarray(ev_ns, dtype=np.int64))
    t = np.asarray(labels_ns, dtype=np.int64)
    return (np.searchsorted(ev, t + H * MIN_NS, "left") - np.searchsorted(ev, t, "left")) > 0


def in_window_literal(labels_ns: np.ndarray, ev_ns: np.ndarray, H: int) -> np.ndarray:
    """The release instant itself in (t, t+H]: t < e <= t+H. Counted only (DECISIONS N2)."""
    ev = np.sort(np.asarray(ev_ns, dtype=np.int64))
    t = np.asarray(labels_ns, dtype=np.int64)
    return (np.searchsorted(ev, t + H * MIN_NS, "right") - np.searchsorted(ev, t, "right")) > 0


# ----------------------------------------------------------------------------- arms [D5]

def fit_smeared(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    """OLS of y = ln(RV / P) on X, and Duan's smearing factor c = mean exp(residual)."""
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return beta, float(np.mean(np.exp(y - X @ beta)))


def qlike(rv, h):
    q = np.asarray(rv, dtype=float) / np.asarray(h, dtype=float)
    return q - np.log(q) - 1.0


# ----------------------------------------------------------------------------- inference

def newey_west_lrv(d: np.ndarray, lag: int) -> float:
    """Bartlett long-run variance (research/vol_features/run_study.py, the sigma_compare DM)."""
    u = np.asarray(d, dtype=float) - np.mean(d)
    T = len(u)
    lrv = u @ u / T
    for k in range(1, lag + 1):
        lrv += 2.0 * (1.0 - k / (lag + 1.0)) * (u[k:] @ u[:-k]) / T
    return float(lrv)


def dm_two_sided(loss_x: np.ndarray, loss_base: np.ndarray, lag: int) -> dict:
    """[D7] d = loss_x - loss_base in time order; mean d < 0: X lower. Two-sided normal p."""
    d = np.asarray(loss_x, dtype=float) - np.asarray(loss_base, dtype=float)
    lrv = newey_west_lrv(d, lag)
    stat = float(d.mean() / np.sqrt(lrv / len(d))) if lrv > 0 else 0.0
    return {"n": int(len(d)), "mean_d": float(d.mean()), "stat": stat,
            "p": float(2.0 * stats.norm.sf(abs(stat)))}


def month_codes(t: pd.DatetimeIndex) -> np.ndarray:
    t = pd.DatetimeIndex(t).tz_convert("UTC")
    return np.asarray(t.year * 12 + t.month - 1)


def _month_draws(months: np.ndarray, B: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Calendar-month block bootstrap: (unique months, B x M draw counts)."""
    uniq, inv = np.unique(months, return_inverse=True)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(uniq), size=(B, len(uniq)))
    w = np.zeros((B, len(uniq)))
    np.add.at(w, (np.repeat(np.arange(B), len(uniq)), idx.ravel()), 1.0)
    return inv, w


def boot_diff(y: np.ndarray, rel: np.ndarray, months: np.ndarray, B: int, seed: int) -> dict:
    """[D6] mean y over rel rows minus mean y over the others. Month-block bootstrap:
    95 % percentile CI; one-sided p for > 0 = (1 + #{diff* <= 0}) / (B + 1)."""
    y, rel = np.asarray(y, dtype=float), np.asarray(rel, dtype=bool)
    est = float(y[rel].mean() - y[~rel].mean())
    inv, w = _month_draws(months, B, seed)
    M = w.shape[1]
    s1 = np.bincount(inv, weights=np.where(rel, y, 0.0), minlength=M)
    n1 = np.bincount(inv, weights=rel.astype(float), minlength=M)
    s0 = np.bincount(inv, weights=np.where(rel, 0.0, y), minlength=M)
    n0 = np.bincount(inv, weights=(~rel).astype(float), minlength=M)
    with np.errstate(invalid="ignore", divide="ignore"):
        star = (w @ s1) / (w @ n1) - (w @ s0) / (w @ n0)
    ok = np.isfinite(star)
    star = star[ok]
    return {"estimate": est, "ci95": [float(np.quantile(star, 0.025)), float(np.quantile(star, 0.975))],
            "p_one_sided_gt0": float((1 + np.sum(star <= 0)) / (len(star) + 1)),
            "n_rel": int(rel.sum()), "n_other": int((~rel).sum()), "B_used": int(ok.sum()),
            "months": int(M)}


def spearman(x: np.ndarray, z: np.ndarray) -> float:
    return float(stats.spearmanr(x, z).statistic)


def boot_spearman(x: np.ndarray, z: np.ndarray, months: np.ndarray, B: int, seed: int) -> dict:
    """[D8] Spearman rho with a month-block bootstrap: 95 % percentile CI and a two-sided
    p = min(1, 2 min(1 + #{rho* <= 0}, 1 + #{rho* >= 0}) / (B + 1))."""
    x, z = np.asarray(x, dtype=float), np.asarray(z, dtype=float)
    est = spearman(x, z)
    uniq, inv = np.unique(months, return_inverse=True)
    rows = [np.flatnonzero(inv == k) for k in range(len(uniq))]
    rng = np.random.default_rng(seed)
    star = np.empty(B)
    for b in range(B):
        pick = np.concatenate([rows[k] for k in rng.integers(0, len(uniq), size=len(uniq))])
        xs, zs = x[pick], z[pick]
        star[b] = np.corrcoef(stats.rankdata(xs), stats.rankdata(zs))[0, 1] if len(pick) > 2 else np.nan
    star = star[np.isfinite(star)]
    lo, hi = np.sum(star <= 0), np.sum(star >= 0)
    return {"estimate": est, "ci95": [float(np.quantile(star, 0.025)), float(np.quantile(star, 0.975))],
            "p_two_sided": float(min(1.0, 2.0 * (1 + min(lo, hi)) / (len(star) + 1))),
            "n": int(len(x)), "B_used": int(len(star)), "months": int(len(uniq))}


def bh(p) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values (NaN stays NaN)."""
    p = np.asarray(p, dtype=float)
    out = np.full(len(p), np.nan)
    ok = np.flatnonzero(np.isfinite(p))
    if not len(ok):
        return out
    order = ok[np.argsort(p[ok])]
    m = len(order)
    adj = p[order] * m / np.arange(1, m + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out[order] = np.minimum(adj, 1.0)
    return out


def verdict(mean_d_240: float, bh_240: float, mean_d_60: float, bh_60: float, q: float) -> tuple[str, str]:
    """[D7] PASS: the augmented arm lower at H = 240 with BH p < q, and not significantly
    worse at H = 60 (worse = mean d > 0 with BH p < q). Otherwise FAIL."""
    better_240 = mean_d_240 < 0 and bh_240 < q
    worse_60 = mean_d_60 > 0 and bh_60 < q
    if better_240 and not worse_60:
        return "PASS", "augmented QLIKE lower at H = 240 (BH p < q) and not significantly worse at H = 60"
    why = []
    if not better_240:
        why.append("not significantly lower at H = 240" if mean_d_240 < 0 else "not lower at H = 240")
    if worse_60:
        why.append("significantly worse at H = 60")
    return "FAIL", "; ".join(why)
