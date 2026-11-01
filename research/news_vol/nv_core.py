"""research/news_vol (Study A): the pipeline, shared by the real run and the synthetic
machinery check. It takes 1-min bars and grouped events; it never loads data.

Windows, targets and the seasonal mass come from research/sigma_compare
(build_context: the first H market-open minutes from each hourly label t,
RV = sum of squared 1-min log returns, >= 90 % coverage). Each fold refits
vol_block (VolParams) and both arms on its own training rows (D5).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (ROOT, ROOT / "research", ROOT / "research" / "vol_features", ROOT / "research" / "vol_features_r3",
           ROOT / "research" / "sigma_compare", HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import nv  # noqa: E402
import run_sigma as rs  # noqa: E402
import sealing  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402

ARM_COLS = {"har": ["one", "L", "S_hd", "T"], "aug": ["one", "L", "S_hd", "T", "d_data", "d_fomc"]}


@dataclass
class Config:
    train_start: pd.Timestamp
    end: pd.Timestamp                      # exclusive; every window ends at or before it
    edges: pd.DatetimeIndex                # test folds [edges[k], edges[k+1])
    horizons: tuple = (240, 60)
    embargo: pd.Timedelta = pd.Timedelta(hours=24)
    seal_end: pd.Timestamp | None = None   # if set, every row must lie before it (real data)


@dataclass
class Result:
    rows: dict = field(default_factory=dict)      # H -> DataFrame of test rows (D4 sample)
    coefs: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)


def _flags(labels: pd.DatetimeIndex, events: pd.DataFrame, H: int) -> tuple[np.ndarray, np.ndarray]:
    ev_ns = pd.DatetimeIndex(events.index).asi8
    is_f = (events["type"] == "FOMC").to_numpy()
    return (nv.in_window(labels.asi8, ev_ns[~is_f], H).astype(float),
            nv.in_window(labels.asi8, ev_ns[is_f], H).astype(float))


def run(m: pd.DataFrame, events: pd.DataFrame, cfg: Config) -> Result:
    if set(cfg.horizons) - set(rs.HORIZONS):
        raise ValueError(f"horizons must be among {rs.HORIZONS}")
    ctx = rs.build_context(m, cfg.end)
    labels = ctx.labels
    close = ctx.m["close"].to_numpy()
    res = Result()
    flags = {H: _flags(labels, events, H) for H in cfg.horizons}
    parts = {H: [] for H in cfg.horizons}
    for k in range(len(cfg.edges) - 1):
        s_k, e_k = cfg.edges[k], cfg.edges[k + 1]
        tr_end = s_k - cfg.embargo
        params = vb.fit(m[(m.index >= cfg.train_start) & (m.index < tr_end)])
        assert pd.Timestamp(params.train_end) <= tr_end
        feats = vb.transform(params, m)
        assert feats.index.equals(labels)
        base = np.column_stack([np.ones(len(labels)), feats["L"], feats["S_hd"], feats["T"]])
        ok_x = np.isfinite(base).all(axis=1)
        prof = np.asarray(params.profile, dtype=float)
        CW = np.concatenate([[0.0], np.cumsum(np.where(ctx.grid_open, prof[ctx.grid_bucket], 0.0))])
        for H in cfg.horizons:
            w = ctx.win[H]
            P = CW[w.j] - CW[w.g0]
            d_data, d_fomc = flags[H]
            X = {"har": base, "aug": np.column_stack([base, d_data, d_fomc])}
            usable = w.valid & ok_x & ~w.crossing & (P > 0)
            train = usable & (labels >= cfg.train_start) & (labels <= tr_end) & (w.E_ns <= s_k.value)
            assert (w.E_ns[train] <= s_k.value).all() and (labels[train] <= tr_end).all()
            y = np.log(w.rv[train] / P[train])
            h = {}
            for arm, Xa in X.items():
                beta, c = nv.fit_smeared(Xa[train], y)
                with np.errstate(over="ignore", invalid="ignore"):
                    h[arm] = P * np.exp(Xa @ beta) * c
                res.coefs.append({"fold": k + 1, "test_start": str(s_k.date()), "H": H, "arm": arm,
                                  **dict(zip(ARM_COLS[arm], map(float, beta))), "smear_c": c,
                                  "n_train": int(train.sum()),
                                  "n_train_data": int(d_data[train].sum()), "n_train_fomc": int(d_fomc[train].sum())})
            test = (usable & (labels >= s_k) & (labels < e_k)
                    & np.isfinite(h["har"]) & np.isfinite(h["aug"]) & (h["har"] > 0) & (h["aug"] > 0))
            i_end = np.maximum(w.i1 - 1, 0)
            f = pd.DataFrame({
                "fold": k + 1, "rv": w.rv[test], "P": P[test], "h_har": h["har"][test], "h_aug": h["aug"][test],
                "d_data": d_data[test].astype(bool), "d_fomc": d_fomc[test].astype(bool),
                "ret": np.log(close[i_end[test]]) - np.log(w.close_ref[test]),
                "E_ns": w.E_ns[test]}, index=labels[test])
            f.index.name = "t"
            parts[H].append(f)
            res.counts.setdefault(str(H), []).append({
                "fold": k + 1, "test_labels": int(((labels >= s_k) & (labels < e_k)).sum()),
                "crossing": int((((labels >= s_k) & (labels < e_k)) & w.crossing).sum()),
                "rows": int(test.sum()), "release_rows": int((test & ((d_data + d_fomc) > 0)).sum())})
    for H in cfg.horizons:
        f = pd.concat(parts[H]).sort_index()
        f["rel"] = f["d_data"] | f["d_fomc"]
        f["y"] = np.log(f["rv"] / f["h_har"])                           # Q1, Q3: the baseline arm
        f["ql_har"] = nv.qlike(f["rv"], f["h_har"])
        f["ql_aug"] = nv.qlike(f["rv"], f["h_aug"])
        assert (f["E_ns"] <= cfg.end.value).all()
        if cfg.seal_end is not None:
            sealing.assert_before_global_seal(pd.DatetimeIndex(f.index), f"H={H} rows")
            if (f.index >= cfg.seal_end).any() or (f["E_ns"] > cfg.seal_end.value).any():
                raise AssertionError(f"H={H}: a row or window reaches {cfg.seal_end}")
        res.rows[H] = f
    return res


def analyse(res: Result, primary_h: int, B: int, seed: int, nw_lag: int, q: float) -> dict:
    """Q1 (D6), Q2 (D7), Q3 (D8), BH (D9) and the verdict, from the pipeline's rows."""
    out: dict = {}
    f = res.rows[primary_h]
    months = nv.month_codes(f.index)
    out["Q1"] = {"H": primary_h, **nv.boot_diff(f["y"].to_numpy(), f["rel"].to_numpy(), months, B, seed)}
    out["Q2"] = {}
    for H, g in res.rows.items():
        out["Q2"][str(H)] = {**nv.dm_two_sided(g["ql_aug"].to_numpy(), g["ql_har"].to_numpy(), nw_lag),
                             "qlike_har": float(g["ql_har"].mean()), "qlike_aug": float(g["ql_aug"].mean())}
    r = f[f["rel"]]
    out["Q3"] = {"H": primary_h, **nv.boot_spearman(r["y"].to_numpy(), r["ret"].to_numpy(),
                                                    nv.month_codes(r.index), B, seed)}
    names = ["Q1", f"Q2_H{primary_h}", "Q2_H60", "Q3"]
    p = [out["Q1"]["p_one_sided_gt0"], out["Q2"][str(primary_h)]["p"], out["Q2"]["60"]["p"], out["Q3"]["p_two_sided"]]
    adj = nv.bh(p)
    out["BH"] = {n: {"p": float(a), "p_bh": float(b)} for n, a, b in zip(names, p, adj)}
    v, why = nv.verdict(out["Q2"][str(primary_h)]["mean_d"], out["BH"][f"Q2_H{primary_h}"]["p_bh"],
                        out["Q2"]["60"]["mean_d"], out["BH"]["Q2_H60"]["p_bh"], q)
    out["verdict"], out["verdict_why"] = v, why
    return out
