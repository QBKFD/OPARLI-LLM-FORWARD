#!/usr/bin/env python3
"""
Regime model v2 (jump model on the locked volatility block) + Stage 1 test:
do causal regime labels carry information beyond simple baselines?

Specification: PREREG.md (committed before any result). Decisions: DECISIONS.md.

    backend/venv/bin/python3 research/regime_v2/run_regime.py
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in ("vol_features", "vol_features_r2", "vol_features_r3", "vol_block", "trend_features"):
    sys.path.insert(0, str(ROOT / "research" / p))
sys.path.insert(0, str(HERE))
import run_r3 as r3  # noqa: E402
import run_trend as rt  # noqa: E402
from vol_block import VolBlock  # noqa: E402
from jump import JumpModel, order_by, mean_duration  # noqa: E402

r1 = r3.r1
OUT = HERE / "results"
FIG = HERE / "figures"
EMBARGO = r3.EMBARGO
GRID = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0]
MIN_DURATION = 24
CLIP = 4.0
N_INIT = 10
OUTCOMES = {"O1": "y24_ds", "O2": "E24", "O3": "y4_ds"}
NW = {"O1": 48, "O2": 48, "O3": 8}
FEATS = {"A": ["L", "S_hd", "T"], "B": ["L", "S_hd", "T", "Z24"]}
VERSIONS = [("A", 3), ("B", 3), ("A", 2), ("A", 4)]         # (features, K)
NAMES3 = ["CALM", "NORMAL", "STRESSED"]
PERIODS = r3.PERIODS
COST = 0.30


def log(msg):
    r1.log(msg)


# =============================================================================
# Building blocks (tested)
# =============================================================================

def standardize(train: np.ndarray, X: np.ndarray):
    mu, sd = train.mean(axis=0), train.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    return np.clip((X - mu) / sd, -CLIP, CLIP), mu, sd


def cond_mean_forecast(lab_tr: np.ndarray, y_tr: np.ndarray, lab_te: np.ndarray) -> np.ndarray:
    """Training-window mean of y per label; unseen labels fall back to the training mean."""
    base = float(np.mean(y_tr))
    means = {l: float(y_tr[lab_tr == l].mean()) for l in np.unique(lab_tr)}
    return np.array([means.get(l, base) for l in lab_te])


def r2_oos(y, f, bench):
    return 1.0 - np.sum((y - f) ** 2) / np.sum((y - bench) ** 2)


def seed_for(fold, version, K, lam_idx):
    return 1000 * fold + 100 * (0 if version == "A" else 1) + 10 * K + lam_idx


def select_lambda(X_seq, fit_mask, val_mask, y_seq, K, fold, version, grid=GRID):
    """λ selection inside the training window.

    X_seq: standardized features of the complete rows up to train_end (fit part
    + validation), in time order. fit_mask / val_mask: boolean over those rows.
    y_seq: selection-stage y24_ds on the same rows (NaN allowed).
    """
    Xf = X_seq[fit_mask]
    out = []
    yf_ok = fit_mask & np.isfinite(y_seq)
    yv_ok = val_mask & np.isfinite(y_seq)
    for i, lam in enumerate(grid):
        jm = JumpModel(K=K, lam=lam, n_init=N_INIT, seed=seed_for(fold, version, K, i)).fit(Xf)
        lab = jm.causal_labels(X_seq)
        f = cond_mean_forecast(lab[yf_ok], y_seq[yf_ok], lab[yv_ok])
        r2 = r2_oos(y_seq[yv_ok], f, y_seq[yf_ok].mean())
        out.append({"lam": lam, "val_r2": float(r2), "val_duration": float(mean_duration(lab[val_mask]))})
    ok = [o for o in out if o["val_duration"] >= MIN_DURATION]
    if ok:
        best = max(ok, key=lambda o: (o["val_r2"], o["lam"]))
    else:
        best = max(out, key=lambda o: (o["val_duration"], o["lam"]))
    return best["lam"], out


# =============================================================================
# Per-fold jump-model work (runs in worker processes)
# =============================================================================

def fold_job(job: dict) -> dict:
    k = job["fold"]
    t_ns = job["t_ns"]
    train_end, val_start, fit_end, te = (job[x] for x in ("train_end", "val_start", "fit_end", "te"))
    res = {"fold": k, "versions": {}}
    for version, K in VERSIONS:
        cols = FEATS[version]
        # ---- selection stage (fit-part VolBlock, fit-part scaler)
        Fs = job["sel"][cols]
        ok_s = np.isfinite(Fs).all(axis=1) & (t_ns <= train_end)
        Xs_raw = Fs[ok_s]
        ts_s = t_ns[ok_s]
        fit_mask = ts_s <= fit_end
        val_mask = ts_s >= val_start
        Xs, _, _ = standardize(Xs_raw[fit_mask], Xs_raw)
        lam_star, table = select_lambda(Xs, fit_mask, val_mask, job["sel_y24"][ok_s], K, k, version)
        # ---- full stage (full-window VolBlock, training scaler)
        Ff = job["full"][cols]
        ok_f = np.isfinite(Ff).all(axis=1)
        Xf_raw = Ff[ok_f]
        tf = t_ns[ok_f]
        tr_mask = tf <= train_end
        Xf, mu, sd = standardize(Xf_raw[tr_mask], Xf_raw)
        lams = [lam_star]
        if (version, K) == ("A", 3):
            lams = sorted(set(GRID) | {lam_star, lam_star * 0.5, lam_star * 2.0})
        labels, offline = {}, None
        for lam in lams:
            li = GRID.index(lam) if lam in GRID else 50 + int(round(lam * 10))
            jm = JumpModel(K=K, lam=lam, n_init=N_INIT, seed=seed_for(k, version, K, li)).fit(Xf[tr_mask])
            rank = order_by(Xf_raw[tr_mask][:, 0], jm.labels_, K)       # raw L = column 0
            causal = rank[jm.causal_labels(Xf)]
            full = np.full(len(t_ns), -1, np.int8)
            full[ok_f] = causal
            labels[lam] = full
            if lam == lam_star and (version, K) == ("A", 3):
                upto = tf < te
                off = np.full(len(t_ns), -1, np.int8)
                off_idx = np.flatnonzero(ok_f)[upto]
                off[off_idx] = rank[jm.offline_labels(Xf[upto])]
                offline = off
                res["centroids_A3"] = (jm.centroids_ * sd + mu)[np.argsort(rank)].tolist()
        res["versions"][f"{version}{K}"] = {"lam_star": lam_star, "table": table, "labels": labels}
        if offline is not None:
            res["offline_A3"] = offline
    return res


# =============================================================================
# Period pipeline
# =============================================================================

def prepare(name, cfg):
    m = r3.load_dev() if name == "DEV" else r3.load_conf()
    start, end = cfg["start"], cfg["end"]
    r, flag = r1.minute_returns(m)
    bars = r1.hourly_bars(m, flag)
    r3.assert_sealed(bars, f"{name} bars", end=end)
    bucket = r3.minute_buckets(m.index)
    R5 = rt.five_min_returns(m)
    static = pd.concat([rt.z_features(bars.index, R5)[["Z24"]],
                        rt.targets(bars.index, R5, end)[["E24"]],
                        rt.existing_trend(bars)[["adx"]], bars[["close"]]], axis=1)
    inp = (bars.index >= start) & (bars.index < end)
    static = static[inp]
    t_ns = static.index.asi8
    jobs, fold_meta = [], {}
    for k, train_end, ts, te in r3.folds(cfg["edges"]):
        val_start = train_end - pd.DateOffset(months=12)
        if val_start - EMBARGO - start < pd.Timedelta(days=182):
            # DEV folds 1-3 only (training window < 18 months): the pre-registered
            # 12-month validation slice leaves < 6 months to fit on, so the last
            # third of the training window is used instead (DECISIONS #11).
            val_start = train_end - (train_end - start) / 3
        fit_end = val_start - EMBARGO
        vb_s = VolBlock().fit(m[(m.index >= start) & (m.index < fit_end)])
        sel = vb_s.transform(m, bars.index)[inp]
        sel_y = r3.ds_features(bars.index, m.index, r, bucket, vb_s.profile_, end)["y24_ds"][inp]
        vb = VolBlock().fit(m[(m.index >= start) & (m.index < train_end)])
        full = vb.transform(m, bars.index)[inp]
        ds = r3.ds_features(bars.index, m.index, r, bucket, vb.profile_, end)[["y24_ds", "y4_ds"]][inp]
        for tg, h in (("y24_ds", 1440), ("y4_ds", 240)):
            e = ds.index[ds[tg].notna()] + pd.Timedelta(minutes=h)
            assert (e <= end).all(), f"{name}: {tg} window past the seal"
        full_frame = pd.concat([full, static[["Z24"]]], axis=1)
        sel_frame = pd.concat([sel, static[["Z24"]]], axis=1)
        jobs.append({"fold": k, "t_ns": t_ns,
                     "train_end": train_end.value, "val_start": val_start.value,
                     "fit_end": fit_end.value, "te": te.value,
                     "sel": {c: sel_frame[c].to_numpy(float) for c in FEATS["B"]},
                     "full": {c: full_frame[c].to_numpy(float) for c in FEATS["B"]},
                     "sel_y24": sel_y.to_numpy(float)})
        fold_meta[k] = {"train_end": train_end, "ts": ts, "te": te, "val_start": val_start,
                        "full": full, "ds": ds}
        log(f"  {name} fold {k}: features ready")
    for j in jobs:                                    # dict-of-arrays -> 2D arrays per stage
        for stage in ("sel", "full"):
            j[stage] = _Cols(j[stage])
    return static, jobs, fold_meta


class _Cols(dict):
    """Column dict whose [list] returns a 2D float array (picklable)."""
    def __getitem__(self, key):
        if isinstance(key, list):
            return np.column_stack([dict.__getitem__(self, c) for c in key])
        return dict.__getitem__(self, key)


def run_period(name, cfg, workers):
    log(f"=== {name} ===")
    static, jobs, meta = prepare(name, cfg)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        fold_res = {r["fold"]: r for r in ex.map(fold_job, jobs)}
    log(f"  {name}: jump models fitted")
    return score_period(name, cfg, static, meta, fold_res)


def score_period(name, cfg, static, meta, fold_res):
    half = name == "CONF"
    rows_out, lam_rows, s4_parts, strat_rows = {o: [] for o in OUTCOMES}, [], [], []
    regime_desc, chart_labels = [], {}
    for k, fm in meta.items():
        fr = fold_res[k]
        train_end, ts, te = fm["train_end"], fm["ts"], fm["te"]
        idx = static.index
        v = fr["versions"]
        lamA = v["A3"]["lam_star"]
        lab = {"JM-A": v["A3"]["labels"][lamA], "JM-B": v["B3"]["labels"][v["B3"]["lam_star"]],
               "JM-A-K2": v["A2"]["labels"][v["A2"]["lam_star"]],
               "JM-A-K4": v["A4"]["labels"][v["A4"]["lam_star"]],
               "JM-A-x0.5": v["A3"]["labels"][lamA * 0.5], "JM-A-x2": v["A3"]["labels"][lamA * 2.0]}
        for g in GRID:
            lab[f"JM-A-lam{g:g}"] = v["A3"]["labels"][g]
        L = fm["full"]["L"].to_numpy()
        trm = np.asarray(idx <= train_end)
        q1, q2 = np.nanquantile(L[trm], [1 / 3, 2 / 3])
        lab["BASE-L3"] = np.where(np.isfinite(L), np.digitize(L, [q1, q2]), -1)
        adx = static["adx"].to_numpy()
        for thr in (20, 25, 30):
            lab[f"BASE-ADX{thr}"] = np.where(np.isfinite(adx), (adx > thr).astype(int), -1)
        lam_rows.append({"fold": k, "block": r3.label(ts, half), **{f"lam_{vv}": v[vv]["lam_star"] for vv in v},
                         **{f"valr2_A3_lam{t['lam']:g}": t["val_r2"] for t in v["A3"]["table"]},
                         **{f"valdur_A3_lam{t['lam']:g}": t["val_duration"] for t in v["A3"]["table"]}})
        tem = np.asarray((idx >= ts) & (idx < te))
        # ---- S4 usability on JM-A test labels
        a = lab["JM-A"][tem]
        a_ok = a >= 0
        off = fr["offline_A3"][tem]
        s4_parts.append({"fold": k, "labels": a[a_ok], "offline": off[a_ok]})
        # ---- regime description (test rows)
        F = fm["full"][tem]
        for s in range(3):
            sel = a == s
            regime_desc.append({"fold": k, "regime": NAMES3[s], "n": int(sel.sum()),
                                **{f"mean_{c}": float(F[c].to_numpy()[sel].mean()) if sel.any() else np.nan
                                   for c in ["L", "S_hd", "T"]}})
        chart_labels[k] = pd.Series(lab["JM-A"], index=idx)
        # ---- scoring per outcome
        outs = {"O1": fm["ds"]["y24_ds"].to_numpy(), "O2": static["E24"].to_numpy(),
                "O3": fm["ds"]["y4_ds"].to_numpy()}
        base_ok = np.isfinite(fm["full"][FEATS["A"]].to_numpy()).all(axis=1) & np.isfinite(
            static[["Z24", "adx"]].to_numpy()).all(axis=1)
        for o, y in outs.items():
            ok = base_ok & np.isfinite(y) & np.all([lab[n] >= 0 for n in lab], axis=0)
            tr, te_m = ok & trm, ok & tem
            assert idx[tr].max() + EMBARGO <= ts
            P = pd.DataFrame({"fold": k, "block": r3.label(ts, half), "era": r3.era_of(ts, cfg["eras"]),
                              "y": y[te_m], "BASE-0": y[tr].mean()}, index=idx[te_m])
            for n, l in lab.items():
                P[n] = cond_mean_forecast(l[tr], y[tr], l[te_m])
            P["lab_JM-A"], P["lab_BASE-L3"] = lab["JM-A"][te_m], lab["BASE-L3"][te_m]
            Xa = fm["full"][FEATS["A"]].to_numpy()
            tr_df = pd.DataFrame(Xa[tr], columns=FEATS["A"]).assign(y=y[tr])
            te_df = pd.DataFrame(Xa[te_m], columns=FEATS["A"]).assign(y=y[te_m])
            P["CONT"] = r1.ols_fit_predict(tr_df, te_df, FEATS["A"], "y")[0]
            rows_out[o].append(P)
        # ---- secondary strategies (all bars of the test block, regime at entry)
        c = static["close"].to_numpy()
        lc = np.log(c)
        for sname, h, sgn in (("TSMOM-24", 24, 1.0), ("MR-4", 4, -1.0)):
            past = lc - np.roll(lc, h)
            past[:h] = np.nan
            fwd = np.roll(c, -h) - c
            fwd[len(c) - h:] = np.nan
            pos = sgn * np.sign(past)
            pnl = pos * fwd - 2 * COST * np.abs(pos)
            sel = tem & np.isfinite(pnl) & (lab["JM-A"] >= 0)
            strat_rows.append(pd.DataFrame({"strategy": sname, "h": h, "regime": lab["JM-A"][sel],
                                            "pnl": pnl[sel]}, index=idx[sel]))
    return {"preds": {o: pd.concat(v).sort_index() for o, v in rows_out.items()},
            "lam": pd.DataFrame(lam_rows), "s4": s4_parts, "desc": pd.DataFrame(regime_desc),
            "strat": pd.concat(strat_rows).sort_index(), "chart": chart_labels, "static": static,
            "meta": {k: (m["ts"], m["te"]) for k, m in meta.items()},
            "centroids": {k: fold_res[k].get("centroids_A3") for k in fold_res}}


# =============================================================================
# Evaluation
# =============================================================================

LABELINGS = ["JM-A", "JM-B", "BASE-L3", "BASE-ADX20", "BASE-ADX25", "BASE-ADX30", "CONT",
             "JM-A-K2", "JM-A-K4", "JM-A-x0.5", "JM-A-x2"] + [f"JM-A-lam{g:g}" for g in GRID]


def pooled(P):
    return {n: float(r2_oos(P.y.to_numpy(), P[n].to_numpy(), P["BASE-0"].to_numpy())) for n in LABELINGS}


def dmt(P, a, b, lag):
    s, p = r1.dm_test(((P.y - P[a]) ** 2).to_numpy(), ((P.y - P[b]) ** 2).to_numpy(), lag)
    return {"dm": s, "p_A_better": p}


DM_PAIRS = [("JM-A", "BASE-0"), ("JM-A", "BASE-L3"), ("BASE-L3", "JM-A"), ("JM-A", "BASE-ADX25"),
            ("BASE-ADX25", "JM-A"), ("JM-B", "JM-A"), ("BASE-L3", "BASE-0"), ("CONT", "JM-A"),
            ("JM-A-x0.5", "BASE-0"), ("JM-A-x0.5", "BASE-L3"), ("BASE-L3", "JM-A-x0.5"),
            ("JM-A-x2", "BASE-0"), ("JM-A-x2", "BASE-L3"), ("BASE-L3", "JM-A-x2")]


def evaluate(preds):
    out = {}
    for o, P in preds.items():
        out[o] = {"rows": len(P), "r2": pooled(P),
                  "dm": {f"{a}|{b}": dmt(P, a, b, NW[o]) for a, b in DM_PAIRS},
                  "per_block": [{"block": b, "n": len(g), **pooled(g)} for b, g in P.groupby("block", sort=False)]}
    return out


def stage1_rules(ev, s4, prefix="JM-A"):
    p = lambda o, a, b: ev[o]["dm"][f"{a}|{b}"]["p_A_better"]

    def s1s2(jm):
        s1 = p("O1", jm, "BASE-0") < 0.05
        s2 = any(p(o, jm, "BASE-L3") < 0.05 and p(other, "BASE-L3", jm) >= 0.05
                 for o, other in (("O1", "O2"), ("O2", "O1")))
        return s1, s2

    S1, S2 = s1s2("JM-A")
    rob = {m: s1s2(f"JM-A-{m}") for m in ("x0.5", "x2")}
    S5 = all(r == (S1, S2) for r in rob.values())
    S6 = p("O1", "JM-B", "JM-A") < 0.05 or p("O2", "JM-B", "JM-A") < 0.05
    S4 = s4["mean_duration"] >= MIN_DURATION if s4 else None
    if not S1:
        rec = "JM-A carries no information about future volatility; not recommended"
    elif not S2:
        rec = "the jump model adds nothing over L terciles; use BASE-L3"
    elif S4 and S5:
        rec = f"use the jump model: {'JM-B (with Z24)' if S6 else 'JM-A'}"
    else:
        rec = "jump model not usable/robust (S4 or S5 failed); use BASE-L3"
    return {"S1": S1, "S2": S2, "S4": S4, "S5": S5, "S5_detail": {m: {"S1": a, "S2": b} for m, (a, b) in rob.items()},
            "S6": S6, "recommendation": rec,
            "p": {"S1_O1": p("O1", "JM-A", "BASE-0"),
                  "S2_O1_JM>L3": p("O1", "JM-A", "BASE-L3"), "S2_O2_JM>L3": p("O2", "JM-A", "BASE-L3"),
                  "S2_O1_L3>JM": p("O1", "BASE-L3", "JM-A"), "S2_O2_L3>JM": p("O2", "BASE-L3", "JM-A"),
                  "S3_O2_JM>ADX": p("O2", "JM-A", "BASE-ADX25"), "S3_O2_ADX>JM": p("O2", "BASE-ADX25", "JM-A"),
                  "S3_O1_JM>ADX": p("O1", "JM-A", "BASE-ADX25"), "S3_O1_ADX>JM": p("O1", "BASE-ADX25", "JM-A"),
                  "S6_O1": p("O1", "JM-B", "JM-A"), "S6_O2": p("O2", "JM-B", "JM-A")}}


def s4_stats(parts, months_per_block):
    lab = [q["labels"] for q in parts]
    runs = sum(1 + np.count_nonzero(np.diff(l)) for l in lab if len(l))
    n = sum(len(l) for l in lab)
    switches = sum(np.count_nonzero(np.diff(l)) for l in lab)
    allab = np.concatenate(lab)
    agree = float(np.mean(np.concatenate([q["labels"] == q["offline"] for q in parts])))
    lags, unmatched = [], 0
    for q in parts:
        c, o = q["labels"], q["offline"]
        for i in np.flatnonzero(np.diff(o)) + 1:
            j = np.flatnonzero(c[i:i + 241] == o[i])
            if len(j):
                lags.append(int(j[0]))
            else:
                unmatched += 1
    run_len = {s: [] for s in range(3)}
    for l in lab:
        if not len(l):
            continue
        cuts = np.flatnonzero(np.diff(l)) + 1
        for seg in np.split(l, cuts):
            run_len[int(seg[0])].append(len(seg))
    return {"mean_duration": n / runs, "switches_per_month": switches / (months_per_block * len(parts)),
            "time_share": {NAMES3[s]: float(np.mean(allab == s)) for s in range(3)},
            "mean_run_by_regime": {NAMES3[s]: float(np.mean(v)) if v else np.nan for s, v in run_len.items()},
            "causal_offline_agreement": agree, "mean_lag_bars": float(np.mean(lags)) if lags else np.nan,
            "median_lag_bars": float(np.median(lags)) if lags else np.nan,
            "offline_switches": len(lags) + unmatched, "unmatched_within_240": unmatched}


def per_fold_directional(preds, mult):
    out = []
    for b, g1 in preds["O1"].groupby("block", sort=False):
        g2 = preds["O2"][preds["O2"].block == b]
        r = lambda g, n: r2_oos(g.y.to_numpy(), g[n].to_numpy(), g["BASE-0"].to_numpy())
        jm = f"JM-A-{mult}"
        out.append({"block": b, "s1_dir": r(g1, jm) > 0,
                    "s2_dir": r(g1, jm) > r(g1, "BASE-L3") or r(g2, jm) > r(g2, "BASE-L3")})
    df = pd.DataFrame(out)
    return {"s1_dir_rate": float(df.s1_dir.mean()), "s2_dir_rate": float(df.s2_dir.mean())}


def strategy_tests(strat):
    from scipy import stats
    rows = []
    for sname, g in strat.groupby("strategy"):
        h = int(g.h.iat[0])
        D = np.column_stack([(g.regime.to_numpy() == s).astype(float) for s in range(3)])
        y = g.pnl.to_numpy()
        present = D.sum(axis=0) > 0
        D = D[:, present]
        names = [NAMES3[s] for s in range(3) if present[s]]
        beta, *_ = np.linalg.lstsq(D, y, rcond=None)
        u = y - D @ beta
        gm = D * u[:, None]
        S = gm.T @ gm
        for l in range(1, h + 1):
            G = gm[l:].T @ gm[:-l]
            S += (1 - l / (h + 1)) * (G + G.T)
        B = np.linalg.inv(D.T @ D)
        V = B @ S @ B
        for i, n in enumerate(names):
            t = beta[i] / np.sqrt(V[i, i])
            rows.append({"strategy": sname, "test": f"mean {n}", "estimate": beta[i], "t": t,
                         "n": int(D[:, i].sum()), "p": 2 * (1 - stats.norm.cdf(abs(t)))})
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                d = beta[j] - beta[i]
                se = np.sqrt(V[i, i] + V[j, j] - 2 * V[i, j])
                rows.append({"strategy": sname, "test": f"{names[j]} - {names[i]}", "estimate": d,
                             "t": d / se, "n": np.nan, "p": 2 * (1 - stats.norm.cdf(abs(d / se)))})
    df = pd.DataFrame(rows)
    m = len(df)
    order = np.argsort(df.p.to_numpy())
    thresh = 0.10 * (np.arange(1, m + 1) / m)
    passed = df.p.to_numpy()[order] <= thresh
    kmax = np.max(np.flatnonzero(passed)) + 1 if passed.any() else 0
    df["bh_fdr10_reject"] = False
    df.loc[df.index[order[:kmax]], "bh_fdr10_reject"] = True
    return df


def chart(res, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    static, meta = res["static"], res["meta"]
    colors = {0: "#2e7d32", 1: "#bdbdbd", 2: "#c62828"}
    fig, axes = plt.subplots(4, 1, figsize=(14, 13))
    for ax, year in zip(axes, [2008, 2011, 2013, 2020]):
        seg = static.loc[str(year), "close"]
        lab = pd.Series(-1, index=seg.index)
        note = "test labels"
        for k, (ts, te) in meta.items():
            mask = (seg.index >= ts) & (seg.index < te)
            lab[mask] = res["chart"][k].reindex(seg.index[mask]).to_numpy()
        if year < 2009:
            lab[:] = res["chart"][1].reindex(seg.index).to_numpy()
            note = "fold 1 causal labels inside its TRAINING window (in-sample fit)"
        x = np.arange(len(seg))
        ax.plot(x, seg.to_numpy(), color="black", lw=0.7)
        for s, col in colors.items():
            m = (lab.to_numpy() == s)
            ax.fill_between(x, seg.min(), seg.max(), where=m, color=col, alpha=0.25, step="mid", lw=0)
        ticks = np.linspace(0, len(seg) - 1, 7).astype(int)
        ax.set_xticks(ticks, [seg.index[i].strftime("%b %d") for i in ticks])
        ax.set_title(f"XAUUSD {year}: JM-A causal regimes ({note}); green CALM, grey NORMAL, red STRESSED")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def main() -> int:
    OUT.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    workers = min(6, max(1, (os.cpu_count() or 2) - 1))
    allres = {}
    for name, cfg in PERIODS.items():
        res = run_period(name, cfg, workers)
        ev = evaluate(res["preds"])
        s4 = s4_stats(res["s4"], 6 if name == "CONF" else 3)
        dec = {"overall": stage1_rules(ev, s4)}
        eras = {}
        for era in (cfg["eras"] or {}):
            pe = {o: P[P.era == era] for o, P in res["preds"].items()}
            eve = evaluate(pe)
            parts = [q for q in res["s4"] if r3.era_of(res["meta"][q["fold"]][0], cfg["eras"]) == era]
            dec[era] = stage1_rules(eve, s4_stats(parts, 6))
            eras[era] = eve
        strat = strategy_tests(res["strat"])
        strat.to_csv(OUT / f"strategies_{name}.csv", index=False, float_format="%.6g")
        res["lam"].to_csv(OUT / f"lambda_{name}.csv", index=False, float_format="%.6g")
        res["desc"].to_csv(OUT / f"regime_desc_{name}.csv", index=False, float_format="%.6g")
        for o, P in res["preds"].items():
            P.to_csv(OUT / f"predictions_{name}_{o}.csv", float_format="%.6f")
        if name == "CONF":
            chart(res, FIG / "regimes_2008_2011_2013_2020.png")
        allres[name] = {"eval": ev, "eras": eras, "s4": s4, "decisions": dec,
                        "robust_dir": {m: per_fold_directional(res["preds"], m) for m in ("x0.5", "x2")},
                        "centroids_A3": {int(k): v for k, v in res["centroids"].items()}}
        for scope, d in dec.items():
            log(f"  {name}/{scope}: S1={d['S1']} S2={d['S2']} S4={d['S4']} S5={d['S5']} S6={d['S6']} -> {d['recommendation']}")
        log(f"  {name}: R2 O1 " + " ".join(f"{n}={ev['O1']['r2'][n]:.4f}" for n in LABELINGS[:7]))
    (OUT / "results_regime.json").write_text(json.dumps(allres, indent=1, default=float))
    log("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
