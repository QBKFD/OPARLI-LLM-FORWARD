#!/usr/bin/env python3
"""
DIAGNOSTIC / EXPLORATORY ONLY (see DIAGNOSTIC_PLAN.md). 2007-2023, seen data.

Reuses research/levels_confirm/confirm.py and research/levels/levels.py
UNCHANGED (imported). New here: yearly expanding VolBlock refits, the Q5
context-matched placebo builder, the old same-day design run on the same σ unit,
and the machinery validation (A/A + injection), which gates Q1-Q5.

    python research/levels_diag/diag.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "levels_confirm"))
import confirm as cf  # noqa: E402  (unchanged)

lv = cf.lv
OUT, FIG = HERE / "results", HERE / "figures"
START, END = "2007-01-01", "2023-12-31"
B = 10_000
N_AA = 200
N_INJ = 200


def log(m):
    lv.log(m)


def two_sided(draws):
    d = draws[np.isfinite(draws)]
    lo = (np.sum(d <= 0) + 1) / (len(d) + 1)
    hi = (np.sum(d >= 0) + 1) / (len(d) + 1)
    return float(min(1.0, 2 * min(lo, hi)))


def cell(sub: pd.DataFrame, value: str, seed: int, W_cache={}):
    """Effect with its own real-day bootstrap over the clusters present in the cell."""
    real = sub[~sub.is_placebo]
    if real.empty:
        return {"effect": np.nan, "n_real": 0, "n_placebo": 0}
    clusters = pd.Index(sorted(real.cluster.unique()))
    key = (len(clusters), seed)
    if key not in W_cache:
        W_cache.clear()
        W_cache[key] = cf.boot_W(len(clusters), B=B, seed=seed)
    pt, dr = cf.effect_draws(sub, value, clusters, W_cache[key])
    s = cf.summarize(pt, dr)
    s["p_two"] = two_sided(dr)
    return {k: s[k] for k in ("effect", "ci_lo", "ci_hi", "p_two", "real_rate", "placebo_rate", "n_real", "n_placebo")}


# =============================================================================
# Data, σ unit (yearly expanding refits), days
# =============================================================================

def load():
    old = cf.pre2024()                                   # asserts < 2024-01-01
    m = old[(old.index >= pd.Timestamp("2006-01-01", tz="UTC")) & (old.index < pd.Timestamp("2024-01-01", tz="UTC"))]
    cf.assert_before_global_seal(m.index, "diag data")
    return m


def sigma_yearly(m: pd.DataFrame):
    ts = m.index
    td_year = pd.DatetimeIndex(lv.trading_dates(ts)).year.to_numpy()
    f = np.full(len(m), np.nan)
    L = np.full(len(m), np.nan)
    for Y in range(2007, 2024):
        fit_end = pd.Timestamp(f"{Y}-01-01", tz="UTC")
        m_fit = m[ts < fit_end]
        sub_mask = (ts >= pd.Timestamp(f"{Y - 1}-12-01", tz="UTC")) & (ts < pd.Timestamp(f"{Y + 1}-01-02", tz="UTC"))
        m_st = m[sub_mask]
        fy, Ly = cf.sigma_single_fit(m_fit, m_st, fit_end)
        pos = np.flatnonzero(sub_mask)
        sel = td_year[pos] == Y
        f[pos[sel]], L[pos[sel]] = fy[sel], Ly[sel]
        log(f"  σ unit {Y}: fit 2006-01-01..{fit_end.date()}, median σ/price {np.nanmedian(fy[sel]):.4f}")
    return f, L


def day_context(days: pd.DataFrame, c: np.ndarray):
    close = c[days["i1"].to_numpy()]
    d = days.copy()
    d["close"] = close
    d["prev_ret"] = np.log(d["close"].shift(1) / d["close"].shift(2))
    d["trend20"] = np.log(d["close"].shift(1) / d["close"].shift(21))
    rng_ = d["PDH"] - d["PDL"]
    d["pos"] = (d["open"] - d["PDL"]) / rng_.where(rng_ > 0)
    return d


# =============================================================================
# Q5 builder: confirm.build_units + context matching of the candidate days
# =============================================================================

def build_units_ctx(days, test_idx, mk, ctx_class: dict, seed=20260132):
    """Same as confirm.build_units, but placebo days must share the real day's context class."""
    rng = np.random.default_rng(seed)
    pos = {di: k for k, di in enumerate(test_idx)}
    units, short = [], 0

    def add(**kw):
        kw["id"] = len(units)
        units.append(kw)
        return kw["id"]

    for di in test_idx:
        row = days.loc[di]
        i0, o = int(row["i0"]), float(row["open"])
        date = pd.Timestamp(row["date"])
        cls = ctx_class.get(di)
        for name, side in (("PDH", +1), ("PDL", -1)):
            lvl = row[name]
            if not np.isfinite(lvl):
                continue
            sig0 = abs(lvl) * mk.f[i0]
            if not (np.isfinite(sig0) and lv.eligible(side * o, side * lvl, sig0)):
                continue
            d = side * (lvl - o) / sig0
            rid = add(level=float(lvl), side=side, family="DAILY", name=name, days=[di], cluster=str(date.date()),
                      period_date=date, parent=None, is_placebo=False, j=-1, dist=float(d), pday=di)
            units[rid]["parent"] = rid
            k = pos[di]
            window = [test_idx[x] for x in range(max(0, k - cf.WINDOW), min(len(test_idx), k + cf.WINDOW + 1))
                      if x != k and cls is not None and ctx_class.get(test_idx[x]) == cls]
            if not window:
                short += cf.N_PLACEBO
                continue
            for j in range(cf.N_PLACEBO):
                for _ in range(1000):
                    P = window[rng.integers(len(window))]
                    rp = days.loc[P]
                    i0p, op = int(rp["i0"]), float(rp["open"])
                    fp = mk.f[i0p]
                    if not np.isfinite(fp):
                        continue
                    p = cf.placebo_level(op, side, d, fp)
                    sp = abs(p) * fp
                    reals = [r for r in (rp["PDH"], rp["PDL"]) if np.isfinite(r)]
                    if reals and min(abs(p - r) for r in reals) < cf.EXCL * sp:
                        continue
                    if not lv.eligible(side * op, side * p, sp):
                        continue
                    add(level=float(p), side=side, family="DAILY", name=name, days=[P], cluster=str(date.date()),
                        period_date=date, parent=rid, is_placebo=True, j=j, dist=float(d), pday=P)
                    break
                else:
                    short += 1
    return units, short


