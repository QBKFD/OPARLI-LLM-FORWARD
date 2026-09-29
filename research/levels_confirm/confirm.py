#!/usr/bin/env python3
"""
Levels confirmation test (PREREG.md). Reuses research/levels unchanged
(run_unit, first_break, first_passage, eligibility); adds only what PREREG
lists: one VolBlock fit before the test period, distance-matched day-shifted
placebos, a real-day bootstrap, MDE and the verdict mapping.

    python research/levels_confirm/confirm.py dry       # declared dry run, 2021-2023 (seen data)
    python research/levels_confirm/confirm.py confirm   # the one confirmatory run, 2024-01-02..2026-01-30
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "research" / "levels"))
sys.path.insert(0, str(HERE))
import levels as lv  # noqa: E402   (research/levels, unchanged)
from sealing import assert_before_global_seal  # noqa: E402
import audit_2024 as au  # noqa: E402
from vol_block import VolBlock  # noqa: E402   (on sys.path via levels)

OUT, FIG = HERE / "results", HERE / "figures"
N_PLACEBO = 20
WINDOW = 60
EXCL = 0.15
NEAR = (0.1, 0.5)
B_BOOT = 10_000
Z = 1.6449 + 0.8416
MODES = {
    "dry": {"data_start": "2020-11-01", "test_start": "2021-01-04", "test_end": "2023-06-30",
            "fit_end": "2021-01-01"},
    "confirm": {"data_start": "2023-11-01", "test_start": "2024-01-02", "test_end": "2026-01-30",
                "fit_end": "2024-01-01"},
}


def log(msg):
    lv.log(msg)


# =============================================================================
# Loading and the volatility unit
# =============================================================================

def pre2024():
    df = pd.read_parquet(lv.r3.CACHE).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_localize("UTC")
    lv.assert_sealed_ns(df.index.asi8, "pre-2024 cache")            # the old seal still holds for this file
    return df[["open", "high", "low", "close"]]


def load_study(mode: str) -> pd.DataFrame:
    cfg = MODES[mode]
    end = pd.Timestamp(cfg["test_end"], tz="UTC") + pd.Timedelta(days=1)
    if mode == "confirm":
        df = pd.read_parquet(au.CACHE).sort_index()
        df = df[~df.index.duplicated(keep="first")]
        df.index = df.index.tz_localize("UTC")
        df = df[(df.index >= pd.Timestamp(cfg["data_start"], tz="UTC")) & (df.index < end)]
        au.assert_study_window(df.index, "confirm study data")    # nothing after 2026-01-30
    else:
        df = pre2024()
        df = df[(df.index >= pd.Timestamp(cfg["data_start"], tz="UTC")) & (df.index < end)]
    assert_before_global_seal(df.index, "study data")
    bad = ~((df.high >= df.low) & (df[["open", "high", "low", "close"]] > 0).all(axis=1))
    return df[~bad][["open", "high", "low", "close"]]


def sigma_single_fit(m_fit: pd.DataFrame, m_study: pd.DataFrame, fit_end: pd.Timestamp):
    """One VolBlock fit on data < fit_end; applied to the study minutes with no refit."""
    if len(m_fit) and m_fit.index.max() >= fit_end:
        raise AssertionError(f"VolBlock fit data reaches {m_fit.index.max()} >= {fit_end}")
    vb = VolBlock().fit(m_fit)
    L = vb.transform(m_study)["L"].ffill()
    ts = m_study.index
    local = ts.tz_convert(lv.NY).tz_localize(None)
    dst = np.asarray((local - ts.tz_localize(None)) == pd.Timedelta(hours=-4)).astype(int)
    pbar = np.array([vb.profile_[:288].mean(), vb.profile_[288:].mean()])
    k = np.searchsorted(L.index.asi8, ts.asi8, side="right") - 1
    Lk = np.where(k >= 0, L.to_numpy()[np.clip(k, 0, None)], np.nan)
    return np.sqrt(np.exp(Lk) * pbar[dst] * 1440), Lk


# =============================================================================
# Units: real PDH/PDL + distance-matched, day-shifted placebos
# =============================================================================

def placebo_level(open_p: float, side: int, d: float, f_p: float) -> float:
    """Same side, exactly distance d in σ_d units of day P (σ priced at the level)."""
    return open_p / (1.0 - side * d * f_p)


def build_units(days: pd.DataFrame, test_idx: list, mk, seed=20260130):
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
            window = [test_idx[x] for x in range(max(0, k - WINDOW), min(len(test_idx), k + WINDOW + 1)) if x != k]
            for j in range(N_PLACEBO):
                for _ in range(1000):
                    P = window[rng.integers(len(window))]
                    rp = days.loc[P]
                    i0p, op = int(rp["i0"]), float(rp["open"])
                    fp = mk.f[i0p]
                    if not np.isfinite(fp):
                        continue
                    p = placebo_level(op, side, d, fp)
                    sp = abs(p) * fp
                    reals = [r for r in (rp["PDH"], rp["PDL"]) if np.isfinite(r)]
                    if reals and min(abs(p - r) for r in reals) < EXCL * sp:
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
# Inference
# =============================================================================

def boot_W(n, B=B_BOOT, seed=20260131):
    rng = np.random.default_rng(seed)
    return rng.multinomial(n, np.full(n, 1.0 / n), size=B).astype(np.float32)


def effect_draws(sub: pd.DataFrame, value: str, clusters: pd.Index, W, parents_ok=None):
    """Point effect and bootstrap draws; clusters = real days (placebos carry their real day)."""
    real = sub[~sub.is_placebo]
    par = set(real.unit) if parents_ok is None else parents_ok
    plc = sub[sub.is_placebo & sub.parent.isin(par)]
    cid = pd.Series(np.arange(len(clusters)), index=clusters)
    agg = lambda g, v: np.bincount(cid[g.cluster].to_numpy(), weights=None if v is None else g[v].to_numpy(),
                                   minlength=len(clusters)).astype(float)
    rs, rn, ps, pn = agg(real, value), agg(real, None), agg(plc, value), agg(plc, None)
    point = rs.sum() / max(rn.sum(), 1) - ps.sum() / max(pn.sum(), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        draws = (W @ rs) / (W @ rn) - (W @ ps) / (W @ pn)
    return {"effect": float(point), "real_rate": float(rs.sum() / max(rn.sum(), 1)),
            "placebo_rate": float(ps.sum() / max(pn.sum(), 1)), "n_real": int(rn.sum()),
            "n_placebo": int(pn.sum())}, draws


def summarize(point: dict, draws: np.ndarray) -> dict:
    d = draws[np.isfinite(draws)]
    se = float(np.std(d))
    return {**point, "ci_lo": float(np.percentile(d, 2.5)), "ci_hi": float(np.percentile(d, 97.5)),
            "p_less": float((np.sum(d >= 0) + 1) / (len(d) + 1)), "se": se, "mde": Z * se, "n_draws": int(len(d))}


def verdict(s: dict) -> str:
    e, p, mde = s["effect"], s["p_less"], s["mde"]
    if p < 0.05 and e <= -0.05:
        return "SUPPORTED"
    if e >= 0:
        return "REJECTED"
    if p >= 0.05:
        return "REJECTED" if mde <= 0.10 else "INCONCLUSIVE"
    return "SMALLER THAN CLAIMED"


def holm(p: list) -> list:
    m = len(p)
    order = np.argsort(p)
    adj, run = np.empty(m), 0.0
    for rank, i in enumerate(order):
        run = max(run, min(1.0, (m - rank) * p[i]))
        adj[i] = run
    return adj.tolist()


# =============================================================================
# Main
# =============================================================================

def main(mode: str) -> int:
    cfg = MODES[mode]
    OUT.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    fit_end = pd.Timestamp(cfg["fit_end"], tz="UTC")
    log(f"=== {mode.upper()} run: test {cfg['test_start']} .. {cfg['test_end']}, VolBlock fit < {fit_end.date()} ===")
    old = pre2024()
    m_fit = old[(old.index >= pd.Timestamp("2007-01-01", tz="UTC")) & (old.index < fit_end)]
    del old
    m = load_study(mode)
    f, Lmin = sigma_single_fit(m_fit, m, fit_end)
    del m_fit
    ts = m.index
    o, h, l, c = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
    days = lv.attach_levels(lv.day_table(ts, o, h, l, c))
    mk = lv.Market(ts.asi8, o, h, l, c, f, Lmin)
    dd = pd.DatetimeIndex(days.date)
    test_idx = list(days.index[(~days.excluded) & (dd >= cfg["test_start"]) & (dd <= cfg["test_end"])])
    info = {"mode": mode, "minutes": len(m), "test_days": len(test_idx),
            "test_days_excluded": int(days[(dd >= cfg["test_start"]) & (dd <= cfg["test_end"])].excluded.sum()),
            "median_sigma_over_price": float(np.nanmedian(f[ts >= pd.Timestamp(cfg["test_start"], tz="UTC")]))}
    del m
    units, short = build_units(days, test_idx, mk)
    info.update(units=len(units), real_units=sum(not u["is_placebo"] for u in units), placebo_short=short)
    log(f"  {info}")

    dist = {u["id"]: u["dist"] for u in units}
    path_sum, path_n, recs, has_break = {}, {}, [], set()
    for u in units:
        for rec in lv.run_unit(u, mk, days, with_path=True):
            path = rec.pop("path", None)
            if rec["etype"] == "BREAK" and path is not None:
                if not rec["is_placebo"]:
                    has_break.add(rec["unit"])
                near = NEAR[0] <= dist[rec["unit"]] <= NEAR[1]
                if near and (not rec["is_placebo"] or rec["parent"] in has_break):
                    k = "real" if not rec["is_placebo"] else u["j"]
                    path_sum[k] = path_sum.get(k, 0) + path.astype(np.float64)
                    path_n[k] = path_n.get(k, 0) + 1
            recs.append(rec)
    ev = pd.DataFrame(recs)
    assert_before_global_seal(ev.t_event.to_numpy(), "events")
    if mode == "confirm":
        assert pd.Timestamp(ev.t_event.max(), tz="UTC") < au.STUDY_END.tz_localize("UTC"), "event after 2026-01-30"
    ev["dist"] = ev.unit.map(dist)
    ev["cls"] = np.where((ev.dist >= NEAR[0]) & (ev.dist <= NEAR[1]), "NEAR", np.where(ev.dist > NEAR[1], "FAR", "none"))
    ev["hour"] = pd.to_datetime(ev.t_event, utc=True).dt.hour
    ev["session"] = np.where(ev.hour < 7, "Asia", np.where(ev.hour < 17, "LonNY", "Late"))
    clusters = pd.Index(sorted({u["cluster"] for u in units if not u["is_placebo"]}))
    W = boot_W(len(clusters))

    br = ev[ev.etype == "BREAK"].copy()
    br["cont"] = (br[f"o_{lv.B_MAIN}_{lv.H_MAIN}"] == 1).astype(float)
    pn, dn = effect_draws(br[br.cls == "NEAR"], "cont", clusters, W)
    pf, df_ = effect_draws(br[br.cls == "FAR"], "cont", clusters, W)
    near, far = summarize(pn, dn), summarize(pf, df_)
    near["verdict"] = verdict(near)
    ok = np.isfinite(dn) & np.isfinite(df_)
    diff = dn[ok] - df_[ok]
    spec = {"near_minus_far": near["effect"] - far["effect"],
            "ci_lo": float(np.percentile(diff, 2.5)), "ci_hi": float(np.percentile(diff, 97.5)),
            "p_less": float((np.sum(diff >= 0) + 1) / (len(diff) + 1))}
    if near["verdict"] == "SUPPORTED":
        spec["conclusion"] = ("PDH/PDL breaks fail generally; the near-open part is not supported"
                              if spec["p_less"] >= 0.10 else "near-open breaks fail more than far ones")
    res = {"info": info, "main_near": near, "far": far, "specificity": spec}
    # ---- side claims only if SUPPORTED
    if near["verdict"] == "SUPPORTED":
        nb = br[br.cls == "NEAR"]
        par = set(nb[~nb.is_placebo].unit)
        cells = {}
        for sname in ("Asia", "LonNY", "Late"):
            cells[sname] = effect_draws(nb[nb.session == sname], "cont", clusters, W, parents_ok=par)
        side = {}
        for sname in ("Asia", "Late"):
            a, b = cells[sname][1], cells["LonNY"][1]
            okk = np.isfinite(a) & np.isfinite(b)
            dd_ = a[okk] - b[okk]
            side[sname] = {"cell_effect": cells[sname][0], "lonny_effect": cells["LonNY"][0],
                           "diff": cells[sname][0]["effect"] - cells["LonNY"][0]["effect"],
                           "p_less": float((np.sum(dd_ >= 0) + 1) / (len(dd_) + 1))}
        adj = holm([side["Asia"]["p_less"], side["Late"]["p_less"]])
        for sname, a in zip(("Asia", "Late"), adj):
            side[sname]["p_holm"] = a
            side[sname]["supported"] = a < 0.05
        res["side_claims"] = side
    else:
        res["side_claims"] = "not evaluated (main claim not SUPPORTED)"
    # ---- exploratory (labelled; cannot change the verdict)
    expl = {}
    for etype, fill, lab in (("TOUCH", lv.FILL, "H1_touch_reaction"), ("SWEEP", None, "H3_sweep_reversal")):
        sub = ev[(ev.etype == etype) & ((ev.fill == fill) if fill is not None else True)].copy()
        sub["t"] = (sub[f"o_{lv.B_MAIN}_{lv.H_MAIN}"] == 1).astype(float)
        for cls in ("NEAR", "FAR"):
            pt, dr = effect_draws(sub[sub.cls == cls], "t", clusters, W)
            s = summarize(pt, dr)
            expl[f"{lab}:{cls}"] = {k: s[k] for k in ("effect", "ci_lo", "ci_hi", "n_real", "n_placebo")}
    grid = {}
    for b in lv.BS:
        for hm in lv.HS:
            s = br[br.cls == "NEAR"].assign(t=(br[f"o_{b}_{hm}"] == 1).astype(float))
            grid[f"b{b}_h{hm}"] = effect_draws(s, "t", clusters, W)[0]["effect"]
    expl["break_near_grid"] = grid
    res["exploratory"] = expl
    res["counts"] = {cls: {"real_break_events": int(((br.cls == cls) & ~br.is_placebo).sum()),
                           "placebo_break_events_paired": int(effect_draws(br[br.cls == cls], "cont", clusters, W)[0]["n_placebo"]),
                           "real_levels_eligible": int(sum(1 for u in units if not u["is_placebo"] and
                                                           ((NEAR[0] <= u["dist"] <= NEAR[1]) if cls == "NEAR" else u["dist"] > NEAR[1])))}
                     for cls in ("NEAR", "FAR")}
    figure(path_sum, path_n, FIG / f"{mode}_event_study_near.png", mode)
    (OUT / f"{mode}_results.json").write_text(json.dumps(res, indent=1, default=float))
    log(f"  NEAR: effect {near['effect']:+.4f} CI [{near['ci_lo']:+.4f}, {near['ci_hi']:+.4f}] "
        f"p {near['p_less']:.4g} MDE {near['mde']:.4f} -> {near['verdict']}")
    log(f"  FAR: effect {far['effect']:+.4f}; near-far {spec['near_minus_far']:+.4f} p {spec['p_less']:.4g}")
    log("Done")
    return 0


def figure(path_sum, path_n, path, mode):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x = lv.PATH_OFFSETS
    fig, ax = plt.subplots(figsize=(10, 5.5))
    plc = [path_sum[j] / path_n[j] for j in range(N_PLACEBO) if j in path_n]
    if plc:
        plc = np.array(plc)
        ax.fill_between(x, plc.min(axis=0), plc.max(axis=0), color="#9e9e9e", alpha=0.35, label="placebo range (20 draws)")
        ax.plot(x, plc.mean(axis=0), color="#616161", ls="--", lw=1, label="placebo mean")
    if "real" in path_n:
        ax.plot(x, path_sum["real"] / path_n["real"], color="#c62828", lw=2, label=f"real NEAR (n={path_n['real']:,})")
    ax.axvline(0, color="black", lw=0.6)
    ax.axhline(0, color="black", lw=0.4)
    ax.set_xlabel("minutes from the break (5-min close)")
    ax.set_ylabel("mean move in σ_d (+ = further beyond the level)")
    ax.set_title(f"{'CONFIRMATORY 2024-01..2026-01' if mode == 'confirm' else 'DRY RUN 2021-2023 (seen data)'}: "
                 "NEAR PDH/PDL breaks vs distance-matched placebos")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        raise SystemExit("usage: confirm.py dry|confirm")
    sys.exit(main(sys.argv[1]))
