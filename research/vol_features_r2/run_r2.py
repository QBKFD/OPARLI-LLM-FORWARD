#!/usr/bin/env python3
"""
Volatility block, round 2: deseasonalized, 1-min based candidate block C
(L, S, T, W) vs the 13 existing 1H features, with a development run
(2021-2023) and a primary out-of-era confirmation run (2007-2020).

Specification: PREREG.md (committed before any model result). Choices:
DECISIONS.md. Research only; nothing in regime_classifier/ or backend/ changes.

    backend/venv/bin/python3 research/vol_features_r2/run_r2.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "vol_features"))
sys.path.insert(0, str(HERE))
import run_study as r1  # noqa: E402  (round 1: loaders, bars, features, OLS, DM)

OUT = HERE / "results"
CACHE = HERE / "cache" / "xauusd_txt_pre2024_filetime.parquet"
SEAL = r1.SEAL                                   # 2024-01-01 UTC
CONF_END = pd.Timestamp("2021-01-01", tz="UTC")
EMBARGO = pd.Timedelta(hours=24)
HORIZONS = {"y4_ds": 240, "y24_ds": 1440}
NW_LAG = {"y4_ds": 8, "y24_ds": 48}
COVERAGE = 0.90
N_BUCKETS = 576
MIN_BUCKET_OBS = 50

M1 = r1.EXISTING
HOD = [f"h_{h}" for h in range(1, 24)]
C4 = ["L", "S", "T", "W"]
C3 = ["L", "S", "T"]
MODELS = {"M1": M1, "M1H": M1 + HOD, "MC": C4, "MC3": C3, "MCH": C4 + HOD}
# (A, B): d = e²_A − e²_B, p = one-sided "A better"
DM_PAIRS = [("M1H", "MC"), ("MC", "MC3"), ("MC3", "MC"), ("MCH", "MC"),
            ("MC", "M1"), ("M1H", "M1")]
RAW_HAR = ["har_1h", "har_24h", "har_120h"]

PERIODS = {
    "DEV": {
        "start": pd.Timestamp("2021-01-01", tz="UTC"), "end": SEAL,
        "edges": pd.date_range("2022-01-01", "2024-01-01", freq="QS", tz="UTC"),
        "eras": None,
    },
    "CONF": {
        "start": pd.Timestamp("2007-01-01", tz="UTC"), "end": CONF_END,
        "edges": pd.date_range("2009-01-01", "2021-01-01", freq="6MS", tz="UTC"),
        "eras": {"2008-2012": ("2008-01-01", "2013-01-01"),
                 "2013-2020": ("2013-01-01", "2021-01-01")},
    },
}


def log(msg):
    r1.log(msg)


def assert_sealed(obj, what="frame", end=SEAL):
    r1.assert_sealed(obj, what)
    idx = obj.index if hasattr(obj, "index") else pd.DatetimeIndex(obj)
    if end < SEAL and len(idx) and (idx >= end).any():
        raise AssertionError(f"SEALED DATA: {what} has rows at or after {end.date()}")


# =============================================================================
# Data
# =============================================================================

def load_dev():
    m, _ = r1.load_minutes()                      # parquet, 2020-01-01 .. < 2024
    assert_sealed(m, "DEV 1-min")
    return m


def load_conf(start=pd.Timestamp("2007-01-01", tz="UTC"), end=CONF_END):
    if not CACHE.exists():
        import audit_long
        audit_long.load_raw()
    df = pd.read_parquet(CACHE).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_localize("UTC")        # the file is UTC (DATA_AUDIT_LONG.md)
    df.index.name = "timestamp"
    df = df[(df.index >= start) & (df.index < end)]
    assert_sealed(df, "CONF 1-min", end=CONF_END)
    return df[["open", "high", "low", "close", "vol"]].rename(columns={"vol": "volume"})


def ny_dst(index: pd.DatetimeIndex) -> np.ndarray:
    local = index.tz_convert("America/New_York").tz_localize(None)
    return np.asarray((local - index.tz_localize(None)) == pd.Timedelta(hours=-4))


def minute_buckets(index: pd.DatetimeIndex) -> np.ndarray:
    return ny_dst(index).astype(int) * 288 + (index.hour * 60 + index.minute) // 5


# =============================================================================
# Seasonal profile and deseasonalized features
# =============================================================================

def fit_profile(index: pd.DatetimeIndex, r: pd.Series, bucket: np.ndarray, t0, t1) -> np.ndarray:
    """Mean r² per bucket from 1-min bars opening in [t0, t1) only."""
    rv = r.to_numpy()
    mask = (index >= t0) & (index < t1) & ~np.isnan(rv)
    s = np.bincount(bucket[mask], weights=rv[mask] ** 2, minlength=N_BUCKETS)
    n = np.bincount(bucket[mask], minlength=N_BUCKETS)
    prof = np.where(n >= MIN_BUCKET_OBS, s / np.maximum(n, 1), np.nan)
    if np.isnan(prof).all():
        raise ValueError("no bucket has enough returns to fit the seasonal profile")
    prof = np.where(np.isnan(prof), np.nanmedian(prof), prof)
    return np.maximum(prof, np.quantile(prof, 0.01))


def window_sum(index: pd.DatetimeIndex, x: np.ndarray, starts, ends):
    """Sum and count of non-NaN x over 1-min bars opening in [start, end)."""
    ok = ~np.isnan(x)
    cs = np.concatenate([[0.0], np.cumsum(np.where(ok, x, 0.0))])
    cn = np.concatenate([[0], np.cumsum(ok)])
    t = index.asi8
    i0 = np.searchsorted(t, np.asarray(starts), "left")
    i1 = np.searchsorted(t, np.asarray(ends), "left")
    return cs[i1] - cs[i0], cn[i1] - cn[i0]


def _log(x):
    x = np.asarray(x, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(x > 0, np.log(np.where(x > 0, x, 1.0)), np.nan)


def ds_features(bars_index, index, r, bucket, profile, end) -> pd.DataFrame:
    """L, S, T (+ the raw log dsRVs) and deseasonalized targets for every bar."""
    rds2 = r.to_numpy() ** 2 / profile[bucket]
    t = bars_index
    s1, n1 = window_sum(index, rds2, (t - pd.Timedelta(hours=1)).asi8, t.asi8)
    per_bar = pd.Series(np.where(n1 > 0, s1, np.nan), index=t)
    a1 = _log(per_bar)
    a24 = _log(per_bar.rolling(24, min_periods=24).sum())
    a120 = _log(per_bar.rolling(120, min_periods=120).sum())
    out = pd.DataFrame({"ds1": a1, "ds24": a24, "ds120": a120,
                        "L": a24, "S": a1 - a24, "T": a24 - a120}, index=t)
    for name, h in HORIZONS.items():
        e = t + pd.Timedelta(minutes=h)
        s, n = window_sum(index, rds2, t.asi8, e.asi8)
        ok = (n >= COVERAGE * h) & (s > 0) & (e <= end)
        out[name] = np.where(ok, _log(s), np.nan)
    return out


def raw_targets(bars_index, index, r, end) -> pd.DataFrame:
    """Raw (not deseasonalized) log RV targets, for the clock check only."""
    r2 = r.to_numpy() ** 2
    out = pd.DataFrame(index=bars_index)
    for name, h in (("y4_raw", 240), ("y24_raw", 1440)):
        e = bars_index + pd.Timedelta(minutes=h)
        s, n = window_sum(index, r2, bars_index.asi8, e.asi8)
        out[name] = np.where((n >= COVERAGE * h) & (s > 0) & (e <= end), _log(s), np.nan)
    return out


def static_features(m: pd.DataFrame, r, flag):
    """Profile-free features: the 13 existing, W, hour dummies, raw HAR."""
    bars = r1.hourly_bars(m, flag)
    hod = pd.DataFrame({f"h_{h}": (bars.index.hour == h).astype(float) for h in range(1, 24)},
                       index=bars.index)
    st = pd.concat([r1.existing_features(bars),
                    r1.new_features(bars)[["wick60"]].rename(columns={"wick60": "W"}),
                    hod, r1.har_features(bars, m.index, r)], axis=1)
    return bars, st.replace([np.inf, -np.inf], np.nan)


def all_features(m: pd.DataFrame, profile: np.ndarray, end) -> pd.DataFrame:
    """Every feature at every bar for a fixed profile (used by tests)."""
    r, flag = r1.minute_returns(m)
    bars, st = static_features(m, r, flag)
    ds = ds_features(bars.index, m.index, r, minute_buckets(m.index), profile, end)
    return pd.concat([st, ds], axis=1)


# =============================================================================
# Models, inference
# =============================================================================

def hac_tstats(train: pd.DataFrame, feats, target, lag):
    mu, sd = train[feats].mean(), train[feats].std(ddof=0).replace(0, 1.0)
    X = np.column_stack([np.ones(len(train)), ((train[feats] - mu) / sd).to_numpy()])
    y = train[target].to_numpy()
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    u = y - X @ beta
    g = X * u[:, None]
    S = g.T @ g
    for k in range(1, lag + 1):
        G = g[k:].T @ g[:-k]
        S += (1 - k / (lag + 1)) * (G + G.T)
    B = np.linalg.inv(X.T @ X)
    se = np.sqrt(np.diag(B @ S @ B))
    return dict(zip(feats, (beta / se)[1:])), dict(zip(feats, beta[1:]))


def eta2(x: pd.Series) -> float:
    x = x.dropna()
    g = x.groupby(x.index.hour).transform("mean")
    den = ((x - x.mean()) ** 2).sum()
    return float(((g - x.mean()) ** 2).sum() / den) if den > 0 else np.nan


def folds(edges):
    for k in range(len(edges) - 1):
        yield k + 1, edges[k] - EMBARGO, edges[k], edges[k + 1]


def label(ts, half=False):
    return f"{ts.year}H{1 + (ts.month > 6)}" if half else f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


def era_of(ts, eras):
    if not eras:
        return "all"
    for name, (a, b) in eras.items():
        if pd.Timestamp(a, tz="UTC") <= ts < pd.Timestamp(b, tz="UTC"):
            return name
    return "other"


def run_period(name: str, cfg: dict) -> dict:
    log(f"=== {name} ===")
    m = load_dev() if name == "DEV" else load_conf()
    r, flag = r1.minute_returns(m)
    bucket = minute_buckets(m.index)
    bars, st = static_features(m, r, flag)
    assert_sealed(bars, f"{name} bars", end=cfg["end"])
    rawt = raw_targets(bars.index, m.index, r, cfg["end"])
    start, end = cfg["start"], cfg["end"]
    in_period = (bars.index >= start) & (bars.index < end)
    log(f"  1-min rows {len(m):,}, bars {len(bars):,}, analysis bars {int(in_period.sum()):,}")

    half = name == "CONF"
    fold_frames, pattern, eta_rows, rho_rows = {}, None, [], []
    for k, train_end, ts, te in folds(cfg["edges"]):
        prof = fit_profile(m.index, r, bucket, start, train_end)
        ds = ds_features(bars.index, m.index, r, bucket, prof, end)
        F = pd.concat([st, ds, rawt], axis=1)[in_period]
        assert_sealed(F, f"{name} fold {k} frame", end=end)
        for t, h in HORIZONS.items():
            e = F.index[F[t].notna()] + pd.Timedelta(minutes=h)
            assert (e <= end).all(), f"{name}: {t} window past the seal"
        pat = F[C4 + list(HORIZONS)].notna().to_numpy()
        if pattern is None:
            pattern = pat
        assert (pat == pattern).all(), "missingness depends on the profile"
        fold_frames[k] = (train_end, ts, te, F)
        tr = F[F.index <= train_end]
        eta_rows.append({"fold": k, **{c: eta2(tr[c]) for c in
                         M1 + RAW_HAR + ["ds1", "ds24", "ds120"] + C4 +
                         ["y4_raw", "y24_raw"] + list(HORIZONS)}})
        rho = tr[C4].dropna().corr(method="spearman")
        rho_rows.append({"fold": k, **{f"{a}~{b}": float(rho.loc[a, b])
                                       for i, a in enumerate(C4) for b in C4[i + 1:]}})
    eta_df = pd.DataFrame(eta_rows).set_index("fold")
    rho_df = pd.DataFrame(rho_rows).set_index("fold")

    res = {"n_minutes": len(m), "n_bars": int(in_period.sum()), "targets": {}}
    for t in HORIZONS:
        cols = sorted(set(sum(MODELS.values(), []))) + [t]
        preds, coefs = [], []
        n_rows = None
        for k, (train_end, ts, te, F) in fold_frames.items():
            S = F.dropna(subset=cols)
            n_rows = len(S) if n_rows is None else n_rows
            assert len(S) == n_rows
            assert_sealed(S, f"{name} model sample {t}", end=end)
            tr = S[S.index <= train_end]
            tst = S[(S.index >= ts) & (S.index < te)]
            assert tr.index.max() + EMBARGO <= ts
            y = tst[t].to_numpy()
            P = pd.DataFrame({"fold": k, "block": label(ts, half), "era": era_of(ts, cfg["eras"]),
                              "y": y, "bench": tr[t].mean()}, index=tst.index)
            P["bench_q"] = r1.qlike(y, np.full_like(y, tr[t].mean()), tr[t].var(ddof=1))
            for mname, feats in MODELS.items():
                yhat, beta, s2 = r1.ols_fit_predict(tr, tst, feats, t)
                P[mname] = yhat
                P[f"{mname}_q"] = r1.qlike(y, yhat, s2)
            for mname in ("MC", "MC3"):
                tstat, b = hac_tstats(tr, MODELS[mname], t, NW_LAG[t])
                for f in MODELS[mname]:
                    coefs.append({"fold": k, "block": label(ts, half), "era": P["era"].iat[0] if len(P) else "",
                                  "model": mname, "feature": f, "coef": b[f], "hac_t": tstat[f]})
            preds.append(P)
        P = pd.concat(preds).sort_index()
        C = pd.DataFrame(coefs)
        P.to_csv(OUT / f"predictions_{name}_{t}.csv", float_format="%.6f")
        res["targets"][t] = {"rows": n_rows, "test_rows": len(P),
                             "pooled": metrics(P), "per_block": per_block(P),
                             "dm": dm(P, t), "eras": {}, "coefs": C.to_dict("records")}
        if cfg["eras"]:
            for era in cfg["eras"]:
                Pe = P[P.era == era]
                res["targets"][t]["eras"][era] = {"test_rows": len(Pe), "pooled": metrics(Pe),
                                                  "dm": dm(Pe, t)}
        log(f"  {t}: rows {n_rows:,}, test {len(P):,}  " +
            " ".join(f"{k2}={v['r2']:.3f}" for k2, v in res["targets"][t]["pooled"].items()))
    res["eta2"] = eta_df
    res["rho"] = rho_df
    res["fold_eras"] = {k: era_of(ts, cfg["eras"]) for k, (_, ts, _, _) in fold_frames.items()}
    return res


def metrics(P: pd.DataFrame) -> dict:
    bsse = ((P.y - P.bench) ** 2).sum()
    out = {"BENCH": {"r2": 0.0, "qlike": float(P.bench_q.mean())}}
    for mname in MODELS:
        out[mname] = {"r2": float(1 - ((P.y - P[mname]) ** 2).sum() / bsse),
                      "qlike": float(P[f"{mname}_q"].mean())}
    return out


def per_block(P: pd.DataFrame) -> list:
    rows = []
    for (k, b), g in P.groupby(["fold", "block"]):
        rows.append({"fold": int(k), "block": b, "n": len(g),
                     **{f"{mn}_r2": v["r2"] for mn, v in metrics(g).items() if mn != "BENCH"},
                     **{f"{mn}_q": v["qlike"] for mn, v in metrics(g).items()}})
    return rows


def dm(P: pd.DataFrame, t: str) -> dict:
    out = {}
    for a, b in DM_PAIRS:
        la, lb = ((P.y - P[a]) ** 2).to_numpy(), ((P.y - P[b]) ** 2).to_numpy()
        s, p = r1.dm_test(la, lb, NW_LAG[t])
        out[f"{a}|{b}"] = {"dm": s, "p_A_better": p}
    return out


# =============================================================================
# Decision rules
# =============================================================================

def rules(res: dict, eta: pd.DataFrame, rho: pd.DataFrame, folds_sel=None) -> dict:
    """Evaluate R1-R5 on one result set (period or era)."""
    T = list(HORIZONS)
    r1_detail, r2_detail, r3_detail = {}, {}, {}
    for t in T:
        pm, d = res[t]["pooled"], res[t]["dm"]
        r1_detail[t] = {"r2_MC": pm["MC"]["r2"], "r2_M1H": pm["M1H"]["r2"],
                        "r2_ok": pm["MC"]["r2"] >= pm["M1H"]["r2"] - 0.01,
                        "p_M1H_better": d["M1H|MC"]["p_A_better"],
                        "dm_ok": d["M1H|MC"]["p_A_better"] >= 0.05}
        r2_detail[t] = {"gain_hod": pm["MCH"]["r2"] - pm["MC"]["r2"],
                        "ok": pm["MCH"]["r2"] - pm["MC"]["r2"] <= 0.01}
        r3_detail[t] = {"p_MC_better": d["MC|MC3"]["p_A_better"],
                        "p_MC3_better": d["MC3|MC"]["p_A_better"]}
    eta_sel = eta if folds_sel is None else eta.loc[folds_sel]
    eta_max = {c: float(eta_sel[c].max()) for c in C4}
    R1 = all(v["r2_ok"] and v["dm_ok"] for v in r1_detail.values())
    R2 = all(v["ok"] for v in r2_detail.values()) and all(v < 0.05 for v in eta_max.values())
    sig = {t: r3_detail[t]["p_MC_better"] < 0.05 for t in T}
    worse = {t: r3_detail[t]["p_MC3_better"] < 0.05 for t in T}
    R3 = any(sig[a] and not worse[b] for a in T for b in T if a != b)
    rho_sel = rho if folds_sel is None else rho.loc[folds_sel]
    rmax = rho_sel.abs().max()
    R4 = {"max_abs_rho": {k: float(v) for k, v in rmax.items()},
          "flags": [k for k, v in rmax.items() if v > 0.5]}
    R5 = {}
    for t in T:
        C = pd.DataFrame(res[t]["coefs"])
        C = C[C.model == "MC"]
        if folds_sel is not None:
            C = C[C.fold.isin(folds_sel)]
        for f in C4:
            c = C[C.feature == f]
            s = c[c.hac_t.abs() > 2]
            if s.empty:
                R5[f"{t}:{f}"] = {"status": "no signal", "n_sig": 0, "n_folds": len(c)}
            else:
                share = max((s.coef > 0).mean(), (s.coef < 0).mean())
                R5[f"{t}:{f}"] = {"status": "pass" if share >= 0.8 else "FAIL",
                                  "n_sig": len(s), "n_folds": len(c), "sign_share": float(share),
                                  "sign": "+" if (s.coef > 0).mean() >= 0.5 else "-"}
    R5_pass = all(v["status"] != "FAIL" for v in R5.values())
    block = ("C: L, S, T" + (", W" if R3 else "")) if (R1 and R2) else "status quo (13 existing)"
    return {"R1": {"pass": R1, "detail": r1_detail},
            "R2": {"pass": R2, "detail": r2_detail, "eta2_max": eta_max},
            "R3": {"keep_W": R3, "detail": r3_detail},
            "R4": R4, "R5": {"pass": R5_pass, "detail": R5}, "recommended_block": block}


def main() -> int:
    OUT.mkdir(exist_ok=True)
    allres = {}
    for name, cfg in PERIODS.items():
        res = run_period(name, cfg)
        tgt = res["targets"]
        dec = {"overall": rules(tgt, res["eta2"], res["rho"])}
        if cfg["eras"]:
            for era in cfg["eras"]:
                fs = [k for k, e in res["fold_eras"].items() if e == era]
                era_res = {t: {"pooled": tgt[t]["eras"][era]["pooled"], "dm": tgt[t]["eras"][era]["dm"],
                               "coefs": tgt[t]["coefs"]} for t in HORIZONS}
                dec[era] = rules(era_res, res["eta2"], res["rho"], folds_sel=fs)
        res["eta2"].to_csv(OUT / f"eta2_{name}.csv", float_format="%.4f")
        res["rho"].to_csv(OUT / f"rho_LSTW_{name}.csv", float_format="%.4f")
        for t in HORIZONS:
            pd.DataFrame(tgt[t]["coefs"]).to_csv(OUT / f"coefs_{name}_{t}.csv", index=False,
                                                 float_format="%.6f")
        allres[name] = {"n_minutes": res["n_minutes"], "n_bars": res["n_bars"],
                        "targets": {t: {k: v for k, v in tgt[t].items() if k != "coefs"}
                                    for t in HORIZONS},
                        "eta2_first_train": res["eta2"].iloc[0].to_dict(),
                        "eta2_max_train": res["eta2"].max().to_dict(),
                        "decisions": dec}
        for scope, d in dec.items():
            log(f"  {name}/{scope}: R1={d['R1']['pass']} R2={d['R2']['pass']} "
                f"R3 keep W={d['R3']['keep_W']} R4 flags={d['R4']['flags']} R5={d['R5']['pass']} "
                f"-> {d['recommended_block']}")
    (OUT / "results_r2.json").write_text(json.dumps(allres, indent=2, default=float))
    log("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
