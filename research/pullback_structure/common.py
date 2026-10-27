"""Shared steps of the run scripts: prepare the dev data, the per-question
tables, and the statistics of PREREG §4 as amended by §11 (aggregates only)."""

from __future__ import annotations

import numpy as np
import pandas as pd

import data
import params as P
import pullback as pb

RUN = dict(k=P.K, x_sigma=P.X_SIGMA, w=P.W, horizon=P.HORIZON, w_alt=P.W_ALT, n_swings=P.N_SWINGS)
TERCILE_NAMES = ("T1 (shallow)", "T2", "T3 (deep)")


def prepare(verify_cache: bool, timeframe: str = P.TIMEFRAME) -> dict:
    m, checks = data.load(P.WARMUP_START, P.DEV_END_EXCL, verify_cache)
    if timeframe == "1h":
        cd, info = pb.candles_hourly(m, P.MIN_SHARE)          # A2 §12
    elif timeframe == "4h":
        cd, info = pb.candles(m, P.CANDLE_EDGES_NY, P.CANDLE_END_NY, P.MIN_SHARE)
    else:
        raise ValueError(f"timeframe {timeframe!r}")
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
    return {"turned": turned, "q1": q1, "q1_unresolved": turned[turned["a"].isna()], "q2": q2, "q3": q3,
            "c_total": c, "q3_unresolved": c[c["a3"].isna()], "truncated": rec[rec["kind"] == "truncated"]}


def tercile_cuts(rec: pd.DataFrame) -> tuple[float, float]:
    """A1 §11.1: the 1/3 and 2/3 quantiles of depth over the dev turns (no outcome)."""
    d = tables(rec)["turned"]["depth"].astype(float).to_numpy()
    q = np.quantile(d, P.TERCILES)
    return float(q[0]), float(q[1])


def counts(rec: pd.DataFrame) -> dict:
    """Event counts for every question, by direction and pooled; no outcome."""
    t = tables(rec)
    out = {}
    for d in ("bear", "bull", "pooled"):
        sel = (lambda x: x) if d == "pooled" else (lambda x, d=d: x[x["dir"] == d])
        out[d] = {"events_started": int(len(sel(rec))), "truncated": int(len(sel(t["truncated"]))),
                  "q1_turns": int(len(sel(t["turned"]))), "q2_breaks": int(len(sel(t["q2"])))}
    return out


def analyse(rec: pd.DataFrame, null_share: float, seed: int, cuts: tuple[float, float]) -> dict:
    """Q1 pooled, Q2 and Q3 with their bootstrap tests (P27, P28, A1 §11.1-§11.2);
    the depth terciles and bull/bear apart, descriptive only."""
    t = tables(rec)
    q1, q2, q3 = t["q1"], t["q2"], t["q3"]
    cnt1 = {"turns": int(len(t["turned"])), "resolved": int(len(q1)), "unresolved": int(len(t["q1_unresolved"]))}
    res: dict = {}
    if len(q1) >= P.MIN_EVENTS:
        res["q1"] = {**pb.excess_test(q1["q1_month"].to_numpy(), q1["x"].to_numpy(), P.BOOT_B, seed),
                     **cnt1, "rate_A": float(q1["a"].astype(float).mean()), "mean_P": float(q1["p"].astype(float).mean())}
    else:
        res["q1"] = {**cnt1, "too_few": True}
    cnt2 = {"breaks": int(len(q2)), "B": int(q2["is_b"].sum()), "C": int((~q2["is_b"]).sum())}
    res["q2"] = ({**pb.share_test(q2["q2_month"].to_numpy(), q2["is_b"].to_numpy(), null_share, P.BOOT_B, seed + 10), **cnt2}
                 if len(q2) >= P.MIN_EVENTS and np.isfinite(null_share) else {**cnt2, "too_few": len(q2) < P.MIN_EVENTS})
    cnt3 = {"c_events": int(len(t["c_total"])), "resolved": int(len(q3)), "unresolved": int(len(t["q3_unresolved"])),
            "excluded_ll_during_w": int(((rec["bc"] == "C") & (rec["q3_excluded"] != "")).sum())}
    res["q3"] = ({**pb.excess_test(q3["q3_month"].to_numpy(), q3["x"].to_numpy(), P.BOOT_B, seed + 20), **cnt3,
                  "rate_A": float(q3["a3"].astype(float).mean()), "mean_P": float(q3["p3"].astype(float).mean())}
                 if len(q3) >= P.MIN_EVENTS else {**cnt3, "too_few": True})
    terc = pb.tercile_of(q1["depth"].astype(float).to_numpy(), cuts)
    res["q1_terciles_descriptive"] = {"cuts_sigma": list(cuts), **{
        TERCILE_NAMES[j]: {"n": int((terc == j).sum()),
                           "excess": float(q1["x"].to_numpy()[terc == j].mean()) if (terc == j).any() else None}
        for j in range(3)}}
    res["by_direction_descriptive"] = {}
    for d in ("bear", "bull"):
        a, b2, c = q1[q1["dir"] == d], q2[q2["dir"] == d], q3[q3["dir"] == d]
        res["by_direction_descriptive"][d] = {
            "q1": {"n": int(len(a)), "excess": float(a["x"].mean()) if len(a) else None},
            "q2": {"n": int(len(b2)), "share_B": float(b2["is_b"].mean()) if len(b2) else None},
            "q3": {"n": int(len(c)), "excess": float(c["x"].mean()) if len(c) else None}}
    res["counts"] = {"events_started": int(len(rec)), "truncated": int(len(t["truncated"])),
                     "kinds": {k: int(v) for k, v in rec["kind"].value_counts().items()},
                     "q2_from": {k: int(v) for k, v in q2["brk_from"].value_counts().items()}}
    return res


def family(res: dict) -> dict:
    """A1 §11.2: BH over Q1 (pooled), Q2, Q3 (those with enough events)."""
    p = {}
    if "p_one_sided" in res["q1"]:
        p["Q1"] = res["q1"]["p_one_sided"]
    if "p_two_sided" in res["q2"]:
        p["Q2"] = res["q2"]["p_two_sided"]
    if "p_one_sided" in res["q3"]:
        p["Q3"] = res["q3"]["p_one_sided"]
    return {"p": p, "bh": pb.bh(p, P.BH_Q)}


def verdict(q1: dict, bh: dict) -> str:
    """A1 §11.4, on Q1."""
    if q1.get("too_few"):
        return "INCONCLUSIVE (too few events)"
    lo, hi = q1["ci95"]
    if q1["excess"] >= P.PASS_EXCESS and lo > 0 and bh["Q1"]["p_adj"] < P.BH_Q:
        return "PASS"
    if hi < P.PASS_EXCESS:
        return "FAIL"
    return "INCONCLUSIVE"
