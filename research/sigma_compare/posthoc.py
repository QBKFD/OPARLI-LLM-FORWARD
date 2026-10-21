#!/usr/bin/env python3
"""
Post-hoc checks P1 and P2 (POSTHOC.md, committed fb8ba0d before running).
Reuses the study's code (run_sigma, fit_final). Run once.

    .venv/bin/python research/sigma_compare/posthoc.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fit_final as ff  # noqa: E402
import run_sigma as rs  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402

OUT = HERE / "results"
TXT_START = pd.Timestamp("2007-01-01", tz="UTC")
PARQ_START = pd.Timestamp("2021-01-01", tz="UTC")
CONF_END = pd.Timestamp("2021-01-01", tz="UTC")
END = rs.STUDY_SEAL
DEV_EDGES = rs.PERIODS["DEV"]["edges"]
BAND = (0.9, 1.1)
TIE = 0.005


def refeature(ctx: rs.Ctx, params: vb.VolParams):
    """X = [1, L, S_hd, T], ok_x and seasonal mass P on ctx, for given VolParams
    (as fit_fold computes them)."""
    feats = vb.transform(params, ctx.m)
    assert feats.index.equals(ctx.labels)
    X = np.column_stack([np.ones(len(ctx.labels)), feats["L"], feats["S_hd"], feats["T"]])
    prof = np.asarray(params.profile, dtype=float)
    CW = np.concatenate([[0.0], np.cumsum(np.where(ctx.grid_open, prof[ctx.grid_bucket], 0.0))])
    P = {H: CW[ctx.win[H].j] - CW[ctx.win[H].g0] for H in rs.HORIZONS}
    return X, np.isfinite(X).all(axis=1), P


def rows_from(ctx: rs.Ctx, X, ok_x, P, model, start, end) -> dict:
    """Valid, finite, non-crossing rows in [start, end) with plug-in (h_a) and
    HAR (h_b) forecasts, the target and the move over the window."""
    close = ctx.m["close"].to_numpy()
    sel = np.asarray((ctx.labels >= start) & (ctx.labels < end))
    out = {}
    for H in rs.HORIZONS:
        w, mod = ctx.win[H], model[H]
        with np.errstate(invalid="ignore", over="ignore"):
            h_a = np.exp(X[:, 1]) * P[H]
            h_b = P[H] * np.exp(X @ mod["beta"]) * mod["c"]
        c_end = np.where(w.i1 > 0, close[np.maximum(w.i1 - 1, 0)], np.nan)
        keep = (sel & w.valid & ok_x & ~w.crossing & np.isfinite(h_a) & np.isfinite(h_b)
                & (h_a > 0) & (h_b > 0) & np.isfinite(w.close_ref))
        out[H] = pd.DataFrame({"rv": w.rv[keep], "h_a": h_a[keep], "h_b": h_b[keep],
                               "R": np.log(c_end[keep] / w.close_ref[keep]),
                               "crossing": False}, index=ctx.labels[keep])
    return out


def calib(g: pd.DataFrame, col: str) -> dict:
    h = g[col].to_numpy()
    return {"mean_rv_h": float(np.mean(g.rv.to_numpy() / h)),
            "rms_z": float(math.sqrt(np.mean(g.R.to_numpy() ** 2 / h))),
            "qlike": float(np.mean(rs.qlike(g.rv, h)))}


def p1(ctx_dev) -> dict:
    m = rs.load("CONF")
    ctx = rs.build_context(m, CONF_END)
    fit = rs.fit_fold(ctx, {"train_start": TXT_START}, CONF_END + rs.EMBARGO)
    rs.assert_sealed(pd.DatetimeIndex([fit.params.train_end]), CONF_END + pd.Timedelta(minutes=1), "P1 fit")
    X, ok_x, P = refeature(ctx_dev, fit.params)
    rows = rows_from(ctx_dev, X, ok_x, P, fit.model, PARQ_START, END)
    tests, res = {}, {"fit": {str(H): {"beta": list(map(float, fit.model[H]["beta"])),
                                       "c": fit.model[H]["c"], "n_train": fit.model[H]["n_train"]}
                              for H in rs.HORIZONS}}
    for H in rs.HORIZONS:
        g = rows[H]
        tests[H] = rs.dm(g, "b", "a")
        res[f"H{H}"] = {"n": len(g), "qlike_plugin": float(np.mean(rs.qlike(g.rv, g.h_a))),
                        "qlike_har": float(np.mean(rs.qlike(g.rv, g.h_b))), "dm": tests[H],
                        "har": calib(g, "h_b"), "plugin": calib(g, "h_a")}
    adj = rs.bh({H: t["p"] for H, t in tests.items()})
    for H in rs.HORIZONS:
        res[f"H{H}"]["p_adj"] = adj[H]["p_adj"]
        res[f"H{H}"]["significant"] = bool(adj[H]["reject"] and tests[H]["mean_d"] < 0)
    res["pass"] = all(res[f"H{H}"]["significant"] for H in rs.HORIZONS)
    return res


def p2(ctx_dev) -> dict:
    ctx_txt = rs.build_context(ff.load_all(), END)
    fams = {"LONG": (ctx_txt, TXT_START), "SHORT": (ctx_dev, PARQ_START)}
    rows = {f: {H: [] for H in rs.HORIZONS} for f in fams}
    for k in range(len(DEV_EDGES) - 1):
        s_k, e_k = DEV_EDGES[k], DEV_EDGES[k + 1]
        for f, (ctx, start) in fams.items():
            fit = rs.fit_fold(ctx, {"train_start": start}, s_k)
            r = rows_from(ctx, fit.X, fit.ok_x, fit.P, fit.model, s_k, e_k)
            for H in rs.HORIZONS:
                rows[f][H].append(r[H])
        rs.log(f"  P2 quarter {k + 1} done")
    res = {}
    for H in rs.HORIZONS:
        a = pd.concat(rows["LONG"][H])
        b = pd.concat(rows["SHORT"][H])
        common = a.index.intersection(b.index)
        res[f"H{H}"] = {"n_long": len(a), "n_short": len(b), "n_common": len(common),
                        **{f: {"har": calib(x.loc[common], "h_b"), "plugin": calib(x.loc[common], "h_a")}
                           for f, x in (("LONG", a), ("SHORT", b))}}
    score = {f: float(np.mean([abs(math.log(res[f"H{H}"][f]["har"][m]))
                               for H in rs.HORIZONS for m in ("mean_rv_h", "rms_z")])) for f in fams}
    fav = "LONG" if score["LONG"] <= score["SHORT"] + TIE else "SHORT"
    nums = [res[f"H{H}"][fav]["har"][m] for H in rs.HORIZONS for m in ("mean_rv_h", "rms_z")]
    res.update({"score": score, "favoured": fav, "favoured_numbers": nums,
                "pass": all(BAND[0] <= x <= BAND[1] for x in nums)})
    return res


def main() -> int:
    OUT.mkdir(exist_ok=True)
    ctx_dev = rs.build_context(rs.load("DEV"), END)
    rs.log("P1 ...")
    r1 = p1(ctx_dev)
    rs.log(f"P1 pass={r1['pass']}: " + "; ".join(
        f"H={H} QLIKE plug-in {r1[f'H{H}']['qlike_plugin']:.4f} HAR {r1[f'H{H}']['qlike_har']:.4f} "
        f"DM {r1[f'H{H}']['dm']['stat']:.2f} p_adj {r1[f'H{H}']['p_adj']:.3g}" for H in rs.HORIZONS))
    rs.log("P2 ...")
    r2 = p2(ctx_dev)
    rs.log(f"P2 pass={r2['pass']} favoured={r2['favoured']} score={r2['score']} "
           f"numbers={[round(x, 4) for x in r2['favoured_numbers']]}")
    for H in rs.HORIZONS:
        for f in ("LONG", "SHORT"):
            rs.log(f"  H={H} {f}: HAR {r2[f'H{H}'][f]['har']}  plug-in {r2[f'H{H}'][f]['plugin']}")
    (OUT / "posthoc.json").write_text(json.dumps({"P1": r1, "P2": r2}, indent=2, default=float))
    rs.log(f"GATE: {'PASS' if r1['pass'] and r2['pass'] else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