# =============================================================================
# Evaluation
# =============================================================================

def evaluate(units, mk, days, design):
    uj = {u["id"]: u["j"] for u in units}
    ud = {u["id"]: u.get("dist", np.nan) for u in units}
    recs = []
    for u in units:
        recs.extend(lv.run_unit(u, mk, days))
    ev = pd.DataFrame(recs)
    ev["design"] = design
    ev["j"] = ev.unit.map(uj)
    ev["dist"] = ev.parent.map(ud)
    ev["cls"] = np.where((ev.dist >= cf.NEAR[0]) & (ev.dist <= cf.NEAR[1]), "NEAR",
                         np.where(ev.dist > cf.NEAR[1], "FAR", "none"))
    ev["year"] = pd.to_datetime(ev.period_date).dt.year
    ev["tgt"] = (ev[f"o_{lv.B_MAIN}_{lv.H_MAIN}"] == 1).astype(float)
    return ev


def old_units(days, test_idx, mk):
    units, _ = lv.build_units(days.loc[test_idx], mk, families=("DAILY",))
    by = {u["id"]: u for u in units}
    for u in units:
        if not u["is_placebo"]:
            di = u["days"][0]
            i0 = int(days.at[di, "i0"])
            u["dist"] = u["side"] * (u["level"] - days.at[di, "open"]) / (abs(u["level"]) * mk.f[i0])
    for u in units:
        if u["is_placebo"]:
            u["dist"] = by[u["parent"]]["dist"]
            di = u["days"][0]
            i0 = int(days.at[di, "i0"])
            u["own_dist"] = u["side"] * (u["level"] - days.at[di, "open"]) / (abs(u["level"]) * mk.f[i0])
    return units


