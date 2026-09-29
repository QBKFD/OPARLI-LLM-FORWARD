#!/usr/bin/env python3
"""
Level behaviour study pipeline (PREREG.md). Research only.

    /Users/x/Desktop/algo_project/backend/venv/bin/python3 research/levels/run_levels.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import levels as lv  # noqa: E402

r3 = lv.r3
OUT, FIG = lv.OUT, lv.FIG


def load_minutes(start="2006-11-01"):
    cache = lv.r3.CACHE
    if not cache.exists():
        import audit_long
        audit_long.load_raw()
    df = pd.read_parquet(cache).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    df.index = df.index.tz_localize("UTC")
    df = df[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index < lv.SEAL)]
    lv.assert_sealed_ns(df.index.asi8, "1-min data")
    bad = ~((df.high >= df.low) & (df[["open", "high", "low", "close"]] > 0).all(axis=1))
    return df[~bad][["open", "high", "low", "close"]], int(bad.sum())


def period_of(year):
    if 2007 <= year <= 2020:
        return "CONF"
    if 2021 <= year <= 2023:
        return "REPL"
    return None


def main() -> int:
    OUT.mkdir(exist_ok=True)
    FIG.mkdir(exist_ok=True)
    lv.log("Loading 1-min data (< 2024-01-01)")
    m, n_bad = load_minutes()
    ts = m.index
    o, h, l, c = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
    days = lv.attach_levels(lv.day_table(ts, o, h, l, c))
    info = {"minutes": len(m), "bad_rows_dropped": n_bad,
            "days_total": int(len(days)), "days_excluded": int(days.excluded.sum()),
            "days_excluded_2007_2023": int(days[(pd.DatetimeIndex(days.date).year >= 2007)].excluded.sum())}
    lv.log(f"  {info}")

    lv.log("σ unit: annual expanding VolBlock fits")
    factor, Lmin = lv.sigma_factor(m, range(2007, 2024))
    mk = lv.Market(ts.asi8, o, h, l, c, factor, Lmin)
    del m                                              # free the raw frame; arrays live in mk
    days_all = days                                   # 2006 rows only supply levels for early 2007
    keep = days_all.index[pd.DatetimeIndex(days_all.date).year >= 2007]
    lv.log("Building units (real + placebo)")
    units, ustats = lv.build_units(days_all.loc[keep], mk)
    info.update(n_units=len(units), n_real_units=sum(not u["is_placebo"] for u in units), **ustats)
    lv.log(f"  units {len(units):,} (real {info['n_real_units']:,}); placebo draws short: {ustats['placebo_short']}")

    # ---- evaluate every unit through run_unit; aggregate event-study paths on the fly
    parent_has = set()
    path_sum, path_n = {}, {}

    def on_record(rec):
        path = rec.pop("path", None)
        yr = pd.Timestamp(rec["period_date"]).year
        rec["period"] = period_of(yr)
        rec["year"] = yr
        key_ev = (rec["parent"], rec["etype"], rec["fill"])
        if not rec["is_placebo"]:
            parent_has.add(key_ev)
        if path is not None and rec["period"] == "CONF" and (rec["etype"] != "TOUCH" or rec["fill"] == lv.FILL):
            if rec["is_placebo"] and key_ev not in parent_has:
                return
            k = (rec["family"], rec["hyp"], "real" if not rec["is_placebo"] else int(units[rec["unit"]]["j"]))
            path_sum[k] = path_sum.get(k, 0) + path.astype(np.float64)
            path_n[k] = path_n.get(k, 0) + 1

    lv.log("Evaluating events")
    recs, frames = [], []

    def flush():
        if recs:
            f = pd.DataFrame(recs)
            for col in f.columns:
                if f[col].dtype == np.float64 and col not in ("sigma", "pnl", "move60", "move240", "L"):
                    f[col] = f[col].astype(np.float32)
            frames.append(f)
            recs.clear()

    for i, u in enumerate(units):
        for rec in lv.run_unit(u, mk, days_all, with_path=True):
            on_record(rec)
            recs.append(rec)
        if len(recs) >= 50000:
            flush()
        if i % 100000 == 0:
            lv.log(f"  {i:,}/{len(units):,} units")
    flush()
    ev = pd.concat(frames, ignore_index=True)
    del frames
    ev = ev[ev.period.notna()].copy()
    lv.assert_sealed_ns(ev.t_event.to_numpy(), "events")
    lv.assert_sealed_ns((ev.t_event + 240 * lv.MIN).to_numpy() - 1, "outcome windows")
    ev["session"] = pd.cut(pd.to_datetime(ev.t_event, utc=True).dt.hour, [-1, 6, 11, 16, 23],
                           labels=["Asia", "London", "NY", "late"]).astype(str)
    info["events"] = int(len(ev))
    lv.log(f"  events {len(ev):,}")

    results = analyse(ev, units, info, path_sum, path_n)
    (OUT / "results_levels.json").write_text(json.dumps(results, indent=1, default=float))
    lv.log("Done")
    return 0


# =============================================================================
# Analysis
# =============================================================================

SEEDS = {(f, p): 20240102 + 10 * i + j for i, f in enumerate(["DAILY", "WEEKLY", "ROUND10", "ROUND50"])
         for j, p in enumerate(["CONF", "REPL"])}


def main_spec(ev):
    """Main-spec event rows: TOUCH at fill $0.10, BREAK, SWEEP."""
    return ev[(ev.etype != "TOUCH") | (ev.fill == lv.FILL)]


def clusters_for(units, family, period):
    cl = sorted({u["cluster"] for u in units if not u["is_placebo"] and u["family"] == family
                 and period_of(pd.Timestamp(u["period_date"]).year) == period})
    return pd.Index(cl)


def target(ev, b=lv.B_MAIN, h=lv.H_MAIN):
    return (ev[f"o_{b}_{h}"] == 1).astype(float)


def analyse(ev, units, info, path_sum, path_n):
    res = {"info": info}
    ms = main_spec(ev).copy()
    ms["tgt"] = target(ms)
    fams = ["DAILY", "WEEKLY", "ROUND10", "ROUND50"]
    W, CL = {}, {}
    for fam in fams:
        for per in ("CONF", "REPL"):
            CL[(fam, per)] = clusters_for(units, fam, per)
            W[(fam, per)] = lv.boot_weights(len(CL[(fam, per)]), seed=SEEDS[(fam, per)])
    # ---- primary + exploratory round families
    prim = []
    for fam in fams:
        for etype, (hyp, _) in lv.HYP.items():
            sub = ms[(ms.family == fam) & (ms.etype == etype)]
            row = {"family": fam, "hyp": hyp, "etype": etype}
            c = sub[sub.period == "CONF"]
            row.update({f"conf_{k}": v for k, v in lv.paired_effect(c, "tgt", CL[(fam, "CONF")], W[(fam, "CONF")]).items()})
            for era, (y0, y1) in (("era1", lv.PERIODS["ERA1"]), ("era2", lv.PERIODS["ERA2"])):
                e = c[(c.year >= y0) & (c.year <= y1)]
                row[f"{era}_effect"] = lv.paired_effect(e, "tgt", CL[(fam, "CONF")])["effect"]
            rp = sub[sub.period == "REPL"]
            row.update({f"repl_{k}": v for k, v in lv.paired_effect(rp, "tgt", CL[(fam, "REPL")], W[(fam, "REPL")]).items()})
            for per, mv in (("CONF", "move60"), ("CONF", "move240")):
                row[f"conf_{mv}"] = lv.paired_effect(c, mv, CL[(fam, "CONF")])
            prim.append(row)
    P = pd.DataFrame(prim)
    pr = P[P.family.isin(lv.FAMILIES_PRIMARY)].copy()
    adj, _ = lv.bh(pr["conf_p_one_sided"].to_numpy(), 0.05)
    pr["bh_p"] = adj
    pr["supported"] = (pr.bh_p < 0.05) & (pr.conf_effect >= 0.03) & (pr.era1_effect > 0) & (pr.era2_effect > 0)
    pr["replicated"] = (pr.repl_effect > 0) & (pr.repl_p_one_sided < 0.10)
    res["primary"] = pr.drop(columns=["conf_move60", "conf_move240"]).to_dict("records")
    res["moves"] = {f"{r.family}:{r.hyp}": {"move60": r.conf_move60, "move240": r.conf_move240} for r in P.itertuples()}
    res["exploratory_round"] = P[~P.family.isin(lv.FAMILIES_PRIMARY)].drop(columns=["conf_move60", "conf_move240"]).to_dict("records")
    # ---- outcome category shares (CONF, main spec)
    cats = []
    for (fam, hyp, plc), g in ms[ms.period == "CONF"].groupby(["family", "hyp", "is_placebo"]):
        o = g[f"o_{lv.B_MAIN}_{lv.H_MAIN}"]
        cats.append({"family": fam, "hyp": hyp, "placebo": bool(plc), "n": len(g),
                     "target": float((o == 1).mean()), "stop": float((o == 0).mean()), "timeout": float((o == -1).mean())})
    res["categories"] = cats
    # ---- robustness grid (point estimates, CONF)
    grid = []
    conf = ev[ev.period == "CONF"]
    for fam in lv.FAMILIES_PRIMARY:
        for etype, (hyp, _) in lv.HYP.items():
            fills = lv.FILLS if etype == "TOUCH" else (None,)
            for fill in fills:
                sub = conf[(conf.family == fam) & (conf.etype == etype)]
                sub = sub[sub.fill == fill] if etype == "TOUCH" else sub
                for b in lv.BS:
                    for hm in lv.HS:
                        s = sub.assign(t=(sub[f"o_{b}_{hm}"] == 1).astype(float))
                        e = lv.paired_effect(s, "t", CL[(fam, "CONF")])["effect"]
                        grid.append({"family": fam, "hyp": hyp, "fill": fill, "b": b, "h": hm, "effect": e})
    G = pd.DataFrame(grid)
    G.to_csv(OUT / "robustness_grid.csv", index=False, float_format="%.5f")
    res["robustness_share_positive"] = {f"{f}:{hh}": float((g.effect > 0).mean())
                                        for (f, hh), g in G.groupby(["family", "hyp"])}
    # ---- secondary: conditioning + tradability (BH across all cells)
    sec = []
    cm = ms[(ms.period == "CONF") & ms.family.isin(lv.FAMILIES_PRIMARY)].copy()
    real = cm[~cm.is_placebo]
    thr = np.nanquantile(real.L, [1 / 3, 2 / 3])       # CONF-wide terciles of L at the real events
    real_state = {(r.parent, r.etype): (int(np.digitize(r.L, thr)) if np.isfinite(r.L) else np.nan, r.session)
                  for r in real.itertuples()}
    cm["vol_t"] = [real_state.get((p, e), (np.nan, None))[0] for p, e in zip(cm.parent, cm.etype)]
    cm["sess_p"] = [real_state.get((p, e), (np.nan, None))[1] for p, e in zip(cm.parent, cm.etype)]
    for fam in lv.FAMILIES_PRIMARY:
        for etype, (hyp, _) in lv.HYP.items():
            sub = cm[(cm.family == fam) & (cm.etype == etype)]
            Wf, Cf = W[(fam, "CONF")], CL[(fam, "CONF")]
            for dim, col, vals in (("vol_tercile", "vol_t", [0, 1, 2]),
                                   ("session", "sess_p", ["Asia", "London", "NY", "late"]),
                                   ("side", "side", [1, -1])):
                for v in vals:
                    r = lv.paired_effect(sub[sub[col] == v], "tgt", Cf, Wf)
                    sec.append({"kind": "conditioning", "family": fam, "hyp": hyp, "dim": dim, "cell": str(v), **r})
            r = lv.paired_effect(sub, "pnl", Cf, Wf)
            sec.append({"kind": "tradability", "family": fam, "hyp": hyp, "dim": "pnl_$", "cell": "all", **r})
    S = pd.DataFrame(sec)
    ok = S.p_one_sided.notna()
    S.loc[ok, "bh_p"] = lv.bh(S.loc[ok, "p_one_sided"].to_numpy(), 0.05)[0]
    S.to_csv(OUT / "secondary.csv", index=False, float_format="%.5f")
    res["secondary"] = S.to_dict("records")
    # ---- event counts per level / year (main spec)
    cnt = ms.groupby(["year", "name", "etype", "is_placebo"]).size().unstack("is_placebo", fill_value=0)
    cnt.columns = ["real" if not x else "placebo" for x in cnt.columns]
    cnt.reset_index().to_csv(OUT / "event_counts.csv", index=False)
    # ---- figure
    event_study_figure(path_sum, path_n, FIG / "event_study.png", lv.FAMILIES_PRIMARY)
    event_study_figure(path_sum, path_n, FIG / "event_study_round.png", ("ROUND10", "ROUND50"))
    res["path_counts"] = {f"{k[0]}:{k[1]}:{k[2]}": v for k, v in path_n.items() if k[2] == "real"}
    pd.DataFrame(res["primary"]).to_csv(OUT / "primary.csv", index=False, float_format="%.5f")
    pd.DataFrame(res["exploratory_round"]).to_csv(OUT / "exploratory_round.csv", index=False, float_format="%.5f")
    return res


def event_study_figure(path_sum, path_n, path, fams):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    titles = {"H1": "H1 reaction after TOUCH (+ = away)", "H2": "H2 continuation after BREAK (+ = further beyond)",
              "H3": "H3 reversal after SWEEP-RECLAIM (+ = away)"}
    fig, axes = plt.subplots(len(fams), 3, figsize=(16, 4.2 * len(fams)), squeeze=False)
    x = lv.PATH_OFFSETS
    for i, fam in enumerate(fams):
        for jx, hyp in enumerate(("H1", "H2", "H3")):
            ax = axes[i, jx]
            k = (fam, hyp, "real")
            if k not in path_n:
                ax.set_title(f"{fam} {hyp}: no events")
                continue
            plc = [path_sum[(fam, hyp, j)] / path_n[(fam, hyp, j)] for j in range(lv.N_PLACEBO) if (fam, hyp, j) in path_n]
            if plc:
                plc = np.array(plc)
                ax.fill_between(x, plc.min(axis=0), plc.max(axis=0), color="#9e9e9e", alpha=0.35,
                                label="placebo range (20 draws)")
                ax.plot(x, plc.mean(axis=0), color="#616161", lw=1, ls="--", label="placebo mean")
            ax.plot(x, path_sum[k] / path_n[k], color="#c62828", lw=1.8, label=f"real (n={path_n[k]:,})")
            ax.axvline(0, color="black", lw=0.6)
            ax.axhline(0, color="black", lw=0.4)
            ax.set_title(f"{fam}: {titles[hyp]}", fontsize=9)
            ax.set_xlabel("minutes from event")
            ax.set_ylabel("mean signed move (σ_d)")
            ax.legend(fontsize=7, loc="upper left")
    fig.suptitle("Event study, CONFIRMATION 2007–2020: real levels vs placebo levels (same pipeline)")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())
