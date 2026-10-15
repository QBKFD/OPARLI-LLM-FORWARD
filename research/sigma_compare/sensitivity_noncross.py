#!/usr/bin/env python3
"""
PREREG AMENDMENT (a), post hoc: fit HAR (β, smearing) and choose the EWMA's
lambda on non-crossing training rows only, then rerun the CONF verdict with the
pre-registered rule. DEV is reported alongside and is not part of the verdict.

The original fit used every valid training row, crossing and non-crossing
(PREREG choice 6). Everything else is unchanged: run_sigma.run_period with
noncross_only=True.

    .venv/bin/python research/sigma_compare/sensitivity_noncross.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_sigma as rs  # noqa: E402

OUT = HERE / "results"


def summary(rows: dict) -> dict:
    out = {}
    for H in rs.HORIZONS:
        g = rows[H][~rows[H].crossing]
        out[f"H{H}"] = {"n": len(g),
                        "qlike": {c: float(np.mean(rs.qlike(g.rv, g[f"h_{c}"]))) for c in rs.CANDS},
                        "dm": {f"{x}|a": rs.dm(g, x, "a") for x in ("b", "c")}}
    return out


def main() -> int:
    orig = json.loads((OUT / "results_sigma.json").read_text())
    res_all = {}
    for name, cfg in rs.PERIODS.items():
        rs.log(f"=== {name} (non-crossing training rows only) ===")
        res = rs.run_period(name, cfg, rs.load(name), noncross_only=True)
        for H in rs.HORIZONS:                       # the same test rows as the original run
            assert len(res["rows"][H]) == orig[name]["rows"][f"H{H}"]["common"], (name, H)
        res_all[name] = {"summary": summary(res["rows"]), "coefs": res["coefs"]}
        if name == "CONF":
            res_all["verdict"] = rs.verdict(res["rows"])
    res_all["original_verdict"] = {"choice": orig["verdict"]["choice"],
                                   "qlike": orig["verdict"]["qlike"]}
    (OUT / "sensitivity_noncross.json").write_text(json.dumps(res_all, indent=2, default=float))
    v = res_all["verdict"]
    rs.log(f"VERDICT (non-crossing fit): {v['choice_name']} ({v['why']})")
    for k, t in v["tests"].items():
        rs.log(f"  {k}: mean_d={t['mean_d']:.5f} DM={t['stat']:.2f} p_adj={t['p_adj']:.3g} "
               f"significant={t['significant']}")
    for name in rs.PERIODS:
        for H, s in res_all[name]["summary"].items():
            rs.log(f"  {name} {H} n={s['n']} QLIKE " +
                   " ".join(f"{rs.NAMES[c]}={q:.4f}" for c, q in s["qlike"].items()) +
                   f"  DM HAR {s['dm']['b|a']['stat']:.2f}, EWMA {s['dm']['c|a']['stat']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
