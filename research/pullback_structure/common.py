"""Shared steps of the run scripts: prepare the dev data, the per-question
tables, and the statistics of PREREG §4 (aggregates only)."""

from __future__ import annotations

import numpy as np
import pandas as pd

import data
import params as P
import pullback as pb

RUN = dict(k=P.K, x_sigma=P.X_SIGMA, w=P.W, horizon=P.HORIZON, w_alt=P.W_ALT, edges=P.DEPTH_EDGES)
BUCKETS = [f"[{a}, {b})" for a, b in zip(P.DEPTH_EDGES[:-1], P.DEPTH_EDGES[1:])] + [f">= {P.DEPTH_EDGES[-1]}"]


def prepare(verify_cache: bool) -> dict:
    m, checks = data.load(P.WARMUP_START, P.DEV_END_EXCL, verify_cache)
    cd, info = pb.candles(m, P.CANDLE_EDGES_NY, P.CANDLE_END_NY, P.MIN_SHARE)
    tbl, model = data.sigma_table(m, P.DEV_END_EXCL)
    ends = pd.DatetimeIndex(cd["end"])
    return {"m": m, "cd": cd, "candle_info": info, "tbl": tbl, "checks": checks,
            "model": {"name": model.name, "sha256": model.sha256},
            "sig_end": data.at_labels(tbl, f"sigma_px_{P.SIGMA_H}", ends),
            "ret_end": data.at_labels(tbl, f"sigma_ret_{P.SIGMA_H}", ends),
            "mt": m.index.as_unit("ns").asi8, "mh": m["high"].to_numpy(), "ml": m["low"].to_numpy()}


def run(cd, mt, mh, ml, sig):
    return pb.run_both(cd, mt, mh, ml, sig, events_from_ns=P.EVENTS_FROM.value, **RUN)


def tables(rec: pd.DataFrame) -> dict:
    """The rows of each question (P24: truncated events are out of all of them)."""
    r = rec[rec["kind"] != "truncated"]
    turned = r[np.isfinite(r["p"].astype(float))]
    q1 = turned[turned["a"].notna()].copy()
    q1["x"] = q1["a"].astype(float) - q1["p"].astype(float)
    q2 = r[r["bc"].isin(["B", "C"])].copy()
    q2["is_b"] = q2["bc"] == "B"
    c = r[(r["bc"] == "C") & (r["q3_excluded"] == "")]
    q3 = c[c["a3"].notna()].copy()
    q3["x"] = q3["a3"].astype(float) - q3["p3"].astype(float)
    return {"q1": q1, "q1_unresolved": turned[turned["a"].isna()], "q2": q2, "q3": q3,
            "q3_unresolved": c[c["a3"].isna()], "truncated": rec[rec["kind"] == "truncated"]}


def counts(rec: pd.DataFrame) -> dict:
    """Event counts for every question, by direction and pooled; no outcome."""
    t = tables(rec)
    out = {}
    for d in ("bear", "bull", "pooled"):
        sel = (lambda x: x) if d == "pooled" else (lambda x, d=d: x[x["dir"] == d])
        turned = pd.concat([t["q1"], t["q1_unresolved"]])
        out[d] = {"events_started": int(len(sel(rec))), "truncated": int(len(sel(t["truncated"]))),
                  "q1_turns_per_bucket": {BUCKETS[b]: int((sel(turned)["bucket"] == b).sum()) for b in range(len(BUCKETS))},
                  "q2_breaks": int(len(sel(t["q2"])))}
    return out


def analyse(rec: pd.DataFrame, null_share: float, seed: int) -> dict:
    """Q1 per bucket, Q2 and Q3 with their bootstrap tests (P27, P28), on the pooled
    events (P19), plus bull and bear apart (descriptive, no test)."""
    t = tables(rec)
    q1, q2, q3 = t["q1"], t["q2"], t["q3"]
    res: dict = {"q1": {}, "counts": {}}
    for b, name in enumerate(BUCKETS):
        g = q1[q1["bucket"] == b]
        unres = int((t["q1_unresolved"]["bucket"] == b).sum())
        if len(g) < P.MIN_EVENTS:
            res["q1"][name] = {"n": int(len(g)), "unresolved": unres, "too_few": True,
                               "excess": float(g["x"].mean()) if len(g) else None}
            continue
        res["q1"][name] = {**pb.excess_test(g["q1_month"].to_numpy(), g["x"].to_numpy(), P.BOOT_B, seed + b),
                           "unresolved": unres, "too_few": False,
                           "rate_A": float(g["a"].astype(float).mean()), "mean_P": float(g["p"].astype(float).mean())}
    res["q2"] = (pb.share_test(q2["q2_month"].to_numpy(), q2["is_b"].to_numpy(), null_share, P.BOOT_B, seed + 10)
                 if len(q2) >= P.MIN_EVENTS and np.isfinite(null_share) else {"n": int(len(q2)), "too_few": len(q2) < P.MIN_EVENTS})
    res["q3"] = ({**pb.excess_test(q3["q3_month"].to_numpy(), q3["x"].to_numpy(), P.BOOT_B, seed + 20),
                  "unresolved": int(len(t["q3_unresolved"])), "rate_A": float(q3["a3"].astype(float).mean()),
                  "mean_P": float(q3["p3"].astype(float).mean())}
                 if len(q3) >= P.MIN_EVENTS else {"n": int(len(q3)), "too_few": True})
    res["by_direction"] = {}
    for d in ("bear", "bull"):
        a, b2, c = q1[q1["dir"] == d], q2[q2["dir"] == d], q3[q3["dir"] == d]
        res["by_direction"][d] = {
            "q1_per_bucket": {BUCKETS[b]: {"n": int((a["bucket"] == b).sum()),
                                           "excess": float(a.loc[a["bucket"] == b, "x"].mean()) if (a["bucket"] == b).any() else None}
                              for b in range(len(BUCKETS))},
            "q2": {"n": int(len(b2)), "share_B": float(b2["is_b"].mean()) if len(b2) else None},
            "q3": {"n": int(len(c)), "excess": float(c["x"].mean()) if len(c) else None}}
    res["counts"] = {"events_started": int(len(rec)), "truncated": int(len(t["truncated"])),
                     "kinds": {k: int(v) for k, v in rec["kind"].value_counts().items()},
                     "q2_from": {k: int(v) for k, v in q2["brk_from"].value_counts().items()},
                     "q3_excluded_ll_during_w": int(((rec["bc"] == "C") & (rec["q3_excluded"] != "")).sum())}
    return res


def family(res: dict) -> dict:
    """P29 on dev: Q1 x the testable buckets, Q2, Q3."""
    p = {f"Q1 {k}": v["p_one_sided"] for k, v in res["q1"].items() if not v.get("too_few")}
    if "p_two_sided" in res["q2"]:
        p["Q2"] = res["q2"]["p_two_sided"]
    if "p_one_sided" in res["q3"]:
        p["Q3"] = res["q3"]["p_one_sided"]
    return {"p": p, "bh": pb.bh(p, P.BH_Q)}
