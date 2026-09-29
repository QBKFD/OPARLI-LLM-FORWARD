#!/usr/bin/env python3
"""
EXPLORATORY (not pre-registered, not confirmatory). REPORT.md section 6.

Where is the clock that is left after the round-3 fix?
X1  mean S by UTC hour (first training window, DEV and CONF)
X2  Jensen check: eta² of the target and of mRV_1h on the level scale (mean of
    r_ds²) vs the log scale. Only the log shows a clock if the profile
    normalises the mean correctly and the rest is a distribution-shape effect.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_r3 as r3  # noqa: E402

r1 = r3.r1
out = {}
for name, cfg in r3.PERIODS.items():
    m = r3.load_dev() if name == "DEV" else r3.load_conf()
    r, flag = r1.minute_returns(m)
    bucket = r3.minute_buckets(m.index)
    bars = r1.hourly_bars(m, flag)
    k, train_end, ts, te = next(r3.folds(cfg["edges"]))
    prof = r3.fit_profile(m.index, r, bucket, cfg["start"], train_end)
    ds = r3.ds_features(bars.index, m.index, r, bucket, prof, cfg["end"])
    tr = ds[(ds.index >= cfg["start"]) & (ds.index <= train_end)]
    out[f"X1_{name}_mean_S_by_hour"] = tr["S"].groupby(tr.index.hour).mean().round(3).to_dict()
    lev = {c: np.exp(tr[c]) for c in ["m1", "y4_ds", "y24_ds"]}
    out[f"X2_{name}_eta2"] = {**{f"log_{c}": round(r3.eta2(tr[c]), 4) for c in ["m1", "y4_ds", "y24_ds", "S"]},
                              **{f"level_{c}": round(r3.eta2(v), 4) for c, v in lev.items()}}
(r3.OUT / "exploratory_r3.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
