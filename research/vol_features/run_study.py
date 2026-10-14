#!/usr/bin/env python3
"""
Volatility feature reduction study for the regime classifier.

Tests whether 3 pre-registered volatility features (level / term structure /
shape) carry the same out-of-sample information about future realised variance
as the 13 volatility features in regime_classifier/regime_features.py.

Specification: PREREG.md (committed before any result). Choices the spec left
open: DECISIONS.md. Research only: nothing in regime_classifier/ or backend/ is
modified.

Run from the repo root or anywhere:
    backend/venv/bin/python3 research/vol_features/run_study.py
Outputs go to research/vol_features/results/ and research/vol_features/figures/.
"""

from __future__ import annotations

import importlib
import json
import sys
import time
import types
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster.hierarchy import dendrogram, fcluster, leaves_list, linkage
from scipy.spatial.distance import squareform

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "database" / "ohlcv_data" / "XAUUSD_1min_UTC_clean.parquet"
OUT = HERE / "results"
FIG = HERE / "figures"

SEAL = pd.Timestamp("2024-01-01", tz="UTC")
WARMUP_START = pd.Timestamp("2020-01-01", tz="UTC")
ANALYSIS_START = pd.Timestamp("2021-01-01", tz="UTC")
GAP = pd.Timedelta(minutes=5)
EMBARGO = pd.Timedelta(hours=24)
COVERAGE = 0.90
HORIZONS = {"y4": 240, "y24": 1440}          # minutes
NW_LAG = {"y4": 8, "y24": 48}                # rows (1H bars)
QUARTER_EDGES = pd.date_range("2022-01-01", periods=9, freq="QS", tz="UTC")
CLUSTER_CUT = 0.2
D2_MAX_RHO = 0.5
ISOLATED_RHO = 0.5

NEW = ["log_gk60", "log_rv_ratio", "wick60"]
EXISTING = [
    "atr", "atr_ratio", "atr_percentile", "bb_width", "bb_width_percentile",
    "parkinson_vol", "rv_15", "rv_60", "rv_240", "rv_ratio_short_long",
    "rv_ratio_mid_long", "rv_ratio_short_mid", "rv_adjusted",
]
HAR = ["har_1h", "har_24h", "har_120h"]
ALL16 = EXISTING + NEW
MODELS = {
    "M0": HAR,
    "M1": EXISTING,
    "M2": NEW,
    "M3": HAR + NEW,
    "M4": HAR + EXISTING,
}
CONCEPT = {  # DECISIONS.md #14, fixed before any correlation was computed
    **{f: "LEVEL" for f in ["atr", "bb_width", "parkinson_vol", "rv_15", "rv_60",
                            "rv_240", "rv_adjusted", "log_gk60"]},
    **{f: "TERM" for f in ["atr_ratio", "atr_percentile", "bb_width_percentile",
                           "rv_ratio_short_long", "rv_ratio_mid_long",
                           "rv_ratio_short_mid", "log_rv_ratio"]},
    "wick60": "SHAPE",
}
# (A, B) pairs: d = e²_A - e²_B, p = one-sided "A better". D1 = (M1, M2), D3 = (M3, M0).
DM_PAIRS = [("M1", "M2"), ("M3", "M0"), ("M2", "M0"), ("M1", "M0"),
            ("M4", "M3"), ("M4", "M0")]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# =============================================================================
# Sealing
# =============================================================================

def assert_sealed(obj, what: str = "frame") -> None:
    """Fail if any row at or after 2024-01-01 is present."""
    idx = obj.index if hasattr(obj, "index") else pd.DatetimeIndex(obj)
    n_bad = int((idx >= SEAL).sum()) if len(idx) else 0
    if n_bad:
        raise AssertionError(
            f"SEALED DATA: {what} has {n_bad} row(s) at or after {SEAL.date()}"
        )


# =============================================================================
# Data
# =============================================================================

