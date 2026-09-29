#!/usr/bin/env python3
"""
CI correction for research/levels_confirm (plan addendum 2). Recomputes the
2024-01-02 .. 2026-01-30 events with the UNCHANGED confirm pipeline, solely to
recompute the NEAR BREAK effect's 95 % CI with the calendar block bootstrap
(calendar = the 538 test days, block 120, 5,000 draws). The point estimate is
recomputed only as a check. Nothing else is computed or kept: run_unit
necessarily produces TOUCH/SWEEP records as well, and they are discarded
unread.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diag as dg  # noqa: E402

cf, lv = dg.cf, dg.lv


def main() -> int:
    cfg = cf.MODES["confirm"]
    fit_end = pd.Timestamp(cfg["fit_end"], tz="UTC")
    old = cf.pre2024()
    m_fit = old[(old.index >= pd.Timestamp("2007-01-01", tz="UTC")) & (old.index < fit_end)]
    del old
    m = cf.load_study("confirm")                     # asserts: nothing after 2026-01-30, global seal
    f, L = cf.sigma_single_fit(m_fit, m, fit_end)
    ts = m.index
    o, h, l, c = (m[k].to_numpy() for k in ("open", "high", "low", "close"))
    days = lv.attach_levels(lv.day_table(ts, o, h, l, c))
    mk = lv.Market(ts.asi8, o, h, l, c, f, L)
    del m
    dd = pd.DatetimeIndex(days.date)
    test_idx = list(days.index[(~days.excluded) & (dd >= cfg["test_start"]) & (dd <= cfg["test_end"])])
    units, _ = cf.build_units(days, test_idx, mk)    # same seed -> identical units to the confirmatory run
    dist = {u["id"]: u["dist"] for u in units}
    own = {u["id"]: u["days"][0] for u in units}
    recs = []
    for u in units:
        recs.extend(r for r in lv.run_unit(u, mk, days) if r["etype"] == "BREAK")   # others discarded unread
    ev = pd.DataFrame(recs)
    ev["dist"] = ev.unit.map(dist)
    near = ev[(ev.dist >= cf.NEAR[0]) & (ev.dist <= cf.NEAR[1])].copy()
    near["cluster"] = near.unit.map(own)
    near["tgt"] = (near[f"o_{lv.B_MAIN}_{lv.H_MAIN}"] == 1).astype(float)
    cal = pd.Index(test_idx)
    W = dg.block_W(len(cal), dg.PRIMARY_BLOCK, n_draws=5000, seed=2024)
    new = dg.cell(near, "tgt", cal, W)
    prev = json.loads((dg.ROOT / "research" / "levels_confirm" / "results" / "confirm_results.json").read_text())["main_near"]
    out = {"test_days": len(cal), "block_length": dg.PRIMARY_BLOCK, "draws": 5000,
           "point_estimate_recomputed": new["effect"], "point_estimate_original": prev["effect"],
           "point_estimate_identical": bool(abs(new["effect"] - prev["effect"]) < 1e-12),
           "ci_new": [new["ci_lo"], new["ci_hi"]], "ci_original_day_bootstrap": [prev["ci_lo"], prev["ci_hi"]],
           "n_real": new["n_real"], "n_placebo": new["n_placebo"]}
    (dg.OUT / "confirm_ci_correction.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
