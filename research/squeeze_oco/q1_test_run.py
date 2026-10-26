#!/usr/bin/env python3
"""The Q1 test, run once (owner, 2026-10-26, decision log #39-#41): HAR vs
HAR + the small-candle pattern on the dev period's test years, read by
Q1_READING_RULE. THIS IS AN OUTCOME ANALYSIS: it refuses to start unless
ATTEMPTS.json is at 0, and increments it before any RV is read. Writes
results/q1_test.json; the notebook shows that file and does not rerun it.

    python research/squeeze_oco/q1_test_run.py [--check]

--check runs everything before the attempt (register, data, scores) and stops.

Every value comes from the notebook's register through need() (q1_counts.register)."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import q1  # noqa: E402
import stage1  # noqa: E402
from q1_counts import register  # noqa: E402
from stage1 import posthoc, rs  # noqa: E402

OUT = HERE / "results" / "q1_test.json"


def one_q(need, ctx, model, tbl, sc, q, DEV_START, DEV_END, years, test_years, first, emb, TK):
    """Runs for (q, Q1_RUN_LENGTH), the rows, the folds, and per H the losses and DM."""
    r = need("Q1_RUN_LENGTH")
    thr = q1.thresholds(sc["score"], q, years, TK["hour_tz"])
    run = q1.runs_at_labels(sc, tbl.index, thr, need("Q1_TIMEFRAME"), r, TK["hour_tz"])
    rows = q1.scored_rows(ctx, model, DEV_START, DEV_END, run)
    out = {}
    for H, g in rows.items():
        stage1.dev_guard(g.index, DEV_START, DEV_END, f"Q1 rows H={H}")
        recs, te = q1.folds(g, test_years, first, emb)
        la, lb = q1.losses(te)
        pat = te["I"].to_numpy() == 1
        out[H] = {"recs": recs, "te": te, "la": la, "lb": lb, "pat": pat,
                  "dm": q1.dm_two_sided(la, lb, need(f"Q1_NW_LAG_{H}"))}
    return out


def describe(res, H, NY):
    te, la, lb, pat = res["te"], res["la"], res["lb"], res["pat"]
    p, o = te[pat], te[~pat]
    years = sorted(te["fold"].unique())
    ny_hour = (p.index - pd.Timedelta(minutes=15)).tz_convert(NY).hour
    return {
        "n_test_rows": int(len(te)), "n_test_pattern_rows": int(pat.sum()),
        "qlike_all_rows": {"HAR": float(la.mean()), "HAR+pattern": float(lb.mean())},
        "qlike_pattern_rows": {"HAR": float(la[pat].mean()), "HAR+pattern": float(lb[pat].mean())},
        "dm": res["dm"],
        "gamma_per_fold": {str(x["fold"]): x["gamma"] for x in res["recs"]},
        "folds": res["recs"],
        "gamma_pooled_oos": q1.gamma(te),
        "gamma_per_test_year_oos": {str(Y): q1.gamma(te[te["fold"] == Y]) for Y in years},
        "conditional_calibration_HAR": {"pattern_rows": {"n": int(len(p)), **posthoc.calib(p, "h")},
                                        "other_rows": {"n": int(len(o)), **posthoc.calib(o, "h")}},
        "per_test_year": {str(Y): {"n_pattern": int(((te["fold"] == Y) & pat).sum()),
                                   "mean_d_pattern_rows": float((la - lb)[(te["fold"] == Y).to_numpy() & pat].mean())
                                   if (((te["fold"] == Y).to_numpy()) & pat).any() else None,
                                   "mean_rv_over_h_pattern": float((p["rv"] / p["h"])[p["fold"] == Y].mean())
                                   if (p["fold"] == Y).any() else None} for Y in years},
        "per_ny_hour_pattern_rows": {str(h): {"n": int((ny_hour == h).sum()),
                                              "mean_rv_over_h": float((p["rv"] / p["h"])[ny_hour == h].mean())}
                                     for h in sorted(set(ny_hour))},
    }


def main() -> int:
    if stage1.attempts()["outcome_analyses"] != 0:
        raise SystemExit("ATTEMPTS.json is not 0: Q1 has already been run once; it is not rerun")
    need = register()["need"]
    DEV_START, DEV_END = pd.Timestamp(need("DEV_START")), pd.Timestamp(need("DEV_END_EXCL"))
    FO, RULE, TK = need("Q1_FOLDS"), need("Q1_READING_RULE"), need("Q1_THRESHOLD_KIND")
    assert need("Q1_AUG_FIT").startswith("the pinned HAR") and need("Q1_AUG_ESTIMATOR")["gamma"].startswith("ln(mean(RV / h_HAR))")
    assert need("Q1_H") == {"primary": 240, "secondary": 60, "multiplicity": "BH across the two"}
    assert need("Q1_SMALL_DEF")["option"] == "B" and TK["kind"] == "quantile per hour"
    test_years = list(range(2014, 2021))
    assert FO["test_blocks"].startswith("[Y-01-01, Y+1-01-01) UTC for Y = 2014 .. 2020 (7 folds)")
    first, emb = pd.Timestamp("2013-01-01", tz="UTC"), pd.Timedelta(hours=24)
    assert FO["embargo"] == "24 h" and FO["training_rows"].startswith("labels t with 2013-01-01 <= t <= s_k - 24 h")
    years = list(range(DEV_START.year, DEV_END.year))
    m, checks = stage1.load_dev(need("DATA_KEY"), DEV_START, DEV_END, need("CACHE_CHECK"))
    assert checks["manifest_sha256"] == need("DATA_SHA256")
    model = stage1.load_model(need("SIGMA_MODEL"), need("SIGMA_MODEL_SHA256"))
    ctx = stage1.context(m, DEV_END, need("HORIZONS"), need("CALIB_COVERAGE"))
    tbl = stage1.state_table(ctx, model, DEV_START, DEV_END)
    sc = q1.candle_scores(m, ctx, model, need("Q1_TIMEFRAME"), need("Q1_SMALL_DEF")["level_H"])
    q_main = need("Q1_SMALL_Q")
    q_sens = [q for q in need("Q1_RULE")["q_order"] if q != q_main]

    if "--check" in sys.argv:          # everything before the attempt, no RV read: then stop
        print(f"check ok: {len(sc):,} candles, {len(tbl):,} grid labels, q {q_main}, sensitivity {q_sens}; attempts still 0")
        return 0
    n = stage1.bump_attempt("Q1: two-sided DM, HAR vs HAR + small-candle pattern (q 0.20, r 4, per-NY-hour threshold), "
                            "H = 240 primary / 60, BH; folds 2014-2020", "owner, 2026-10-26 (decision log #39-#41)")
    assert n == 1
    # ---- from here on, RV is read
    main_res = one_q(need, ctx, model, tbl, sc, q_main, DEV_START, DEV_END, years, test_years, first, emb, TK)
    bh = rs.bh({H: main_res[H]["dm"]["p"] for H in main_res}, need("BH_Q"))
    adds = bool(bh[240]["reject"])
    NY = TK["hour_tz"]
    desc = {H: describe(main_res[H], H, NY) for H in main_res}
    g240 = desc[240]
    minyr = RULE["min_positive_years"]
    gate_oos = q1.q2_gate(adds, g240["gamma_pooled_oos"], list(g240["gamma_per_test_year_oos"].values()), minyr)
    gate_fold = q1.q2_gate(adds, g240["gamma_pooled_oos"], list(g240["gamma_per_fold"].values()), minyr)
    sens = {}
    for q in q_sens:
        r_ = one_q(need, ctx, model, tbl, sc, q, DEV_START, DEV_END, years, test_years, first, emb, TK)
        sens[str(q)] = {str(H): {"n_test_pattern_rows": int(x["pat"].sum()), "dm": x["dm"],
                                 "gamma_pooled_oos": q1.gamma(x["te"]),
                                 "gamma_per_fold": {str(f["fold"]): f["gamma"] for f in x["recs"]}} for H, x in r_.items()}
    out = {"what": "Q1 test, run once (ATTEMPTS 1). Aggregates only: no price row",
           "attempt": n, "dev_period_utc": [DEV_START.isoformat(), DEV_END.isoformat()], "checks": checks,
           "model": {"name": model.name, "sha256": model.sha256},
           "q": q_main, "r": need("Q1_RUN_LENGTH"), "test_years": test_years,
           "verdict": {"dm_240": main_res[240]["dm"], "bh": {str(H): v for H, v in bh.items()},
                       "pattern_adds_information": adds,
                       "direction_240": "HAR+pattern lower QLIKE" if main_res[240]["dm"]["stat"] > 0 else "HAR lower QLIKE (or equal)",
                       "q2_proceeds_gamma_oos_reading": gate_oos, "q2_proceeds_gamma_fold_reading": gate_fold,
                       "positive_years_oos": int(sum(v > 0 for v in g240["gamma_per_test_year_oos"].values())),
                       "positive_years_fold": int(sum(v > 0 for v in g240["gamma_per_fold"].values()))},
           "per_H": {str(H): {k: v for k, v in d.items()} for H, d in desc.items()},
           "sensitivity_descriptive": sens}
    OUT.write_text(json.dumps(out, indent=1, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)) + "\n")
    print(json.dumps(out["verdict"], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