def load_minutes(path: Path = DATA, start=WARMUP_START, end=SEAL):
    """1-min bars with start <= timestamp < end, indexed by open time (UTC)."""
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_parquet(
        path, filters=[("timestamp", ">=", start), ("timestamp", "<", end)]
    )
    df = df.set_index("timestamp").sort_index()
    info = {"rows_read": len(df)}
    dup = df.index.duplicated(keep="first")
    px = df[["open", "high", "low", "close"]]
    invalid = ~((px > 0).all(axis=1) & (df["high"] >= df["low"]))
    inconsistent = (df["high"] < px[["open", "close"]].max(axis=1)) | (
        df["low"] > px[["open", "close"]].min(axis=1))
    info.update(duplicates=int(dup.sum()), invalid_dropped=int((invalid & ~dup).sum()),
                ohlc_inconsistent_kept=int((inconsistent & ~invalid & ~dup).sum()))
    df = df[~dup & ~invalid]
    assert_sealed(df, "1-min data")
    info.update(rows=len(df), first=str(df.index.min()), last=str(df.index.max()))
    return df, info


def minute_returns(m: pd.DataFrame):
    """1-min log returns, NaN where the 1-min bar follows a gap > 5 min."""
    dt = m.index.to_series().diff()
    flag = (dt > GAP) | dt.isna()
    r = np.log(m["close"]).diff()
    r[flag.values] = np.nan
    return r, pd.Series(flag.values, index=m.index)


def hourly_bars(m: pd.DataFrame, flag: pd.Series) -> pd.DataFrame:
    """1H bars labelled by close time: 1-min bars with open time in [t-1h, t)."""
    kw = dict(rule="1h", label="right", closed="left")
    bars = pd.DataFrame({
        "open": m["open"].resample(**kw).first(),
        "high": m["high"].resample(**kw).max(),
        "low": m["low"].resample(**kw).min(),
        "close": m["close"].resample(**kw).last(),
        "n_min": m["close"].resample(**kw).count(),
        "has_gap": flag.astype(float).resample(**kw).max(),
    })
    bars = bars[bars["n_min"] > 0].copy()
    prev_ok = bars.index.to_series().diff() == pd.Timedelta(hours=1)
    r1h = np.log(bars["close"]).diff()
    r1h[(~prev_ok).values | (bars["has_gap"] > 0).values] = np.nan
    bars["r1h"] = r1h
    return bars


def window_rv(minute_index: pd.DatetimeIndex, r: pd.Series, starts, ends):
    """Sum of r² and count of valid returns for 1-min bars with open in [start, end)."""
    rv = r.to_numpy()
    valid = ~np.isnan(rv)
    cs = np.concatenate([[0.0], np.cumsum(np.where(valid, rv * rv, 0.0))])
    cn = np.concatenate([[0], np.cumsum(valid)])
    t = minute_index.asi8
    i0 = np.searchsorted(t, np.asarray(starts), side="left")
    i1 = np.searchsorted(t, np.asarray(ends), side="left")
    return cs[i1] - cs[i0], cn[i1] - cn[i0]


