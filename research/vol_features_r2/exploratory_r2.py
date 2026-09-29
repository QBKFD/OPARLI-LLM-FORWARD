#!/usr/bin/env python3
"""
EXPLORATORY (not pre-registered, not confirmatory). REPORT.md section 7.

Why does S keep a clock (eta² 0.15) after deseasonalization, and what would
remove it?

E1  Where the clock sits: mean S by UTC hour, and eta² of the bar's minute
    count (bars around the daily break are short).
E2  Candidate causes, as eta² on the CONF training windows:
      S_mean  = per-minute means instead of sums (removes the minute-count effect)
      level   = eta² of dsRV_1h itself (no log), vs log dsRV_1h (Jensen effect)
E3  Candidate fix: S_hd = S minus its training-window mean by UTC hour. The
    same CONF folds and rows as the pre-registered run: R² of MC(S_hd) and of
    MC(S_hd) + hour dummies, i.e. the R2 test re-run on the fixed feature.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_r2 as r2  # noqa: E402

r1 = r2.r1


def main() -> int:
    out = {}
    cfg = r2.PERIODS["CONF"]
    m = r2.load_conf()
    r, flag = r1.minute_returns(m)
    bucket = r2.minute_buckets(m.index)
    bars, st = r2.static_features(m, r, flag)
    start, end = cfg["start"], cfg["end"]
    inp = (bars.index >= start) & (bars.index < end)
    t = bars.index
    _, n1 = r2.window_sum(m.index, np.where(np.isnan(r.to_numpy()), np.nan, 1.0),
                          (t - pd.Timedelta(hours=1)).asi8, t.asi8)
    nbar = pd.Series(n1, index=t, dtype=float)

    rows, fits = [], {tg: [] for tg in r2.HORIZONS}
    for k, train_end, ts, te in r2.folds(cfg["edges"]):
        prof = r2.fit_profile(m.index, r, bucket, start, train_end)
        ds = r2.ds_features(t, m.index, r, bucket, prof, end)
        rds2 = r.to_numpy() ** 2 / prof[bucket]
        s1, c1 = r2.window_sum(m.index, rds2, (t - pd.Timedelta(hours=1)).asi8, t.asi8)
        per_bar = pd.Series(np.where(c1 > 0, s1, np.nan), index=t)
        cnt = pd.Series(np.where(c1 > 0, c1, np.nan), index=t)
        mean1 = per_bar / cnt
        mean24 = per_bar.rolling(24, min_periods=24).sum() / cnt.rolling(24, min_periods=24).sum()
        F = pd.concat([st, ds], axis=1)
        F["S_mean"] = np.log(mean1) - np.log(mean24)
        F["ds1_level"] = per_bar
        F["n_bar"] = nbar
        F = F[inp]
        tr = F[F.index <= train_end]
        hour_mean = tr["S"].groupby(tr.index.hour).mean()
        F["S_hd"] = F["S"] - F.index.hour.map(hour_mean).to_numpy()
        tr = F[F.index <= train_end]
        rows.append({"fold": k, **{c: r2.eta2(tr[c]) for c in
                                   ["S", "S_mean", "S_hd", "ds1", "ds1_level", "n_bar"]}})
        if k == 1:
            prof_S = tr["S"].groupby(tr.index.hour).mean()
            prof_n = tr["n_bar"].groupby(tr.index.hour).mean()
            out["E1_first_train_mean_S_by_hour"] = prof_S.round(3).to_dict()
            out["E1_first_train_mean_minutes_per_bar_by_hour"] = prof_n.round(1).to_dict()
        for tg in r2.HORIZONS:
            cols = sorted(set(sum(r2.MODELS.values(), []))) + [tg]   # same rows as the prereg run
            S = F.dropna(subset=cols + ["S_hd", "S_mean"])
            trn, tst = S[S.index <= train_end], S[(S.index >= ts) & (S.index < te)]
            P = pd.DataFrame({"y": tst[tg], "bench": trn[tg].mean()}, index=tst.index)
            for name, feats in {"MC": r2.C4, "MCH": r2.C4 + r2.HOD,
                                "MC_hd": ["L", "S_hd", "T", "W"],
                                "MC_hd_H": ["L", "S_hd", "T", "W"] + r2.HOD,
                                "MC_mean": ["L", "S_mean", "T", "W"],
                                "MC_mean_H": ["L", "S_mean", "T", "W"] + r2.HOD,
                                "L_T_W": ["L", "T", "W"]}.items():
                P[name] = r1.ols_fit_predict(trn, tst, feats, tg)[0]
            fits[tg].append(P)
    eta = pd.DataFrame(rows).set_index("fold")
    out["E2_eta2_max_over_train_windows"] = eta.max().round(4).to_dict()
    out["E2_eta2_first_train_window"] = eta.iloc[0].round(4).to_dict()
    for tg, ps in fits.items():
        P = pd.concat(ps).sort_index()
        bsse = ((P.y - P.bench) ** 2).sum()
        r2s = {c: round(float(1 - ((P.y - P[c]) ** 2).sum() / bsse), 4)
               for c in P.columns if c not in ("y", "bench")}
        out[f"E3_{tg}_r2"] = r2s
        out[f"E3_{tg}_gain_from_hod"] = {"MC": round(r2s["MCH"] - r2s["MC"], 4),
                                        "MC_hd": round(r2s["MC_hd_H"] - r2s["MC_hd"], 4),
                                        "MC_mean": round(r2s["MC_mean_H"] - r2s["MC_mean"], 4)}
        s, p = r1.dm_test(((P.y - P.MC_hd) ** 2).to_numpy(), ((P.y - P.MC) ** 2).to_numpy(), r2.NW_LAG[tg])
        out[f"E3_{tg}_dm_MC_hd_vs_MC"] = {"dm": round(s, 3), "p_MC_hd_better": round(p, 4)}
    (r2.OUT / "exploratory_r2.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