# =============================================================================
# Validation (gates Q1-Q5)
# =============================================================================

def aa_test(ev_c, units_c, cls):
    br = ev_c[(ev_c.etype == "BREAK") & (ev_c.cls == cls) & ev_c.is_placebo].copy()
    real_ids = sorted({u["id"] for u in units_c if not u["is_placebo"]})
    uid = {}
    for u in units_c:
        if u["is_placebo"]:
            uid[(u["parent"], u["j"])] = u["id"]
    clusters = pd.Index(sorted({u["cluster"] for u in units_c if not u["is_placebo"]}))
    W = cf.boot_W(len(clusters), B=B, seed=777)
    rng = np.random.default_rng(4242)
    maxid = max(real_ids) + 1
    effs, cover = [], []
    for _ in range(N_AA):
        k = rng.integers(0, cf.N_PLACEBO, size=maxid)
        par = br.parent.to_numpy()
        pseudo = br.j.to_numpy() == k[par]
        new_parent = np.array([uid.get((p, k[p]), -1) for p in par])
        sub = br.assign(is_placebo=~pseudo, parent=np.where(pseudo, br.unit.to_numpy(), new_parent))
        pt, dr = cf.effect_draws(sub, "tgt", clusters, W)
        s = cf.summarize(pt, dr)
        effs.append(s["effect"])
        cover.append(s["ci_lo"] <= 0 <= s["ci_hi"])
    effs = np.array(effs)
    return {"class": cls, "reps": N_AA, "mean_effect": float(effs.mean()), "sd_effect": float(effs.std()),
            "ci_coverage_of_0": float(np.mean(cover)),
            "pass": bool(abs(effs.mean()) <= 0.01 and 0.93 <= np.mean(cover) <= 0.97)}


def injection_test(ev_c, delta):
    sub = ev_c[(ev_c.etype == "BREAK") & (ev_c.cls == "NEAR") & (ev_c.year >= 2021)].copy()
    real = sub[~sub.is_placebo]
    plc = sub[sub.is_placebo & sub.parent.isin(real.unit)]
    p0 = plc.tgt.mean()
    clusters = pd.Index(sorted(real.cluster.unique()))
    W = cf.boot_W(len(clusters), B=B, seed=999)
    rng = np.random.default_rng(31337 + int(delta * 1000))
    rec, cover = [], []
    is_real = ~sub.is_placebo.to_numpy()
    for _ in range(N_INJ):
        v = sub.tgt.to_numpy().copy()
        v[is_real] = (rng.random(is_real.sum()) < p0 + delta).astype(float)
        pt, dr = cf.effect_draws(sub.assign(inj=v), "inj", clusters, W)
        s = cf.summarize(pt, dr)
        rec.append(s["effect"])
        cover.append(s["ci_lo"] <= delta <= s["ci_hi"])
    rec = np.array(rec)
    return {"delta": delta, "placebo_rate": float(p0), "n_real": int(len(real)), "reps": N_INJ,
            "mean_recovered": float(rec.mean()), "sd_recovered": float(rec.std()),
            "bias": float(rec.mean() - delta), "ci_coverage_of_delta": float(np.mean(cover))}


# =============================================================================
# Main
# =============================================================================

