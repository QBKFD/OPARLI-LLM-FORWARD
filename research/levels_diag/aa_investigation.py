#!/usr/bin/env python3
"""
Investigation of the A/A failure (NOT Q1-Q5; placebo-only A/A data, 2007-2023).
(1) Is the bootstrap SE smaller than the spread of A/A estimates across
    repetitions? (block 120)
(2) Does a group-balanced estimator remove the +0.41 pp A/A bias?
    balanced = mean over groups (pseudo-real had a break) of
    [pseudo outcome - mean outcome of that group's other placebo breaks],
    vs the pooled estimator used so far (all paired placebo breaks pooled).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag as dg  # noqa: E402

cf, lv = dg.cf, dg.lv
m = dg.load()
f, L = dg.sigma_yearly(m)
ts = m.index
o, h, l, c = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
days = dg.day_context(lv.attach_levels(lv.day_table(ts, o, h, l, c)), c)
mk = lv.Market(ts.asi8, o, h, l, c, f, L)
del m
dd = pd.DatetimeIndex(days.date)
test_idx = list(days.index[(~days.excluded) & (dd >= dg.START) & (dd <= dg.END)])
units, _ = cf.build_units(days, test_idx, mk)
ev = dg.evaluate(units, mk, days, "corrected")
cal = pd.Index(test_idx)
W = dg.block_W(len(cal), 120, seed=1120)
uid = {(u["parent"], u["j"]): u["id"] for u in units if u["is_placebo"]}
maxid = max(u["id"] for u in units if not u["is_placebo"]) + 1
out = {}
for cls in ("NEAR", "FAR"):
    br = ev[(ev.etype == "BREAK") & (ev.cls == cls) & ev.is_placebo].copy()
    rng = np.random.default_rng(4242)
    pooled, balanced, ses = [], [], []
    for _ in range(dg.N_AA):
        k = rng.integers(0, cf.N_PLACEBO, size=maxid)
        par = br.parent.to_numpy()
        pseudo = br.j.to_numpy() == k[par]
        new_parent = np.array([uid.get((p, k[p]), -1) for p in par])
        sub = br.assign(is_placebo=~pseudo, parent=np.where(pseudo, br.unit.to_numpy(), new_parent))
        pt, dr = cf.effect_draws(sub, "tgt", cal, W)
        s = cf.summarize(pt, dr)
        pooled.append(s["effect"])
        ses.append(s["se"])
        g = br.assign(pseudo=pseudo)
        ps = g[g.pseudo].groupby("parent").tgt.mean()
        ot = g[~g.pseudo].groupby("parent").tgt.mean()
        both = ps.index.intersection(ot.index)
        balanced.append(float((ps[both] - ot[both]).mean()))
    pooled, balanced, ses = map(np.array, (pooled, balanced, ses))
    out[cls] = {"pooled_mean": float(pooled.mean()), "pooled_sd_across_reps": float(pooled.std()),
                "mean_bootstrap_se": float(ses.mean()), "se_over_sd": float(ses.mean() / pooled.std()),
                "balanced_mean": float(balanced.mean()), "balanced_sd_across_reps": float(balanced.std())}
    print(cls, {k: round(v, 5) for k, v in out[cls].items()})
(dg.OUT / "aa_investigation.json").write_text(json.dumps(out, indent=1))
