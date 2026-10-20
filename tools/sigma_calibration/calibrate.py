#!/usr/bin/env python3
"""Calibration check of vol_block's sigma over H minutes (pre-2024 history).

Not a research study and not a decision rule: sigma is an engineering decision
(datafeed/features/vol_block.py). It checks the plug-in, called sigma_over when
this was run and renamed sigma_plugin when HAR took over sigma_over. This only
shows how predicted sigma compares with what price did, before sigma is trusted
for bracket distances. The method below was fixed before the first run; it is
run once.

Data    research parquet XAUUSD_1min_UTC_clean.parquet, rows < 2024-01-01 only
        (asserted). Cleaning as research load_minutes: duplicate timestamps
        dropped (first kept), rows with a price <= 0 or high < low dropped.
Fit     vb.fit on the bars in [2021-01-01, 2022-01-01).
Points  every hourly label t in [2022-01-02, 2024-01-01) whose state is valid
        (L, S_hd, T finite, as sigma_plugin requires), for H in 15, 60, 240, 1440
        minutes, with t + H <= 2024-01-01. Features come from one transform()
        of the whole series (equal to state_at at labels, tested); sigma is
        computed vectorised and cross-checked against vb.sigma_plugin(vb.state_at)
        on 200 random (t, H) pairs (rel. 1e-9, else abort).
Dropped windows with no market-open minute (sigma 0), or with bars on fewer than
        90 % of the open minutes (missing data).
Realised
        R      = ln(C_end / close_ref): close_ref = close of the last bar opening
                 before t, C_end = close of the last bar opening before t + H.
        z      = R / sigma_ret.
        ratio  = sqrt(mean z²): realised RMS move over predicted sigma. 1 = calibrated.
        cover1, cover2 = share of |z| <= 1, <= 2 (normal: 0.683, 0.954).
        rv_ratio = Σ RV / Σ RVhat over windows: RV = sum of the 1-min r² in the
                 window (vol_block's returns, gap returns dropped), RVhat =
                 exp(L) × profile summed over the minutes that have a return.
                 It checks the variance level itself; ratio² below rv_ratio
                 means 1-min returns partly cancel over the horizon.
Groups  by H × crosses_close (the window holds closed minutes, so R can contain
        a gap that sigma leaves out), pooled and per year (2022, 2023).
Windows overlap (hourly t, H up to 24h), so no confidence intervals: descriptive.

Reading guide, fixed before the run (not a pass/fail rule): ratio in
0.9-1.1 "close to 1"; 0.75-0.9 or 1.1-1.33 "noticeably off"; outside "far from 1".

    .venv/bin/python tools/sigma_calibration/calibrate.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from datafeed.features import vol_block as vb
from datafeed.ingest.xauusd_bars import _market_open

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = ROOT / "database" / "ohlcv_data" / "XAUUSD_1min_UTC_clean.parquet"
OUT = HERE / "results"

END = pd.Timestamp("2024-01-01", tz="UTC")          # nothing at or after this is read
LOAD_START = pd.Timestamp("2020-12-01", tz="UTC")    # warm-up for the 120-row window
FIT = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2022-01-01", tz="UTC"))
EVAL_START = pd.Timestamp("2022-01-02", tz="UTC")   # 24h after the training end
HORIZONS = (15, 60, 240, 1440)
COVERAGE = 0.90
N_CROSSCHECK = 200


def load() -> pd.DataFrame:
    df = pd.read_parquet(DATA, filters=[("timestamp", ">=", LOAD_START), ("timestamp", "<", END)])
    df = df.set_index("timestamp").sort_index()
    px = df[["open", "high", "low", "close"]]
    keep = ~df.index.duplicated(keep="first") & (px > 0).all(axis=1) & (df["high"] >= df["low"])
    df = df[keep]
    assert (df.index < END).all(), "row at or after 2024-01-01"
    return df[["open", "high", "low", "close"]]


def main() -> int:
    m = load()
    print(f"bars {len(m):,}  {m.index[0]} .. {m.index[-1]}")
    params = vb.fit(m[(m.index >= FIT[0]) & (m.index < FIT[1])])
    feats = vb.transform(params, m)
    ok = feats[["L", "S_hd", "T"]].notna().all(axis=1) & (feats.index >= EVAL_START)
    pos = pd.Series(np.arange(len(feats)), index=feats.index)
    ok &= pos >= vb.WINDOWS[-1]                        # as state_at: row 0 never in a window
    labels = feats.index[ok]
    L = feats.loc[labels, "L"].to_numpy()

    # per-minute grid over the evaluation span: open flag and profile weight
    grid = pd.date_range(EVAL_START, END + pd.Timedelta(minutes=max(HORIZONS)), freq="1min")
    is_open = np.fromiter((_market_open(u) for u in grid.to_pydatetime()), bool, len(grid))
    prof = np.asarray(params.profile)
    w = np.where(is_open, prof[vb._buckets(grid)], 0.0)
    cw = np.concatenate([[0.0], np.cumsum(w)])
    co = np.concatenate([[0], np.cumsum(is_open)])

    # per-bar quantities: returns as vol_block defines them
    r = vb._returns(m).to_numpy()
    has_r = ~np.isnan(r)
    crv = np.concatenate([[0.0], np.cumsum(np.where(has_r, r ** 2, 0.0))])
    cpw = np.concatenate([[0.0], np.cumsum(np.where(has_r, prof[vb._buckets(m.index)], 0.0))])
    t_ns, close = m.index.asi8, m["close"].to_numpy()
    out_bars = int((~np.fromiter((_market_open(u) for u in m.index.to_pydatetime()), bool,
                                 len(m))).sum())
    print(f"bars outside _market_open: {out_bars:,}")

    rows, frames = [], []
    for H in HORIZONS:
        end = labels + pd.Timedelta(minutes=H)
        sel = end <= END
        t, e, Lh = labels[sel], end[sel], L[sel]
        g0 = grid.get_indexer(t)
        assert (g0 >= 0).all()
        g1 = g0 + H
        var = np.exp(Lh) * (cw[g1] - cw[g0])
        n_open = co[g1] - co[g0]
        i0 = np.searchsorted(t_ns, t.asi8, "left")
        i1 = np.searchsorted(t_ns, e.asi8, "left")
        n_bars = i1 - i0
        valid = (n_open > 0) & (n_bars >= COVERAGE * n_open) & (i0 > 0)
        sig = np.sqrt(var)
        R = np.log(close[i1 - 1] / close[i0 - 1])
        rv = crv[i1] - crv[i0]
        rvhat = np.exp(Lh) * (cpw[i1] - cpw[i0])
        f = pd.DataFrame({"t": t, "H": H, "sigma_ret": sig, "R": R, "rv": rv, "rvhat": rvhat,
                          "open_minutes": n_open, "crosses_close": n_open < H,
                          "valid": valid, "close_ref": close[np.maximum(i0 - 1, 0)]})
        frames.append(f)
        print(f"H={H}: labels {len(t):,}, dropped {(~valid).sum():,} "
              f"(no open minute {(n_open == 0).sum():,})")

    allf = pd.concat(frames, ignore_index=True)
    crosscheck(params, m, allf[allf.valid])

    v = allf[allf.valid].copy()
    v["z"] = v.R / v.sigma_ret
    v["year"] = v.t.dt.year
    for (H, cc), g in v.groupby(["H", "crosses_close"]):
        rows.append(summary(g, H, cc, "all"))
        for y, gy in g.groupby("year"):
            rows.append(summary(gy, H, cc, str(y)))
    res = pd.DataFrame(rows)
    OUT.mkdir(exist_ok=True)
    res.to_csv(OUT / "calibration.csv", index=False, float_format="%.4f")
    with pd.option_context("display.width", 200, "display.max_rows", 100):
        print(res.to_string(index=False, float_format=lambda x: f"{x:.3f}"))
    return 0


def summary(g: pd.DataFrame, H, cc, period) -> dict:
    z = g.z.to_numpy()
    return {"H": H, "crosses_close": bool(cc), "period": period, "n": len(g),
            "ratio": math.sqrt(float(np.mean(z ** 2))), "std_z": float(np.std(z)),
            "cover1": float(np.mean(np.abs(z) <= 1)), "cover2": float(np.mean(np.abs(z) <= 2)),
            "rv_ratio": float(g.rv.sum() / g.rvhat.sum()),
            "median_sigma_px": float(np.median(g.sigma_ret * g.close_ref))}


def crosscheck(params, m, v: pd.DataFrame) -> None:
    rng = np.random.default_rng(20261014)
    for k in rng.choice(len(v), N_CROSSCHECK, replace=False):
        row = v.iloc[k]
        t = row.t
        st = vb.state_at(params, m[(m.index >= t - vb.LOOKBACK) & (m.index < t)], t)
        s = vb.sigma_plugin(params, st, int(row.H))
        if not (math.isclose(s.sigma_ret, row.sigma_ret, rel_tol=1e-9)
                and s.open_minutes == row.open_minutes and st.close_ref == row.close_ref):
            raise SystemExit(f"cross-check failed at {t} H={row.H}: {s} vs {row.to_dict()}")
    print(f"cross-check vs sigma_plugin(state_at): {N_CROSSCHECK} pairs agree")


if __name__ == "__main__":
    sys.exit(main())