def main() -> int:
    OUT.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    log("DIAGNOSTIC run (exploratory): loading < 2024-01-01")
    m = load()
    f, L = sigma_yearly(m)
    ts = m.index
    o, h, l, c = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
    days = day_context(lv.attach_levels(lv.day_table(ts, o, h, l, c)), c)
    mk = lv.Market(ts.asi8, o, h, l, c, f, L)
    del m
    dd = pd.DatetimeIndex(days.date)
    test_idx = list(days.index[(~days.excluded) & (dd >= START) & (dd <= END)])
    info = {"test_days": len(test_idx), "excluded_2007_2023": int(days[(dd >= START) & (dd <= END)].excluded.sum())}

    log("Corrected design (confirm.build_units, unchanged)")
    units_c, short_c = cf.build_units(days, test_idx, mk)
    ev_c = evaluate(units_c, mk, days, "corrected")
    info.update(corrected_units=len(units_c), corrected_real=sum(not u["is_placebo"] for u in units_c),
                corrected_short=short_c)
    log(f"  {info}")

    # ---------------- validation first (gate)
    log("Validation: A/A (200 reps each) and injection (200 reps each)")
    val = {"aa": [aa_test(ev_c, units_c, "NEAR"), aa_test(ev_c, units_c, "FAR")],
           "injection": [injection_test(ev_c, +0.10), injection_test(ev_c, -0.10)]}
    for a in val["aa"]:
        log(f"  A/A {a['class']}: mean {a['mean_effect']:+.4f}, coverage {a['ci_coverage_of_0']:.3f}, pass {a['pass']}")
    for i in val["injection"]:
        log(f"  inject {i['delta']:+.2f}: recovered {i['mean_recovered']:+.4f} (bias {i['bias']:+.4f}), coverage {i['ci_coverage_of_delta']:.3f}")
    res = {"info": info, "validation": val}
    if not all(a["pass"] for a in val["aa"]):
        res["stopped"] = "A/A test failed: diagnostic results withheld; see validation.aa for the bias"
        (OUT / "diag_results.json").write_text(json.dumps(res, indent=1, default=float))
        log("A/A FAILED -> stopping before Q1-Q5")
        return 0

    log("Old same-day design (levels.build_units, DAILY) on the same σ unit")
    units_o = old_units(days, test_idx, mk)
    ev_o = evaluate(units_o, mk, days, "old_same_day")
    log("Q5 context-matched design")
    q = days.loc[test_idx, "pos"].dropna()
    t1, t2 = np.quantile(q, [1 / 3, 2 / 3])
    ctx = {}
    for di in test_idx:
        pr, pv = days.at[di, "pos"], days.at[di, "prev_ret"]
        if np.isfinite(pr) and np.isfinite(pv) and pv != 0:
            ctx[di] = (int(np.digitize(pr, [t1, t2])), int(np.sign(pv)))
    units_x, short_x = build_units_ctx(days, test_idx, mk, ctx)
    ev_x = evaluate(units_x, mk, days, "context_matched")
    info.update(context_units=len(units_x), context_short=short_x, pos_terciles=[float(t1), float(t2)])

    # attach real-day covariates (by parent's day)
    for ev, units in ((ev_c, units_c), (ev_o, units_o), (ev_x, units_x)):
        pday = {u["id"]: u["days"][0] for u in units}
        par_day = ev.parent.map(pday)
        ev["trend_up"] = par_day.map(lambda x: days.at[x, "trend20"] > 0 if np.isfinite(days.at[x, "trend20"]) else np.nan)
    BR = lambda ev: ev[ev.etype == "BREAK"]
    per = lambda ev, a, b: ev[(ev.year >= a) & (ev.year <= b)]

    # ---------------- Q1
    q1 = {}
    for design, ev in (("corrected", ev_c), ("old_same_day", ev_o)):
        for cls in ("NEAR", "FAR"):
            q1[f"{design}:{cls}:2007-2020"] = cell(per(BR(ev), 2007, 2020)[lambda x: x.cls == cls], "tgt", 11)
    pub = json.loads((ROOT / "research" / "levels" / "results" / "results_levels.json").read_text())
    pubd = json.loads((ROOT / "research" / "levels" / "results" / "exploratory_distance.json").read_text())
    q1["published_levels_DAILY_H2"] = next({k: r[k] for k in ("conf_effect", "conf_ci_lo", "conf_ci_hi")}
                                          for r in pub["primary"] if r["family"] == "DAILY" and r["hyp"] == "H2")
    q1["published_distance_quintiles_DAILY_H2"] = pubd["DAILY:H2"]["bins"]
    # ---------------- Q2
    years = {}
    for design, ev in (("corrected", ev_c), ("old_same_day", ev_o)):
        for Y in range(2007, 2024):
            years[f"{design}:{Y}"] = cell(BR(ev)[(BR(ev).cls == "NEAR") & (BR(ev).year == Y)], "tgt", 100 + Y)
    q2 = {"per_year": years}
    b = BR(ev_c)[BR(ev_c).cls == "NEAR"]
    for a_, b_ in ((2007, 2023), (2007, 2020)):
        for name in ("PDH", "PDL"):
            q2[f"{name}:{a_}-{b_}"] = cell(per(b, a_, b_)[lambda x: x.name == name], "tgt", 21)
    for up in (True, False):
        q2[f"trend_{'up' if up else 'down'}:2007-2023"] = cell(b[b.trend_up == up], "tgt", 22)
        for name in ("PDH", "PDL"):
            with_trend = (name == "PDH") == up
            q2[f"{name}|trend_{'up' if up else 'down'} ({'with' if with_trend else 'against'} trend)"] = \
                cell(b[(b.trend_up == up) & (b.name == name)], "tgt", 23)
    # ---------------- Q3
    q3 = {}
    for et, fill, lab in (("TOUCH", lv.FILL, "H1_touch"), ("SWEEP", None, "H3_sweep")):
        e = ev_c[(ev_c.etype == et) & ((ev_c.fill == fill) if fill is not None else True)]
        for cls in ("NEAR", "FAR"):
            for a_, b_ in ((2007, 2020), (2021, 2023)):
                q3[f"{lab}:{cls}:{a_}-{b_}"] = cell(per(e, a_, b_)[lambda x: x.cls == cls], "tgt", 31)
    # ---------------- Q4
    q4 = {}
    for design, ev in (("corrected", ev_c), ("old_same_day", ev_o)):
        for cls in ("NEAR", "FAR"):
            q4[f"{design}:{cls}:2021-2023"] = cell(per(BR(ev), 2021, 2023)[lambda x: x.cls == cls], "tgt", 41)
    od = [u["own_dist"] for u in units_o if u["is_placebo"] and 2021 <= pd.Timestamp(u["period_date"]).year <= 2023]
    rd = [u["dist"] for u in units_o if not u["is_placebo"] and 2021 <= pd.Timestamp(u["period_date"]).year <= 2023]
    q4["placebo_distance_from_own_open"] = {"old_same_day_placebos_mean": float(np.mean(od)),
                                             "real_levels_mean": float(np.mean(rd)),
                                             "corrected_placebos_mean": "identical to their real level by construction"}
    # ---------------- Q5
    q5 = {}
    for design, ev in (("corrected", ev_c), ("context_matched", ev_x)):
        for cls in ("NEAR", "FAR"):
            for a_, b_ in ((2007, 2020), (2021, 2023), (2007, 2023)):
                q5[f"{design}:{cls}:{a_}-{b_}"] = cell(per(BR(ev), a_, b_)[lambda x: x.cls == cls], "tgt", 51)
    res.update(q1=q1, q2=q2, q3=q3, q4=q4, q5=q5)
    (OUT / "diag_results.json").write_text(json.dumps(res, indent=1, default=float))
    year_figure(years, FIG / "near_effect_per_year.png")
    log("Done")
    return 0


def year_figure(years, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(12, 5.5))
    for design, col, off in (("corrected", "#c62828", -0.15), ("old_same_day", "#616161", +0.15)):
        Y = np.arange(2007, 2024)
        e = np.array([years[f"{design}:{y}"]["effect"] for y in Y])
        lo = np.array([years[f"{design}:{y}"].get("ci_lo", np.nan) for y in Y])
        hi = np.array([years[f"{design}:{y}"].get("ci_hi", np.nan) for y in Y])
        ax.errorbar(Y + off, 100 * e, yerr=[100 * (e - lo), 100 * (hi - e)], fmt="o", color=col, capsize=3,
                    label={"corrected": "corrected (day-shifted, distance-matched placebos)",
                           "old_same_day": "old design (same-day placebos)"}[design])
    ax.axhline(0, color="black", lw=0.6)
    ax.axvline(2020.5, color="#1565c0", lw=0.8, ls=":")
    ax.set_xticks(np.arange(2007, 2024))
    ax.set_ylabel("NEAR break-continuation effect, real − placebo (pp)")
    ax.set_title("DIAGNOSTIC: NEAR PDH/PDL break continuation vs placebos, per year (95 % CI, real-day bootstrap)")
    ax.legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())
