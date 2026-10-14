#!/usr/bin/env python3
"""Counts-only check of the A/A failure (no outcomes): how many distinct real days
(bootstrap clusters) draw placebos from the same calendar day?"""
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
days = lv.attach_levels(lv.day_table(ts, o, h, l, c))
mk = lv.Market(ts.asi8, o, h, l, c, f, L)
dd = pd.DatetimeIndex(days.date)
test_idx = list(days.index[(~days.excluded) & (dd >= dg.START) & (dd <= dg.END)])
units, _ = cf.build_units(days, test_idx, mk)
by = {u["id"]: u for u in units}
out = {}
for cls, lo, hi in (("NEAR", 0.1, 0.5), ("FAR", 0.5, np.inf)):
    rows = [(u["pday"], by[u["parent"]]["cluster"]) for u in units
            if u["is_placebo"] and lo <= u["dist"] <= hi and not (cls == "FAR" and u["dist"] <= 0.5)]
    df = pd.DataFrame(rows, columns=["pday", "cluster"])
    per_day = df.groupby("pday").cluster.nunique()
    out[cls] = {"placebo_units": len(df), "distinct_placebo_days": int(per_day.size),
                "mean_real_days_per_placebo_day": float(per_day.mean()),
                "median_real_days_per_placebo_day": float(per_day.median()),
                "max_real_days_per_placebo_day": int(per_day.max()),
                "share_placebo_units_on_days_shared_by_>=5_real_days": float(df.pday.map(per_day).ge(5).mean())}
print(json.dumps(out, indent=1))
(dg.OUT / "bias_check.json").write_text(json.dumps(out, indent=1))
