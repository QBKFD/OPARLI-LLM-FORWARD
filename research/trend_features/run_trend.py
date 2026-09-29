#!/usr/bin/env python3
"""
Trend feature study: does direction-neutral trend strength predict whether
price moves cleanly or chops over the next 4h / 24h?

Candidate MT = (Z4, Z24, Z120), benchmark M1+ = 5 existing trend features + 3
absolute values, control MV = (L, S_hd, T) from research/vol_block.
Specification: PREREG.md (committed before any model result).

    backend/venv/bin/python3 research/trend_features/run_trend.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in ("vol_features", "vol_features_r2", "vol_features_r3", "vol_block"):
    sys.path.insert(0, str(ROOT / "research" / p))
import run_r3 as r3  # noqa: E402  (loaders, folds, HAC, eta2; round-1 helpers via r3.r1)
from vol_block import VolBlock  # noqa: E402

r1 = r3.r1
OUT = HERE / "results"
SEAL = r3.SEAL
EMBARGO = r3.EMBARGO
GAP = pd.Timedelta(minutes=5)
COVERAGE = 0.90
TARGETS = {"E4": 240, "E24": 1440}
NW_LAG = {"E4": 8, "E24": 48}
Z_WINDOWS = (4, 24, 120)

MV = ["L", "S_hd", "T"]
M1S = ["adx", "adx_percentile", "di_bias", "ema_slope", "price_vwap_dist"]
M1P = M1S + ["abs_di_bias", "abs_ema_slope", "abs_price_vwap_dist"]
MT = ["Z4", "Z24", "Z120"]
HOD = r3.HOD
MODELS = {"MV": MV, "M1+": M1P, "MT": MT, "MV+M1+": MV + M1P, "MV+MT": MV + MT,
          "M1+H": M1P + HOD, "MTH": MT + HOD, "MV+MTH": MV + MT + HOD}
# (A, B): d = e²_A − e²_B, p = one-sided "A better"
DM_PAIRS = [("MV+MT", "MV"), ("MV+M1+", "MV"), ("MV+M1+", "MV+MT"), ("MT", "M1+"),
            ("MTH", "MT"), ("M1+H", "M1+"), ("MV+MTH", "MV+MT")]
PERIODS = r3.PERIODS


def log(msg):
    r1.log(msg)


# =============================================================================
# 5-min returns and window sums
# =============================================================================

def five_min_returns(m: pd.DataFrame) -> pd.Series:
    """R_b indexed by bucket start b. NaN if the previous bucket is empty or the
    bucket holds a 1-min bar opening > 5 min after the previous 1-min bar."""
    dt = m.index.to_series().diff()
    flag = ((dt > GAP) | dt.isna()).to_numpy()
    b = m.index.floor("5min")
    g = pd.DataFrame({"close": m["close"].to_numpy(), "flag": flag}, index=b).groupby(level=0)
    last = g["close"].last()
    anyflag = g["flag"].max().astype(bool)
    R = np.log(last).diff()
    prev_ok = last.index.to_series().diff() == pd.Timedelta(minutes=5)
    R[(~prev_ok).to_numpy() | anyflag.to_numpy()] = np.nan
    return R


def bucket_sums(R: pd.Series, starts, ends):
    """Σ R, Σ R², Σ |R| and the valid count over buckets with start in [start, end)."""
    x = R.to_numpy()
    ok = ~np.isnan(x)
    z = np.where(ok, x, 0.0)
    pre = lambda v: np.concatenate([[0.0], np.cumsum(v)])
    cs, cs2, csa, cn = pre(z), pre(z * z), pre(np.abs(z)), pre(ok.astype(float))
    t = R.index.asi8
    i0 = np.searchsorted(t, np.asarray(starts), "left")
    i1 = np.searchsorted(t, np.asarray(ends), "left")
    return cs[i1] - cs[i0], cs2[i1] - cs2[i0], csa[i1] - csa[i0], cn[i1] - cn[i0]


def z_features(bar_index: pd.DatetimeIndex, R: pd.Series) -> pd.DataFrame:
    s, s2, _, n = bucket_sums(R, (bar_index - pd.Timedelta(hours=1)).asi8, bar_index.asi8)
    s, s2, n = (pd.Series(v, index=bar_index) for v in (s, s2, n))
    out = pd.DataFrame(index=bar_index)
    for w in Z_WINDOWS:
        S = s.rolling(w, min_periods=w).sum()
        S2 = s2.rolling(w, min_periods=w).sum()
        N = n.rolling(w, min_periods=w).sum()
        ok = (N >= COVERAGE * 12 * w) & (S2 > 0)
        signed = (S / np.sqrt(S2.where(S2 > 0))).where(ok)
        out[f"Z{w}"] = signed.abs()
        if w == 24:
            out["Z24_signed"] = signed
    return out


def targets(bar_index: pd.DatetimeIndex, R: pd.Series, end) -> pd.DataFrame:
    out = pd.DataFrame(index=bar_index)
    for name, h in TARGETS.items():
        e = bar_index + pd.Timedelta(minutes=h)
        s, _, sa, n = bucket_sums(R, bar_index.asi8, e.asi8)
        ok = (n >= COVERAGE * h / 5) & (sa > 0) & (e <= end)
        out[name] = np.where(ok, np.abs(s) / np.where(sa > 0, sa, 1.0), np.nan)
        if name == "E24":
            out["ret24"] = np.where(ok, s, np.nan)          # exploratory sign test only
    return out


# =============================================================================
# Existing trend features (repo code, unmodified)
# =============================================================================

def existing_trend(bars: pd.DataFrame) -> pd.DataFrame:
    rf, rc = r1._import_feature_engine()
    cfg = rc.RegimeConfig()
    eng = rf.RegimeFeatureEngine(config=cfg)
    df = eng._normalize_columns(bars[["open", "high", "low", "close"]])
    atr = eng._atr(df)
    f = pd.DataFrame(index=df.index)
    f["adx"] = eng._adx(df)                       # also stores DI+/DI- for di_bias
    f["adx_percentile"] = eng._rolling_percentile(f["adx"], cfg.adx_percentile_window)
    f["di_bias"] = eng._di_bias(df, atr)
    f["ema_slope"] = eng._ema_slope_normalized(df, atr)
    f["price_vwap_dist"] = eng._price_vwap_distance(df, atr)
    for c in ("di_bias", "ema_slope", "price_vwap_dist"):
        f[f"abs_{c}"] = f[c].abs()
    return f


def static_frame(m: pd.DataFrame, end):
    r, flag = r1.minute_returns(m)
    bars = r1.hourly_bars(m, flag)
    R = five_min_returns(m)
    hod = pd.DataFrame({f"h_{h}": (bars.index.hour == h).astype(float) for h in range(1, 24)},
                       index=bars.index)
    st = pd.concat([existing_trend(bars), z_features(bars.index, R), hod,
                    targets(bars.index, R, end)], axis=1)
    return bars, st.replace([np.inf, -np.inf], np.nan)


# =============================================================================
# Walk-forward
# =============================================================================

def run_period(name: str, cfg: dict) -> dict:
    log(f"=== {name} ===")
    m = r3.load_dev() if name == "DEV" else r3.load_conf()
    start, end = cfg["start"], cfg["end"]
    bars, st = static_frame(m, end)
    r3.assert_sealed(bars, f"{name} bars", end=end)
    inp = (bars.index >= start) & (bars.index < end)
    log(f"  1-min rows {len(m):,}, analysis bars {int(inp.sum()):,}")
    half = name == "CONF"

    frames, pattern, eta_rows, rho_rows = {}, None, [], []
    for k, train_end, ts, te in r3.folds(cfg["edges"]):
        vb = VolBlock().fit(m[(m.index >= start) & (m.index < train_end)])
        mv = vb.transform(m, bars.index)
        F = pd.concat([st, mv], axis=1)[inp]
        r3.assert_sealed(F, f"{name} fold {k}", end=end)
        for tg, h in TARGETS.items():
            e = F.index[F[tg].notna()] + pd.Timedelta(minutes=h)
            assert (e <= end).all(), f"{name}: {tg} window past the seal"
        pat = F[MV].notna().to_numpy()
        pattern = pat if pattern is None else pattern
        assert (pat == pattern).all(), "MV missingness depends on the fold"
        frames[k] = (train_end, ts, te, F)
        tr = F[F.index <= train_end]
        eta_rows.append({"fold": k, **{c: r3.eta2(tr[c]) for c in
                                       M1P + MT + ["L", "S", "S_hd", "T", "E4", "E24"]}})
        rho = tr[MT + MV].dropna().corr(method="spearman")
        pairs = [(a, b) for i, a in enumerate(MT) for b in MT[i + 1:]] + [(a, b) for a in MT for b in MV]
        rho_rows.append({"fold": k, **{f"{a}~{b}": float(rho.loc[a, b]) for a, b in pairs}})
    eta = pd.DataFrame(eta_rows).set_index("fold")
    rho = pd.DataFrame(rho_rows).set_index("fold")

    res = {"n_minutes": len(m), "n_bars": int(inp.sum()), "targets": {}}
    for tg in TARGETS:
        cols = sorted(set(sum(MODELS.values(), []))) + [tg]
        preds, coefs, n_rows = [], [], None
        for k, (train_end, ts, te, F) in frames.items():
            S = F.dropna(subset=cols)
            n_rows = len(S) if n_rows is None else n_rows
            assert len(S) == n_rows, "row count differs between folds"
            r3.assert_sealed(S, f"{name} sample {tg}", end=end)
            tr, tst = S[S.index <= train_end], S[(S.index >= ts) & (S.index < te)]
            assert tr.index.max() + EMBARGO <= ts
            P = pd.DataFrame({"fold": k, "block": r3.label(ts, half), "era": r3.era_of(ts, cfg["eras"]),
                              "y": tst[tg], "bench": tr[tg].mean()}, index=tst.index)
            for mn, feats in MODELS.items():
                P[mn] = r1.ols_fit_predict(tr, tst, feats, tg)[0]
            t_, b_ = r3.hac_tstats(tr, MODELS["MV+MT"], tg, NW_LAG[tg])
            for f in MODELS["MV+MT"]:
                coefs.append({"fold": k, "block": r3.label(ts, half), "era": r3.era_of(ts, cfg["eras"]),
                              "feature": f, "coef": b_[f], "hac_t": t_[f]})
            preds.append(P)
        P = pd.concat(preds).sort_index()
        P.to_csv(OUT / f"predictions_{name}_{tg}.csv", float_format="%.6f")
        C = pd.DataFrame(coefs)
        C.to_csv(OUT / f"coefs_MV+MT_{name}_{tg}.csv", index=False, float_format="%.6f")
        res["targets"][tg] = {"rows": n_rows, "test_rows": len(P), "pooled": metrics(P),
                              "per_block": per_block(P), "dm": dm(P, tg), "eras": {},
                              "coefs": C}
        for era in (cfg["eras"] or {}):
            Pe = P[P.era == era]
            res["targets"][tg]["eras"][era] = {"test_rows": len(Pe), "pooled": metrics(Pe), "dm": dm(Pe, tg)}
        log(f"  {tg}: rows {n_rows:,} test {len(P):,}  " +
            " ".join(f"{k2}={v['r2']:.4f}" for k2, v in res["targets"][tg]["pooled"].items()))
    res.update(eta=eta, rho=rho,
               fold_eras={k: r3.era_of(ts, cfg["eras"]) for k, (_, ts, _, _) in frames.items()})
    eta.to_csv(OUT / f"eta2_{name}.csv", float_format="%.4f")
    rho.to_csv(OUT / f"rho_{name}.csv", float_format="%.4f")
    return res


def metrics(P):
    bsse = ((P.y - P.bench) ** 2).sum()
    out = {"BENCH": {"r2": 0.0, "mae": float((P.y - P.bench).abs().mean())}}
    for mn in MODELS:
        out[mn] = {"r2": float(1 - ((P.y - P[mn]) ** 2).sum() / bsse),
                   "mae": float((P.y - P[mn]).abs().mean())}
    return out


def per_block(P):
    return [{"fold": int(k), "block": b, "n": len(g),
             **{f"{mn}_r2": v["r2"] for mn, v in metrics(g).items()},
             **{f"{mn}_mae": v["mae"] for mn, v in metrics(g).items()}}
            for (k, b), g in P.groupby(["fold", "block"])]


def dm(P, tg):
    out = {}
    for a, b in DM_PAIRS:
        s, p = r1.dm_test(((P.y - P[a]) ** 2).to_numpy(), ((P.y - P[b]) ** 2).to_numpy(), NW_LAG[tg])
        out[f"{a}|{b}"] = {"dm": s, "p_A_better": p}
    return out


# =============================================================================
# Rules
# =============================================================================

def rules(tres: dict, eta: pd.DataFrame, rho: pd.DataFrame, folds_sel=None) -> dict:
    T = list(TARGETS)
    r0 = {t: {"p_MVMT_better_than_MV": tres[t]["dm"]["MV+MT|MV"]["p_A_better"],
              "p_MVM1_better_than_MV": tres[t]["dm"]["MV+M1+|MV"]["p_A_better"],
              "r2_MV": tres[t]["pooled"]["MV"]["r2"], "r2_MVMT": tres[t]["pooled"]["MV+MT"]["r2"],
              "r2_MVM1": tres[t]["pooled"]["MV+M1+"]["r2"]} for t in T}
    R0_MT = any(v["p_MVMT_better_than_MV"] < 0.05 for v in r0.values())
    R0_M1 = any(v["p_MVM1_better_than_MV"] < 0.05 for v in r0.values())
    r1d = {t: {"r2_MVMT": tres[t]["pooled"]["MV+MT"]["r2"], "r2_MVM1": tres[t]["pooled"]["MV+M1+"]["r2"],
               "r2_ok": tres[t]["pooled"]["MV+MT"]["r2"] >= tres[t]["pooled"]["MV+M1+"]["r2"] - 0.005,
               "p_MVM1_better": tres[t]["dm"]["MV+M1+|MV+MT"]["p_A_better"]} for t in T}
    R1 = all(v["r2_ok"] and v["p_MVM1_better"] >= 0.05 for v in r1d.values())
    e = eta if folds_sel is None else eta.loc[folds_sel]
    eta_mt = {c: float(e[c].max()) for c in MT}
    gains = {t: {"MT": tres[t]["pooled"]["MTH"]["r2"] - tres[t]["pooled"]["MT"]["r2"],
                 "M1+": tres[t]["pooled"]["M1+H"]["r2"] - tres[t]["pooled"]["M1+"]["r2"],
                 "MV+MT": tres[t]["pooled"]["MV+MTH"]["r2"] - tres[t]["pooled"]["MV+MT"]["r2"]} for t in T}
    R2a = all(v < 0.05 for v in eta_mt.values())
    R2b = all(g["MT"] <= g["M1+"] for g in gains.values())
    rs = rho if folds_sel is None else rho.loc[folds_sel]
    rmax = rs.abs().max()
    R4 = {"max_abs_rho": {k: float(v) for k, v in rmax.items()},
          "flags": [k for k, v in rmax.items() if v > 0.5]}
    R5 = {}
    for t in T:
        C = tres[t]["coefs"]
        if folds_sel is not None:
            C = C[C.fold.isin(folds_sel)]
        for f in MV + MT:
            c = C[C.feature == f]
            s = c[c.hac_t.abs() > 2]
            if s.empty:
                R5[f"{t}:{f}"] = {"status": "no signal", "n_sig": 0, "n_folds": len(c)}
            else:
                share = max((s.coef > 0).mean(), (s.coef < 0).mean())
                R5[f"{t}:{f}"] = {"status": "pass" if share >= 0.8 else "FAIL", "n_sig": len(s),
                                  "n_folds": len(c), "sign_share": float(share),
                                  "sign": "+" if (s.coef > 0).mean() >= 0.5 else "-"}
    keep = [f for f in MT if not all(R5[f"{t}:{f}"]["status"] == "no signal" for t in T)
            and not any(R5[f"{t}:{f}"]["status"] == "FAIL" for t in T)]
    if R0_MT and R1 and R2a and R2b:
        rec = f"recommend MT: {', '.join(keep) if keep else '(no MT feature survives R5)'}"
    elif not R0_MT and not R0_M1:
        rec = "no trend block in the regime model (R0 fails for both)"
    else:
        failed = [n for n, ok in (("R0(MT)", R0_MT), ("R1", R1), ("R2a", R2a), ("R2b", R2b)) if not ok]
        rec = f"not recommended: {', '.join(failed)} failed"
    return {"R0": {"MT_pass": R0_MT, "M1+_pass": R0_M1, "detail": r0},
            "R1": {"pass": R1, "detail": r1d},
            "R2": {"a_pass": R2a, "b_pass": R2b, "eta2_max_MT": eta_mt, "hod_gain": gains},
            "R4": R4, "R5": R5, "recommendation": rec}


def main() -> int:
    OUT.mkdir(exist_ok=True)
    allres = {}
    for name, cfg in PERIODS.items():
        res = run_period(name, cfg)
        tres = res["targets"]
        dec = {"overall": rules(tres, res["eta"], res["rho"])}
        for era in (cfg["eras"] or {}):
            fs = [k for k, e in res["fold_eras"].items() if e == era]
            era_t = {t: {"pooled": tres[t]["eras"][era]["pooled"], "dm": tres[t]["eras"][era]["dm"],
                         "coefs": tres[t]["coefs"]} for t in TARGETS}
            dec[era] = rules(era_t, res["eta"], res["rho"], folds_sel=fs)
        allres[name] = {"n_minutes": res["n_minutes"], "n_bars": res["n_bars"],
                        "targets": {t: {k: v for k, v in tres[t].items() if k != "coefs"} for t in TARGETS},
                        "eta2_max_train": res["eta"].max().to_dict(), "decisions": dec}
        for scope, d in dec.items():
            log(f"  {name}/{scope}: R0 MT={d['R0']['MT_pass']} M1+={d['R0']['M1+_pass']} "
                f"R1={d['R1']['pass']} R2a={d['R2']['a_pass']} R2b={d['R2']['b_pass']} "
                f"R4 flags={d['R4']['flags']} -> {d['recommendation']}")
    (OUT / "results_trend.json").write_text(json.dumps(allres, indent=2, default=float))
    log("Done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
