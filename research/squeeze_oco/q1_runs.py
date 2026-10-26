#!/usr/bin/env python3
"""Q1 event counts (owner's Q1 order, step 2): the option-B scores, the
walk-forward thresholds and the runs for every (q, r) Q1_RULE names, per year
and in total, overlapping and not, with how clustered the runs are; then the
rule's (q, r). Counts only: no forward return, RV or outcome is read, and
ATTEMPTS.json is not touched. Writes results/q1_run_counts.json.

    python research/squeeze_oco/q1_runs.py

Every value comes from the notebook's register through need() (q1_counts.register)."""
import json
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import q1  # noqa: E402
import stage1  # noqa: E402
from q1_counts import register  # noqa: E402

OUT = HERE / "results" / "q1_run_counts.json"


def key(q, r) -> str:
    return f"q={q},r={r}"


def main() -> int:
    need = register()["need"]
    DEV_START, DEV_END = pd.Timestamp(need("DEV_START")), pd.Timestamp(need("DEV_END_EXCL"))
    m, checks = stage1.load_dev(need("DATA_KEY"), DEV_START, DEV_END, need("CACHE_CHECK"))
    assert checks["manifest_sha256"] == need("DATA_SHA256")
    model = stage1.load_model(need("SIGMA_MODEL"), need("SIGMA_MODEL_SHA256"))
    ctx = stage1.context(m, DEV_END, need("HORIZONS"), need("CALIB_COVERAGE"))
    tbl = stage1.state_table(ctx, model, DEV_START, DEV_END)
    assert need("EVAL_GRID")
    TF, SD, RULE, H = need("Q1_TIMEFRAME"), need("Q1_SMALL_DEF"), need("Q1_RULE"), need("Q1_H")
    TK = need("Q1_THRESHOLD_KIND")
    assert SD["option"] == "B" and SD["range"] == "high - low" and TK["kind"] == "quantile per hour"
    assert need("Q1_RUN_ANCHOR")
    years = list(range(DEV_START.year, DEV_END.year))
    assert (pd.Timestamp(RULE["span"][0], tz="UTC"), pd.Timestamp(RULE["span"][1], tz="UTC")) == (DEV_START, DEV_END)
    sc = q1.candle_scores(m, ctx, model, TF, SD["level_H"])
    stage1.dev_guard(sc.index, DEV_START, DEV_END, "Q1 candle scores")
    qs = tuple(need("DESCRIBE_QUANTILES"))
    ec = q1.event_counts(sc, tbl[[f"crossing_{h}" for h in need("HORIZONS")]], years, RULE["q_order"], RULE["r_order"],
                         TF, (H["primary"], H["secondary"]), qs, TK["hour_tz"])
    rule_args = (RULE["r_order"], RULE["q_order"], RULE["min_runs"])
    chosen = q1.choose({k: v["runs"] for k, v in ec["totals"].items()}, *rule_args)
    compare = {alt: q1.choose({k: v[alt] for k, v in ec["totals"].items()}, *rule_args)
               for alt in ("runs_noncrossing_240", "runs_noncrossing_60")}
    compare |= {f"greedy_spaced_{S}min": q1.choose({k: c[f"greedy_spaced_{S}min"] for k, c in ec["clustering"].items()}, *rule_args)
                for S in (H["primary"], H["secondary"])}
    ok = sc["score"].notna()
    score_q = sc.loc[ok, "score"].groupby(sc.index[ok].year).quantile(list(qs)).unstack()
    score_h = sc.loc[ok, "score"].groupby(sc.index[ok].hour).quantile(list(qs)).unstack()      # UTC hour of the candle's start
    out = {"what": "Q1 event counts: option-B candle scores, walk-forward thresholds per New York hour, runs at hourly labels; "
                   "counts and score quantiles only (no price, no forward return, no outcome)",
           "dev_period_utc": [DEV_START.isoformat(), DEV_END.isoformat()], "checks": checks,
           "model": {"name": model.name, "sha256": model.sha256},
           "timeframe_min": TF, "rule": {k: list(v) if isinstance(v, tuple) else v for k, v in RULE.items()},
           "grid_labels_with_state": int(len(tbl)),
           "candles": {"complete": int(len(sc)), "scored": int(ok.sum()),
                       "with_a_closed_minute": int((sc["closed_minutes"] > 0).sum()),
                       "scored_per_year": {str(y): int(n) for y, n in sc.loc[ok].groupby(sc.index[ok].year).size().items()}},
           "score_quantiles_per_year": {str(y): {str(p): float(v) for p, v in row.items()} for y, row in score_q.iterrows()},
           "score_quantiles_per_utc_hour_of_start": {str(h): {str(p): float(v) for p, v in row.items()} for h, row in score_h.iterrows()},
           "threshold_kind": TK,
           "supersedes": "the pooled-threshold counts of commit bacf336 (decision log #28, #31, #32)",
           "thresholds": {str(q): {f"{y},ny_hour={h}": v for (y, h), v in t.items()} for q, t in ec["thresholds"].items()},
           "per_ny_hour": {key(q, r): {str(h): v for h, v in ph.items()} for (q, r), ph in ec["per_hour"].items()},
           "per_year": {key(q, r): {str(y): v for y, v in yrs.items()} for (q, r), yrs in ec["per_year"].items()},
           "totals": {key(q, r): v for (q, r), v in ec["totals"].items()},
           "clustering": {key(q, r): v for (q, r), v in ec["clustering"].items()},
           "chosen_by_rule": chosen, "for_comparison_only": compare,
           "attempts": stage1.attempts()["outcome_analyses"]}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, allow_nan=True) + "\n")
    print(json.dumps({"totals": out["totals"], "chosen_by_rule": chosen, "for_comparison_only": compare}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
