#!/usr/bin/env python3
"""
EXPLORATORY analyses (not pre-registered, not confirmatory). See REPORT.md
section 7. Same data, sealing, sample and walk-forward as run_study.py.

A1  Intraday seasonality content of each feature: eta² = share of variance
    explained by UTC hour-of-day (initial training window, 2021).
A2  Does M1's 4h advantage come from time-of-day information? Refit M0/M1/M2
    with 23 hour-of-day dummies added and compare pooled OOS R², with DM.
A3  Is wick60 redundant with the classifier's existing persistence features
    (autocorr_lag1, variance_ratio_2/5, hurst_dfa)? Spearman, 2021 window.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_study as rs  # noqa: E402


def hour_dummies(idx: pd.DatetimeIndex) -> pd.DataFrame:
    d = pd.get_dummies(idx.hour, prefix="h").astype(float)
    d.index = idx
    return d.drop(columns=[c for c in d.columns if c == "h_0"])


def eta2(x: pd.Series) -> float:
    g = x.groupby(x.index.hour)
    return float(((g.transform("mean") - x.mean()) ** 2).sum() / ((x - x.mean()) ** 2).sum())


def main() -> int:
    rs.log("EXPLORATORY: loading data (sealed)")
    m, _ = rs.load_minutes()
    bars, ds = rs.build_dataset(m)
    a = ds[(ds.index >= rs.ANALYSIS_START) & (ds.index < rs.SEAL)].copy()
    rs.assert_sealed(a, "exploratory analysis frame")
    train1_end = rs.QUARTER_EDGES[0] - rs.EMBARGO
    out: dict = {}

    # ---- A1 ---------------------------------------------------------------
    t1 = a[a.index <= train1_end]
    out["A1_eta2_hour_of_day_2021"] = {
        f: round(eta2(t1[f].dropna()), 3) for f in rs.ALL16 + rs.HAR + ["y4", "y24"]
    }
    rs.log(f"A1 eta² by hour: {out['A1_eta2_hour_of_day_2021']}")

    # ---- A2 ---------------------------------------------------------------
    H = hour_dummies(a.index)
    a2 = pd.concat([a, H], axis=1)
    hcols = list(H.columns)
    feats_all = sorted(set(sum(rs.MODELS.values(), [])))
    models = {"M0": rs.HAR, "M1": rs.EXISTING, "M2": rs.NEW, "HOD": [],
              "M0+HOD": rs.HAR, "M1+HOD": rs.EXISTING, "M2+HOD": rs.NEW,
              "M3+HOD": rs.HAR + rs.NEW, "M4+HOD": rs.HAR + rs.EXISTING}
    a2_out = {}
    for t in rs.HORIZONS:
        sample = a2.dropna(subset=feats_all + [t])   # same rows as the pre-registered study
        preds = []
        for k, train_end, ts, te_ in rs.folds():
            tr = sample[(sample.index >= rs.ANALYSIS_START) & (sample.index <= train_end)]
            te = sample[(sample.index >= ts) & (sample.index < te_)]
            p = pd.DataFrame({"y": te[t], "bench": tr[t].mean()}, index=te.index)
            for name, f in models.items():
                cols = f + (hcols if "HOD" in name else [])
                p[name] = rs.ols_fit_predict(tr, te, cols, t)[0]
            preds.append(p)
        P = pd.concat(preds).sort_index()
        bsse = ((P.y - P.bench) ** 2).sum()
        r2 = {n: round(float(1 - ((P.y - P[n]) ** 2).sum() / bsse), 4) for n in models}
        dm = {}
        for A, B in [("M1", "M2"), ("M1+HOD", "M2+HOD"), ("M4+HOD", "M3+HOD")]:
            s, pv = rs.dm_test(((P.y - P[A]) ** 2).to_numpy(), ((P.y - P[B]) ** 2).to_numpy(),
                               rs.NW_LAG[t])
            dm[f"{A} vs {B}"] = {"dm": round(s, 3), "p_A_better": round(pv, 4)}
        a2_out[t] = {"n_rows": len(sample), "r2": r2, "dm": dm}
        rs.log(f"A2 {t}: {r2} {dm}")
    out["A2_hour_of_day_controls"] = a2_out

    # ---- A3 ---------------------------------------------------------------
    rs.log("A3: computing existing persistence features (slow DFA loop)")
    rf, rc = rs._import_feature_engine()
    cfg = rc.RegimeConfig()
    eng = rf.RegimeFeatureEngine(cfg)
    df = eng._normalize_columns(bars[["open", "high", "low", "close"]])
    lr = np.log(df["close"] / df["close"].shift(1))
    pers = pd.DataFrame({
        "autocorr_lag1": eng._rolling_autocorrelation(df["close"].pct_change(), cfg.autocorr_window),
        "variance_ratio_2": eng._variance_ratio(lr, 2, cfg.variance_ratio_window),
        "variance_ratio_5": eng._variance_ratio(lr, 5, cfg.variance_ratio_window),
        "hurst_dfa": eng._hurst_dfa(lr),
    }, index=df.index)
    j = pd.concat([a[rs.NEW], pers], axis=1, join="inner")
    j = j[j.index <= train1_end].dropna()
    rho = j.corr(method="spearman").loc[rs.NEW, list(pers.columns)]
    out["A3_new_vs_persistence_spearman_2021"] = rho.round(3).to_dict()
    rs.log(f"A3:\n{rho.round(3)}")

    (rs.OUT / "exploratory.json").write_text(json.dumps(out, indent=2))
    rs.log("Done (exploratory)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
