#!/usr/bin/env python3
"""
EXPLORATORY (not pre-registered, not confirmatory). REPORT.md section 7.

Placebo levels that fail eligibility (the wrong side of the open) are redrawn,
so when a real level sits close to the open its placebos end up, on average,
FARTHER from the open. Distance from the open could drive event behaviour on
its own (far levels are only reached on strong-trend days).

E1  distance of real vs paired placebo levels from the day open (in σ_d at
    the day start), per family and event type
E2  distance-stratified effect: real vs placebo within quintiles of the real
    levels' distance (CONF), combined with the real-event counts as weights
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import levels as lv  # noqa: E402
import run_levels as rl  # noqa: E402


def main() -> int:
    m, _ = rl.load_minutes()
    ts = m.index
    o, h, l, c = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
    days = lv.attach_levels(lv.day_table(ts, o, h, l, c))
    f, Lmin = lv.sigma_factor(m, range(2007, 2024))
    mk = lv.Market(ts.asi8, o, h, l, c, f, Lmin)
    del m
    keep = days.index[pd.DatetimeIndex(days.date).year >= 2007]
    units, _ = lv.build_units(days.loc[keep], mk, families=("DAILY", "WEEKLY"))
    dist = {}
    for u in units:
        di = u["days"][0]
        i0 = int(days.at[di, "i0"])
        sig0 = abs(u["level"]) * mk.f[i0]
        dist[u["id"]] = u["side"] * (u["level"] - days.at[di, "open"]) / sig0
    recs = [r for r in lv.evaluate_levels(units, mk, days) if r["etype"] != "TOUCH" or r["fill"] == lv.FILL]
    ev = pd.DataFrame(recs)
    ev["year"] = pd.to_datetime(ev.period_date).dt.year
    ev = ev[(ev.year >= 2007) & (ev.year <= 2020)]
    ev["dist"] = ev.unit.map(dist)
    ev["tgt"] = (ev[f"o_{lv.B_MAIN}_{lv.H_MAIN}"] == 1).astype(float)
    out = {}
    for (fam, hyp), g in ev.groupby(["family", "hyp"]):
        real = g[~g.is_placebo]
        plc = g[g.is_placebo & g.parent.isin(real.unit)]
        e1 = {"real_dist_median": float(real.dist.median()), "placebo_dist_median": float(plc.dist.median()),
              "real_dist_mean": float(real.dist.mean()), "placebo_dist_mean": float(plc.dist.mean())}
        qs = np.quantile(real.dist, [0.2, 0.4, 0.6, 0.8])
        rb, pb = np.digitize(real.dist, qs), np.digitize(plc.dist, qs)
        cells, num, den = [], 0.0, 0
        for k in range(5):
            r_k, p_k = real.tgt[rb == k], plc.tgt[pb == k]
            if len(r_k) and len(p_k):
                eff = r_k.mean() - p_k.mean()
                cells.append({"bin": k, "n_real": int(len(r_k)), "n_placebo": int(len(p_k)),
                              "real": float(r_k.mean()), "placebo": float(p_k.mean()), "effect": float(eff)})
                num += eff * len(r_k)
                den += len(r_k)
        raw = real.tgt.mean() - plc.tgt.mean()
        out[f"{fam}:{hyp}"] = {**e1, "raw_effect": float(raw), "stratified_effect": float(num / den),
                               "bins": cells}
        print(f"{fam} {hyp}: dist real {e1['real_dist_mean']:.2f} vs placebo {e1['placebo_dist_mean']:.2f} σ; "
              f"raw {raw:+.4f} -> stratified {num / den:+.4f}")
    (lv.OUT / "exploratory_distance.json").write_text(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
