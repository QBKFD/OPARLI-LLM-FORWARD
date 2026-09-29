#!/usr/bin/env python3
"""
EXPLORATORY (not pre-registered, not a decision rule). REPORT.md section 8.

Why do L terciles beat JM-A? Two candidate reasons:
  (a) persistence/lag: large λ makes labels stable but slow;
  (b) dilution: the equal-weight distance on (L, S_hd, T) lets S_hd and T shape
      the states, while next-day volatility depends mostly on L.
Test: a jump model on L alone (JM-L), with λ chosen by the same pre-registered
procedure, and JM-L at λ = 0 (1-D k-means). Same folds, same scoring rows as
the main run (read from results/predictions_*.csv).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_regime as rr  # noqa: E402
from jump import JumpModel, order_by  # noqa: E402

OUTS = {"O1": "y24_ds", "O2": "E24", "O3": "y4_ds"}


def jm_l_labels(job, lam=None):
    t_ns = job["t_ns"]
    Fs, Ff = job["sel"][["L"]], job["full"][["L"]]
    ok_s = np.isfinite(Fs).all(axis=1) & (t_ns <= job["train_end"])
    ts_s = t_ns[ok_s]
    fit_mask, val_mask = ts_s <= job["fit_end"], ts_s >= job["val_start"]
    Xs, _, _ = rr.standardize(Fs[ok_s][fit_mask], Fs[ok_s])
    if lam is None:
        lam, _ = rr.select_lambda(Xs, fit_mask, val_mask, job["sel_y24"][ok_s], 3, job["fold"], "A")
    ok_f = np.isfinite(Ff).all(axis=1)
    tr = t_ns[ok_f] <= job["train_end"]
    Xf, _, _ = rr.standardize(Ff[ok_f][tr], Ff[ok_f])
    jm = JumpModel(K=3, lam=lam, n_init=rr.N_INIT, seed=rr.seed_for(job["fold"], "A", 7, 0)).fit(Xf[tr])
    rank = order_by(Ff[ok_f][tr][:, 0], jm.labels_, 3)
    out = np.full(len(t_ns), -1, np.int64)
    out[ok_f] = rank[jm.causal_labels(Xf)]
    return out, lam


def main() -> int:
    res = {}
    for name, cfg in rr.PERIODS.items():
        static, jobs, meta = rr.prepare(name, cfg)
        idx = static.index
        labs = {}
        for job in jobs:
            k = job["fold"]
            sel, lam = jm_l_labels(job)
            zero, _ = jm_l_labels(job, lam=0.0)
            labs[k] = {"JM-L": sel, "JM-L-lam0": zero, "lam": lam}
            rr.log(f"  {name} fold {k}: JM-L λ={lam:g}")
        out = {"lambda_JM-L": [labs[k]["lam"] for k in sorted(labs)]}
        for o, col in OUTS.items():
            P = pd.read_csv(rr.OUT / f"predictions_{name}_{o}.csv", index_col=0, parse_dates=True)
            P.index = pd.to_datetime(P.index, utc=True)
            y_all = (static["E24"] if col == "E24" else None)
            for n in ("JM-L", "JM-L-lam0"):
                P[n] = np.nan
            for k, fm in meta.items():
                y = y_all.to_numpy() if y_all is not None else fm["ds"][col].to_numpy()
                base_ok = np.isfinite(fm["full"][rr.FEATS["A"]].to_numpy()).all(axis=1) & \
                    np.isfinite(static[["Z24", "adx"]].to_numpy()).all(axis=1) & np.isfinite(y)
                trm = np.asarray(idx <= fm["train_end"]) & base_ok
                te_idx = P.index[P.fold == k]
                pos = idx.get_indexer(te_idx)
                for n in ("JM-L", "JM-L-lam0"):
                    l = labs[k][n]
                    trk = trm & (l >= 0)
                    P.loc[te_idx, n] = rr.cond_mean_forecast(l[trk], y[trk], l[pos])
            r2 = {n: float(rr.r2_oos(P.y.to_numpy(), P[n].to_numpy(), P["BASE-0"].to_numpy()))
                  for n in ("JM-A", "BASE-L3", "JM-L", "JM-L-lam0", "CONT")}
            dm = {f"{a}|{b}": rr.dmt(P, a, b, rr.NW[o]) for a, b in
                  (("JM-L", "JM-A"), ("BASE-L3", "JM-L"), ("JM-L", "BASE-L3"))}
            out[o] = {"r2": r2, "dm": dm}
            rr.log(f"  {name} {o}: " + " ".join(f"{n}={v:.4f}" for n, v in r2.items()))
        res[name] = out
    (rr.OUT / "exploratory_regime.json").write_text(json.dumps(res, indent=1, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