def _safe_log(x):
    x = np.asarray(x, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(x > 0, np.log(np.where(x > 0, x, 1.0)), np.nan)


# =============================================================================
# Features
# =============================================================================

def new_features(bars: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = (bars[k] for k in ("open", "high", "low", "close"))
    f = pd.DataFrame(index=bars.index)

    gk = 0.5 * np.log(h / l) ** 2 - (2 * np.log(2) - 1) * np.log(c / o) ** 2
    gk = gk.clip(lower=1e-12)
    f["log_gk60"] = np.log(gk.rolling(60, min_periods=60).mean())

    r2 = bars["r1h"] ** 2
    rv15 = np.sqrt(r2.rolling(15, min_periods=max(5, 15 // 2)).sum())
    rv240 = np.sqrt(r2.rolling(240, min_periods=max(5, 240 // 2)).sum())
    valid = (rv15 > 0) & (rv240 > 0)
    f["log_rv_ratio"] = np.log((rv15 / rv240).where(valid))

    rng = h - l
    num = (rng - (c - o).abs()).rolling(60, min_periods=60).sum()
    den = rng.rolling(60, min_periods=60).sum()
    f["wick60"] = num / den.where(den > 0)
    return f


def _import_feature_engine():
    """Import regime_features without running regime_classifier/__init__.py.

    __init__ pulls in jumpmodels/sklearn/joblib (not installed). A bare package
    object pointing at the real directory lets the unmodified module and its
    relative import of regime_config load normally (DECISIONS.md #4).
    """
    name = "regime_classifier"
    pkg = sys.modules.get(name)
    if pkg is None or not hasattr(pkg, "__path__"):
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(ROOT / name)]
        sys.modules[name] = pkg
    rf = importlib.import_module(f"{name}.regime_features")
    rc = importlib.import_module(f"{name}.regime_config")
    return rf, rc


def existing_features(bars: pd.DataFrame) -> pd.DataFrame:
    """The 13-feature volatility block of RegimeFeatureEngine.compute(), unmodified."""
    rf, rc = _import_feature_engine()
    cfg = rc.RegimeConfig()
    eng = rf.RegimeFeatureEngine(config=cfg)
    df = eng._normalize_columns(bars[["open", "high", "low", "close"]])
    log_returns = np.log(df["close"] / df["close"].shift(1))

    f = pd.DataFrame(index=df.index)
    f["atr"] = eng._atr(df)
    f["atr_ratio"] = eng._atr_ratio(df)
    f["atr_percentile"] = eng._rolling_percentile(f["atr"], cfg.atr_percentile_window)
    f["bb_width"] = eng._bb_width(df)
    f["bb_width_percentile"] = eng._rolling_percentile(f["bb_width"], cfg.bb_percentile_window)
    f["parkinson_vol"] = eng._parkinson_volatility(df)
    for w in cfg.rv_windows:
        f[f"rv_{w}"] = eng._realized_volatility(log_returns, w)
    f["rv_adjusted"] = eng._periodicity_adjusted_rv(log_returns, df.index)
    # verbatim from compute()
    rv_short, rv_mid, rv_long = f["rv_15"], f["rv_60"], f["rv_240"]
    f["rv_ratio_short_long"] = rv_short / rv_long.replace(0, np.nan)
    f["rv_ratio_mid_long"] = rv_mid / rv_long.replace(0, np.nan)
    f["rv_ratio_short_mid"] = rv_short / rv_mid.replace(0, np.nan)
    return f[EXISTING]


def har_features(bars: pd.DataFrame, minute_index, r) -> pd.DataFrame:
    t = bars.index
    s, n = window_rv(minute_index, r, (t - pd.Timedelta(hours=1)).asi8, t.asi8)
    rvbar = pd.Series(np.where(n > 0, s, np.nan), index=t)
    return pd.DataFrame({
        "har_1h": _safe_log(rvbar),
        "har_24h": _safe_log(rvbar.rolling(24, min_periods=24).sum()),
        "har_120h": _safe_log(rvbar.rolling(120, min_periods=120).sum()),
    }, index=t)


def targets(bars: pd.DataFrame, minute_index, r) -> pd.DataFrame:
    t = bars.index
    out = pd.DataFrame(index=t)
    for name, h in HORIZONS.items():
        end = t + pd.Timedelta(minutes=h)
        s, n = window_rv(minute_index, r, t.asi8, end.asi8)
        ok = (n >= COVERAGE * h) & (s > 0) & (end <= SEAL)
        out[name] = np.where(ok, _safe_log(s), np.nan)
        out[f"{name}_n"] = n
    return out


def build_features(m: pd.DataFrame):
    """All features at each 1H bar. Uses only data up to each bar's close."""
    r, flag = minute_returns(m)
    bars = hourly_bars(m, flag)
    feats = pd.concat(
        [new_features(bars), existing_features(bars), har_features(bars, m.index, r)],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan)
    return bars, feats, r


def build_dataset(m: pd.DataFrame):
    bars, feats, r = build_features(m)
    ds = pd.concat([feats, targets(bars, m.index, r)], axis=1)
    assert_sealed(ds, "1H dataset")
    return bars, ds


# =============================================================================
# Models and inference
# =============================================================================

def ols_fit_predict(train: pd.DataFrame, test: pd.DataFrame, feats, target):
    mu = train[feats].mean()
    sd = train[feats].std(ddof=0).replace(0, 1.0)
    Xtr = np.column_stack([np.ones(len(train)), ((train[feats] - mu) / sd).to_numpy()])
    Xte = np.column_stack([np.ones(len(test)), ((test[feats] - mu) / sd).to_numpy()])
    y = train[target].to_numpy()
    beta, *_ = np.linalg.lstsq(Xtr, y, rcond=None)
    resid = y - Xtr @ beta
    s2 = resid @ resid / (len(y) - Xtr.shape[1])
    return Xte @ beta, beta, s2


def qlike(y, yhat, s2):
    ratio = np.exp(y) / np.exp(yhat + s2 / 2.0)
    return ratio - np.log(ratio) - 1.0


def newey_west_lrv(d: np.ndarray, lag: int) -> float:
    u = d - d.mean()
    T = len(u)
    lrv = u @ u / T
    for k in range(1, lag + 1):
        lrv += 2.0 * (1.0 - k / (lag + 1.0)) * (u[k:] @ u[:-k]) / T
    return lrv


def dm_test(loss_a: np.ndarray, loss_b: np.ndarray, lag: int):
    """DM on d = loss_a - loss_b. Returns (stat, one-sided p for 'A better')."""
    d = np.asarray(loss_a) - np.asarray(loss_b)
    lrv = newey_west_lrv(d, lag)
    stat = d.mean() / np.sqrt(lrv / len(d))
    return float(stat), float(stats.norm.cdf(stat))


def folds():
    for k in range(8):
        test_start, test_end = QUARTER_EDGES[k], QUARTER_EDGES[k + 1]
        yield k + 1, test_start - EMBARGO, test_start, test_end


def fold_label(test_start) -> str:
    return f"{test_start.year}Q{(test_start.month - 1) // 3 + 1}"


def walk_forward(sample: pd.DataFrame, target: str):
    rows, coefs, preds = [], [], []
    for k, train_end, test_start, test_end in folds():
        tr = sample[(sample.index >= ANALYSIS_START) & (sample.index <= train_end)]
        te = sample[(sample.index >= test_start) & (sample.index < test_end)]
        assert tr.index.max() + EMBARGO <= test_start
        y_te = te[target].to_numpy()
        ybar, yvar = tr[target].mean(), tr[target].var(ddof=1)
        bench_sse = float(((y_te - ybar) ** 2).sum())
        bench_q = qlike(y_te, np.full_like(y_te, ybar), yvar)
        p = pd.DataFrame({"fold": k, "y": y_te, "bench": ybar}, index=te.index)
        rows.append({"fold": k, "quarter": fold_label(test_start), "model": "BENCH",
                     "n_train": len(tr), "n_test": len(te), "sse": 0.0,
                     "bench_sse": bench_sse, "r2": 0.0, "qlike": float(bench_q.mean())})
        for name, feats in MODELS.items():
            yhat, beta, s2 = ols_fit_predict(tr, te, feats, target)
            sse = float(((y_te - yhat) ** 2).sum())
            q = qlike(y_te, yhat, s2)
            p[name] = yhat
            p[f"{name}_qlike"] = q
            rows.append({"fold": k, "quarter": fold_label(test_start), "model": name,
                         "n_train": len(tr), "n_test": len(te), "sse": sse,
                         "bench_sse": bench_sse, "r2": 1 - sse / bench_sse,
                         "qlike": float(q.mean())})
            for fname, b in zip(["const"] + feats, beta):
                coefs.append({"target": target, "fold": k, "quarter": fold_label(test_start),
                              "model": name, "feature": fname, "coef": float(b)})
        p["bench_qlike"] = bench_q
        preds.append(p)
    return pd.DataFrame(rows), pd.DataFrame(coefs), pd.concat(preds)


def pooled_metrics(preds: pd.DataFrame):
    bench_sse = ((preds["y"] - preds["bench"]) ** 2).sum()
    out = {"BENCH": {"r2": 0.0, "qlike": float(preds["bench_qlike"].mean())}}
    for name in MODELS:
        sse = ((preds["y"] - preds[name]) ** 2).sum()
        out[name] = {"r2": float(1 - sse / bench_sse),
                     "qlike": float(preds[f"{name}_qlike"].mean())}
    return out


def dm_table(preds: pd.DataFrame, target: str):
    preds = preds.sort_index()
    out = []
    for a, b in DM_PAIRS:
        la = (preds["y"] - preds[a]) ** 2
        lb = (preds["y"] - preds[b]) ** 2
        stat, p = dm_test(la.to_numpy(), lb.to_numpy(), NW_LAG[target])
        out.append({"target": target, "A": a, "B": b, "mean_d": float((la - lb).mean()),
                    "dm_stat": stat, "p_A_better": p})
    return pd.DataFrame(out)


# =============================================================================
# Correlation structure
# =============================================================================

def corr_cluster(df: pd.DataFrame):
    rho = df.corr(method="spearman")
    dist = 1.0 - rho.abs().to_numpy()
    np.fill_diagonal(dist, 0.0)
    Z = linkage(squareform(dist, checks=False), method="average")
    labels = fcluster(Z, t=CLUSTER_CUT, criterion="distance")
    return rho, Z, pd.Series(labels, index=df.columns, name="cluster")


def cluster_table(clusters: pd.Series) -> list:
    out = []
    for c in sorted(clusters.unique()):
        members = list(clusters.index[clusters == c])
        concepts = sorted({CONCEPT[m] for m in members})
        out.append({"cluster": int(c), "members": members, "concepts": concepts})
    return out


def d2_check(rho: pd.DataFrame):
    pairs = {}
    for i, a in enumerate(NEW):
        for b in NEW[i + 1:]:
            pairs[f"{a}~{b}"] = float(rho.loc[a, b])
    return all(abs(v) < D2_MAX_RHO for v in pairs.values()), pairs


def isolated(rho: pd.DataFrame):
    a = rho.abs().copy()
    np.fill_diagonal(a.values, np.nan)
    mx = a.max()
    return {f: float(v) for f, v in mx.items() if v < ISOLATED_RHO}, mx


def plot_corr(rho: pd.DataFrame, Z, title: str, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    order = rho.columns[leaves_list(Z)]
    r = rho.loc[order, order]
    fig, (ax0, ax1) = plt.subplots(1, 2, figsize=(17, 7.5),
                                   gridspec_kw={"width_ratios": [1, 1.25]})
    dendrogram(Z, labels=list(rho.columns), orientation="right", ax=ax0,
               color_threshold=CLUSTER_CUT, above_threshold_color="#888888")
    ax0.axvline(CLUSTER_CUT, ls="--", color="crimson", lw=1)
    ax0.set_xlabel("1 - |Spearman rho|  (average linkage; dashed = cut 0.2)")
    ax0.set_title("Dendrogram")
    im = ax1.imshow(r.to_numpy(), cmap="RdBu_r", vmin=-1, vmax=1)
    ax1.set_xticks(range(len(order)), order, rotation=90, fontsize=8)
    ax1.set_yticks(range(len(order)), order, fontsize=8)
    for i in range(len(order)):
        for j in range(len(order)):
            ax1.text(j, i, f"{r.iat[i, j]:.2f}", ha="center", va="center", fontsize=6,
                     color="white" if abs(r.iat[i, j]) > 0.6 else "black")
    for lbl in list(ax1.get_xticklabels()) + list(ax1.get_yticklabels()):
        if lbl.get_text() in NEW:
            lbl.set_fontweight("bold")
    fig.colorbar(im, ax=ax1, fraction=0.046, pad=0.04)
    ax1.set_title("Spearman rho, ordered by clustering (new features bold)")
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def plot_comembership(freq: pd.DataFrame, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(freq.to_numpy(), cmap="viridis", vmin=0, vmax=1)
    ax.set_xticks(range(len(freq)), freq.columns, rotation=90, fontsize=8)
    ax.set_yticks(range(len(freq)), freq.index, fontsize=8)
    for i in range(len(freq)):
        for j in range(len(freq)):
            ax.text(j, i, f"{freq.iat[i, j]:.2f}".rstrip("0").rstrip("."), ha="center",
                    va="center", fontsize=6,
                    color="black" if freq.iat[i, j] > 0.6 else "white")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    ax.set_title("Share of the 8 training windows in which two features share a cluster (cut 0.2)")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    OUT.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    results: dict = {"seal": str(SEAL)}

    log(f"Loading 1-min data < {SEAL.date()} from {DATA.relative_to(ROOT)}")
    m, info = load_minutes()
    results["data"] = info
    log(f"  {info}")

    log("Building 1H bars, features, HAR and targets")
    bars, ds = build_dataset(m)
    gaps = minute_returns(m)[1]
    results["bars"] = {"n_bars_total": len(bars),
                       "n_bars_with_gap": int((bars["has_gap"] > 0).sum()),
                       "n_minute_gaps": int(gaps.sum()),
                       "n_r1h_dropped": int(bars["r1h"].isna().sum())}
    log(f"  {results['bars']}")

    a = ds[(ds.index >= ANALYSIS_START) & (ds.index < SEAL)].copy()
    assert_sealed(a, "analysis frame")
    for name, h in HORIZONS.items():
        ends = a.index[a[name].notna()] + pd.Timedelta(minutes=h)
        assert (ends <= SEAL).all(), f"{name} target window reaches past the seal"
    results["max_bar_used"] = str(a.index.max())
    log(f"  analysis bars: {len(a)}  ({a.index.min()} .. {a.index.max()})")

    # ---- row counts ---------------------------------------------------------
    counts = {"analysis_bars": len(a)}
    for fs_name, cols in [("new3", NEW), ("existing13", EXISTING), ("har3", HAR)]:
        counts[f"{fs_name}_complete"] = int(a[cols].notna().all(axis=1).sum())
    for t in HORIZONS:
        counts[f"{t}_nonnan"] = int(a[t].notna().sum())
        for name, feats in MODELS.items():
            counts[f"{t}_{name}_own_rows"] = int(a[feats + [t]].notna().all(axis=1).sum())
    all_model_feats = sorted(set(sum(MODELS.values(), [])))

    # ---- Step 1 + 3: correlation structure ----------------------------------
    log("Step 1: correlation structure")
    c16 = a[ALL16].dropna()
    windows = [("full_2021_2023", c16)]
    for k, train_end, test_start, _ in folds():
        windows.append((f"train_{k}_to_{train_end.date()}",
                        c16[c16.index <= train_end]))
    corr_out = {}
    comember = pd.DataFrame(0.0, index=ALL16, columns=ALL16)
    for wname, wdf in windows:
        rho, Z, cl = corr_cluster(wdf)
        ok, pairs = d2_check(rho)
        iso, maxabs = isolated(rho)
        corr_out[wname] = {"n_rows": len(wdf), "start": str(wdf.index.min()),
                           "end": str(wdf.index.max()), "clusters": cluster_table(cl),
                           "n_clusters": int(cl.nunique()), "d2_pass": ok,
                           "d2_pairs": pairs, "isolated": iso,
                           "max_abs_rho": {k2: float(v) for k2, v in maxabs.items()}}
        rho.to_csv(OUT / f"spearman_{wname}.csv", float_format="%.4f")
        if wname.startswith("train_"):
            same = cl.to_numpy()[:, None] == cl.to_numpy()[None, :]
            comember += same.astype(float)
        if wname in ("full_2021_2023", windows[1][0]):
            plot_corr(rho, Z, f"Volatility features, {wname} (n={len(wdf)})",
                      FIG / f"corr_{wname}.png")
        log(f"  {wname}: n={len(wdf)} clusters={cl.nunique()} D2={'pass' if ok else 'FAIL'} "
            f"pairs={ {k2: round(v, 3) for k2, v in pairs.items()} }")
    comember /= 8.0
    comember.to_csv(OUT / "comembership_train_windows.csv", float_format="%.3f")
    order = ALL16
    try:
        _, Zf, _ = corr_cluster(c16)
        order = list(np.array(ALL16)[leaves_list(Zf)])
    except Exception:
        pass
    plot_comembership(comember.loc[order, order], FIG / "comembership_train_windows.png")
    results["correlation"] = corr_out

    # ---- Step 2: OOS information test ---------------------------------------
    log("Step 2: walk-forward OLS")
    oos = {}
    all_coefs, all_folds, all_dm = [], [], []
    for t in HORIZONS:
        sample = a.dropna(subset=all_model_feats + [t])
        assert_sealed(sample, f"model sample {t}")
        counts[f"{t}_common_rows"] = len(sample)
        fold_df, coef_df, preds = walk_forward(sample, t)
        fold_df.insert(0, "target", t)
        pooled = pooled_metrics(preds)
        dm = dm_table(preds, t)
        oos[t] = {"pooled": pooled, "n_test": len(preds),
                  "n_train_first": int(fold_df.iloc[0]["n_train"])}
        all_folds.append(fold_df)
        all_coefs.append(coef_df)
        all_dm.append(dm)
        preds.to_csv(OUT / f"predictions_{t}.csv", float_format="%.6f")
        log(f"  {t}: common rows={len(sample)} test rows={len(preds)} "
            + " ".join(f"{k2}:R2={v['r2']:.4f}" for k2, v in pooled.items()))
    fold_all = pd.concat(all_folds)
    coef_all = pd.concat(all_coefs)
    dm_all = pd.concat(all_dm)
    fold_all.to_csv(OUT / "folds.csv", index=False, float_format="%.6f")
    coef_all.to_csv(OUT / "coefficients.csv", index=False, float_format="%.6f")
    dm_all.to_csv(OUT / "dm_tests.csv", index=False, float_format="%.6g")
    results["oos"] = oos
    results["row_counts"] = counts

    # ---- Decision rules -----------------------------------------------------
    log("Decision rules")
    dec = {}
    d1_parts = {}
    for t in HORIZONS:
        r1, r2 = oos[t]["pooled"]["M1"]["r2"], oos[t]["pooled"]["M2"]["r2"]
        row = dm_all[(dm_all.target == t) & (dm_all.A == "M1") & (dm_all.B == "M2")].iloc[0]
        d1_parts[t] = {"r2_M1": r1, "r2_M2": r2, "r2_ok": r2 >= r1 - 0.01,
                       "dm_stat": row.dm_stat, "p_M1_better": row.p_A_better,
                       "dm_ok": bool(row.p_A_better >= 0.05)}
    dec["D1"] = {"pass": all(v["r2_ok"] and v["dm_ok"] for v in d1_parts.values()),
                 "detail": d1_parts}

    first = windows[1][0]
    dec["D2"] = {"pass": corr_out[first]["d2_pass"], "window": first,
                 "pairs": corr_out[first]["d2_pairs"]}

    d3 = {}
    for t in HORIZONS:
        row = dm_all[(dm_all.target == t) & (dm_all.A == "M3") & (dm_all.B == "M0")].iloc[0]
        d3[t] = {"r2_M0": oos[t]["pooled"]["M0"]["r2"], "r2_M3": oos[t]["pooled"]["M3"]["r2"],
                 "dm_stat": row.dm_stat, "p_M3_better": row.p_A_better,
                 "M3_beats_M0": bool(row.p_A_better < 0.05)}
    dec["D3"] = d3

    d2_windows = {w: corr_out[w]["d2_pass"] for w, _ in windows[1:]}
    n_d2 = sum(d2_windows.values())
    signs = {}
    for t in HORIZONS:
        c = coef_all[(coef_all.target == t) & (coef_all.model == "M2") & (coef_all.feature != "const")]
        for f in NEW:
            v = c[c.feature == f]["coef"].to_numpy()
            pos, neg = int((v > 0).sum()), int((v < 0).sum())
            signs[f"{t}:{f}"] = {"pos": pos, "neg": neg, "consistent": max(pos, neg) >= 7}
    dec["D4"] = {"pass": n_d2 >= 7 and all(s["consistent"] for s in signs.values()),
                 "d2_windows_passing": n_d2, "d2_windows": d2_windows, "signs": signs}
    results["decisions"] = dec
    for k2, v in dec.items():
        log(f"  {k2}: {v.get('pass', '(informative)')}")

    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))
    write_summary(results, fold_all, coef_all, dm_all)
    log(f"Done. Outputs in {OUT.relative_to(ROOT)} and {FIG.relative_to(ROOT)}")
    return 0


def write_summary(res, fold_all, coef_all, dm_all):
    """Machine-written tables; REPORT.md is written from these."""
    L = ["# Auto-generated results summary", "",
         f"Seal: {res['seal']}. Last bar used: {res['max_bar_used']}.", ""]
    L += ["## Row counts", "", "| item | rows |", "|---|---:|"]
    L += [f"| {k} | {v} |" for k, v in res["row_counts"].items()]
    for t in HORIZONS:
        L += ["", f"## {t}: pooled OOS (test rows = {res['oos'][t]['n_test']})", "",
              "| model | R² | QLIKE |", "|---|---:|---:|"]
        for k, v in res["oos"][t]["pooled"].items():
            L.append(f"| {k} | {v['r2']:.4f} | {v['qlike']:.4f} |")
        f = fold_all[fold_all.target == t]
        L += ["", f"### {t}: per-fold R²", ""]
        piv = f[f.model != "BENCH"].pivot(index="quarter", columns="model", values="r2")
        L.append("| quarter | n_test | " + " | ".join(piv.columns) + " |")
        L.append("|---|---:|" + "---:|" * len(piv.columns))
        nt = f.drop_duplicates("quarter").set_index("quarter")["n_test"]
        for q, row in piv.iterrows():
            L.append(f"| {q} | {nt[q]} | " + " | ".join(f"{x:.3f}" for x in row) + " |")
        L += ["", f"### {t}: per-fold QLIKE", ""]
        piv = f.pivot(index="quarter", columns="model", values="qlike")
        L.append("| quarter | " + " | ".join(piv.columns) + " |")
        L.append("|---|" + "---:|" * len(piv.columns))
        for q, row in piv.iterrows():
            L.append(f"| {q} | " + " | ".join(f"{x:.4f}" for x in row) + " |")
    L += ["", "## DM tests (d = e²_A − e²_B; p = one-sided, A better)", "",
          "| target | A | B | mean d | DM | p |", "|---|---|---|---:|---:|---:|"]
    for _, r in dm_all.iterrows():
        L.append(f"| {r.target} | {r.A} | {r.B} | {r.mean_d:.5f} | {r.dm_stat:.3f} | {r.p_A_better:.4f} |")
    L += ["", "## M2 coefficients per fold (standardised)", ""]
    c = coef_all[(coef_all.model == "M2")]
    piv = c.pivot_table(index=["target", "feature"], columns="quarter", values="coef")
    L.append("| target | feature | " + " | ".join(piv.columns) + " |")
    L.append("|---|---|" + "---:|" * len(piv.columns))
    for (t, fe), row in piv.iterrows():
        L.append(f"| {t} | {fe} | " + " | ".join(f"{x:+.3f}" for x in row) + " |")
    L += ["", "## Clusters (cut 0.2)", ""]
    for w, v in res["correlation"].items():
        L.append(f"**{w}** (n={v['n_rows']}, D2 {'pass' if v['d2_pass'] else 'FAIL'}, "
                 f"pairs {', '.join(f'{k}={x:+.3f}' for k, x in v['d2_pairs'].items())})")
        for cl in v["clusters"]:
            L.append(f"- {cl['cluster']}: {', '.join(cl['members'])}  [{'/'.join(cl['concepts'])}]")
        if v["isolated"]:
            L.append(f"- isolated (max |rho| < 0.5): {v['isolated']}")
        L.append("")
    L += ["## Decisions", "", "```json", json.dumps(res["decisions"], indent=2, default=str), "```"]
    (OUT / "summary.md").write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
