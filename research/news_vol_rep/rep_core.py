"""research/news_vol_rep: what the replication adds to Study A's machinery (no data access).

Study A's pipeline (research/news_vol/nv_core.run and analyse) is used unchanged for
each market. Added here:
- fx_open_flags and crossing(): EURUSD's own calendar, to check R3 (its 17:00 NY
  pause) against the metals calendar that sigma_compare's build_context uses;
- combine(): BH over the tests of both markets (R4) and the verdict per market;
- synth_bars_on() and machinery_check(): Study A's synthetic check, on the market's
  real bar open times (timestamps only) instead of the full open-minute grid.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
_NV = HERE.parent / "news_vol"
for _p in (_NV, HERE):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import nv  # noqa: E402
import nv_core  # noqa: E402  (puts research/sigma_compare on sys.path)
import nv_synth  # noqa: E402
import rep_params as V  # noqa: E402
import run_sigma as rs  # noqa: E402

from backend.datafeed.features import vol_block as vb  # noqa: E402


# ----------------------------------------------------------------------------- calendars [R3]

def fx_open_flags(index: pd.DatetimeIndex) -> np.ndarray:
    """EURUSD's open minutes: Sun 17:15 to Fri 17:00 New York, without the daily
    pause [17:00, 17:15) (its bar times, DECISIONS R-N1)."""
    et = index.tz_convert("America/New_York")
    wd, mod = np.asarray(et.dayofweek), np.asarray(et.hour * 60 + et.minute)
    a, b = V.FX_PAUSE_NY
    closed = (wd == 5) | ((wd == 6) & (mod < b)) | ((wd == 4) & (mod >= a)) | ((mod >= a) & (mod < b))
    return ~closed


def crossing(labels: pd.DatetimeIndex, end: pd.Timestamp, H: int, open_fn) -> np.ndarray:
    """sigma_compare's crossing rule (a window of the first H open minutes from t spans
    a closed minute) under the calendar open_fn, for every hourly label."""
    grid = pd.date_range(labels[0], end + rs.GRID_SLACK, freq="1min", inclusive="left")
    CO = np.concatenate([[0], np.cumsum(open_fn(grid))])
    g0 = (labels.asi8 - grid[0].value) // nv.MIN_NS
    j = rs.windows(CO, g0, H)
    return (j - g0) > H


def calendar_agreement(idx: pd.DatetimeIndex, end: pd.Timestamp, horizons) -> dict:
    """[R3] For each H: hourly labels whose crossing flag differs between the metals
    calendar (build_context's) and EURUSD's own. Zero means the same windows are
    excluded, and every non-crossing window covers the same H calendar minutes."""
    labels = vb._bar_labels(idx)
    out = {}
    for H in horizons:
        a, b = crossing(labels, end, H, rs.open_flags), crossing(labels, end, H, fx_open_flags)
        out[str(H)] = {"labels": int(len(labels)), "crossing_metals": int(a.sum()), "crossing_fx": int(b.sum()),
                       "disagree": int((a != b).sum())}
    return out


# ----------------------------------------------------------------------------- R4

def combine(analyses: dict, q: float = V.BH_Q) -> dict:
    """analyses: market -> nv_core.analyse() output, its "BH" renamed "BH_within_market_not_used"
    (only its raw p-values are taken). BH over the four tests of each market (Q1, Q2 at both H, Q3: 8 p-values), then
    Study A's D7 verdict per market with those BH p-values. The replication holds if
    every market passes."""
    names, p = [], []
    for mk, a in analyses.items():
        for n, d in a["BH_within_market_not_used"].items():
            names.append(f"{mk}:{n}")
            p.append(d["p"])
    adj = nv.bh(p)
    fam = {n: {"p": float(a), "p_bh": float(b)} for n, a, b in zip(names, p, adj)}
    per = {}
    for mk, a in analyses.items():
        h = str(V.PRIMARY_H)
        v, why = nv.verdict(a["Q2"][h]["mean_d"], fam[f"{mk}:Q2_H{h}"]["p_bh"],
                            a["Q2"]["60"]["mean_d"], fam[f"{mk}:Q2_H60"]["p_bh"], q)
        per[mk] = {"verdict": v, "verdict_why": why}
    holds = all(r["verdict"] == "PASS" for r in per.values())
    return {"BH_family": fam, "m": len(p), "per_market": per,
            "replication": "HOLDS" if holds else "DOES NOT HOLD"}


# ----------------------------------------------------------------------------- machinery check

def synth_bars_on(index: pd.DatetimeIndex, events: pd.DataFrame, effect: bool, seed: int = V.SYN_SEED,
                  mult: float = V.SYN_EFFECT_MULT, minutes: int = V.SYN_EFFECT_MIN):
    """Study A's generator (nv_synth.synth_bars) on the given bar open times: per-minute
    variance SIGMA_MIN^2 x profile(UTC hour) x exp(x_hour) x M(u), M = mult for `minutes`
    minutes from each grouped event in the effect world. No real price is used."""
    grid = pd.DatetimeIndex(index).as_unit("ns")
    rng = np.random.default_rng(seed)
    hours = pd.date_range(grid[0].floor("1h"), grid[-1] + pd.Timedelta(hours=1), freq="1h")
    eta = rng.standard_normal(len(hours))
    x = np.empty(len(hours))
    x[0] = 0.0
    for i in range(1, len(hours)):
        x[i] = V.SYN_AR * x[i - 1] + V.SYN_AR_SD * eta[i]
    hi = np.searchsorted(hours.asi8, grid.floor("1h").asi8)
    h_utc = np.asarray(grid.hour)
    var0 = V.SYN_SIGMA_MIN ** 2 * (1.0 + 1.5 * np.exp(-((h_utc - 14) / 2.5) ** 2)) * np.exp(x[hi])
    M = np.ones(len(grid))
    if effect and len(events):
        g = grid.asi8
        for e in pd.DatetimeIndex(events.index).asi8:
            a, b = np.searchsorted(g, e, "left"), np.searchsorted(g, e + minutes * nv.MIN_NS, "left")
            M[a:b] = mult
    var = var0 * M
    r = np.sqrt(var) * rng.standard_normal(len(grid))
    close = V.SYN_PRICE0 * np.exp(np.cumsum(r))
    opn = np.concatenate([[V.SYN_PRICE0], close[:-1]])
    m = pd.DataFrame({"open": opn, "high": np.maximum(opn, close), "low": np.minimum(opn, close), "close": close},
                     index=grid)
    m.index.name = "timestamp"
    tr = nv_synth.Truth(grid.asi8, np.concatenate([[0.0], np.cumsum(var)]), np.concatenate([[0.0], np.cumsum(var0)]))
    return m, tr


def machinery_check(index: pd.DatetimeIndex, events: pd.DataFrame, cfg: nv_core.Config, B: int = V.BOOT_B,
                    seed: int = V.SYN_SEED, mult: float = V.SYN_EFFECT_MULT, minutes: int = V.SYN_EFFECT_MIN) -> dict:
    """Study A's PREREG section 7 rule, on one market's bar times. Effect world: Q1 > 0
    with p < 0.05 and Q1 / truth in SYN_RECOVER; the augmented arm lower with DM p < 0.05
    at both H. Null world: DM p > 0.05 at both H."""
    out: dict = {"seed": seed, "mult": mult, "minutes": minutes, "B": B,
                 "bar_times": int(len(index))}
    for world in ("effect", "null"):
        m, truth = synth_bars_on(index, events, world == "effect", seed, mult, minutes)
        res = nv_core.run(m, events, cfg)
        a = nv_core.analyse(res, cfg.horizons[0], B, seed, V.NW_LAG, V.BH_Q)
        f = res.rows[cfg.horizons[0]]
        tl = truth.log_ratio(f.index.asi8, cfg.horizons[0])
        rel = f["rel"].to_numpy()
        ok = np.isfinite(tl)
        true_diff = float(tl[rel & ok].mean() - tl[~rel & ok].mean())
        aug = [c for c in res.coefs if c["arm"] == "aug"]
        out[world] = {"Q1": a["Q1"], "Q2": a["Q2"], "Q3": a["Q3"], "true_Q1_diff": true_diff,
                      "aug_coef_data": [c["d_data"] for c in aug], "aug_coef_fomc": [c["d_fomc"] for c in aug],
                      "rows": {str(H): int(len(g)) for H, g in res.rows.items()},
                      "release_rows": {str(H): int(g["rel"].sum()) for H, g in res.rows.items()}}
    e, n = out["effect"], out["null"]
    ratio = e["Q1"]["estimate"] / e["true_Q1_diff"] if e["true_Q1_diff"] > 0 else float("nan")
    lo, hi = V.SYN_RECOVER
    checks = {
        "effect_Q1_positive_p<0.05": e["Q1"]["estimate"] > 0 and e["Q1"]["p_one_sided_gt0"] < 0.05,
        "effect_Q1_recovers_truth": bool(lo <= ratio <= hi),
        **{f"effect_Q2_H{H}_aug_lower_p<0.05": e["Q2"][str(H)]["mean_d"] < 0 and e["Q2"][str(H)]["p"] < 0.05
           for H in cfg.horizons},
        **{f"null_Q2_H{H}_p>0.05": n["Q2"][str(H)]["p"] > 0.05 for H in cfg.horizons},
    }
    out["Q1_ratio_to_truth"] = ratio
    out["checks"] = {k: bool(v) for k, v in checks.items()}
    out["pass"] = all(checks.values())
    return out


def config(seal: bool) -> nv_core.Config:
    return nv_core.Config(train_start=V.DEV_START, end=V.DEV_END_EXCL, edges=V.FOLD_EDGES, horizons=V.HORIZONS,
                          embargo=V.EMBARGO, seal_end=V.DEV_END_EXCL if seal else None)
