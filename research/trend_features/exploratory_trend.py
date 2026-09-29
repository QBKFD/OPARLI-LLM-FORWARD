#!/usr/bin/env python3
"""
EXPLORATORY (declared in PREREG, not a decision rule). REPORT.md section 7.

Does signed direction predict the SIGN of the next 24h return? In CONF,
walk-forward OLS of sign(next-24h log return) on [di_bias, signed Z24], same
folds. Pooled OOS hit rate of sign(forecast) vs 50 %, Newey-West (lag 48)
t-test on the hit indicator. Also reported per era and for each regressor
alone (descriptive).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_trend as rt  # noqa: E402

r1, r3 = rt.r1, rt.r3


def hit_test(hits: np.ndarray, lag: int = 48):
    d = hits - 0.5
    lrv = r1.newey_west_lrv(d, lag)
    t = d.mean() / np.sqrt(lrv / len(d))
    from scipy import stats
    return float(hits.mean()), float(t), float(1 - stats.norm.cdf(t))


def main() -> int:
    cfg = r3.PERIODS["CONF"]
    m = r3.load_conf()
    bars, st = rt.static_frame(m, cfg["end"])
    F = st[(st.index >= cfg["start"]) & (st.index < cfg["end"])].copy()
    r3.assert_sealed(F, "exploratory frame", end=cfg["end"])
    F = F.dropna(subset=["di_bias", "Z24_signed", "ret24"])
    F = F[F["ret24"] != 0]
    F["sgn"] = np.sign(F["ret24"])
    specs = {"di_bias+Z24_signed": ["di_bias", "Z24_signed"], "di_bias": ["di_bias"],
             "Z24_signed": ["Z24_signed"]}
    out = {"rows": len(F)}
    for name, feats in specs.items():
        preds = []
        for k, train_end, ts, te in r3.folds(cfg["edges"]):
            tr, tst = F[F.index <= train_end], F[(F.index >= ts) & (F.index < te)]
            yhat = r1.ols_fit_predict(tr, tst, feats, "sgn")[0]
            preds.append(pd.DataFrame({"hit": (np.sign(yhat) == tst["sgn"].to_numpy()).astype(float),
                                       "era": r3.era_of(ts, cfg["eras"])}, index=tst.index))
        P = pd.concat(preds).sort_index()
        res = {"all": dict(zip(["hit_rate", "t", "p_above_50"], hit_test(P["hit"].to_numpy())))}
        for era in cfg["eras"]:
            res[era] = dict(zip(["hit_rate", "t", "p_above_50"], hit_test(P.loc[P.era == era, "hit"].to_numpy())))
        res["test_rows"] = len(P)
        out[name] = res
    out["share_up_days_in_test"] = float((F.loc[F.index >= "2009-01-01", "sgn"] > 0).mean())
    (rt.OUT / "exploratory_trend.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
