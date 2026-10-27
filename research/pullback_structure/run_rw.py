#!/usr/bin/env python3
"""PREREG §5 / §11.3 / §12: the random-walk machinery check, before dev. No real outcome
is read: the pipeline runs on simulated 1-min paths over the dev period's real
timestamps, with per-minute sd = the dev HAR sigma_240 / sqrt(240) (data.per_minute_sigma).
Writes results/rw.json; exits 4 if the gate fails (the study then stops).

    python research/pullback_structure/run_rw.py [--reps N]

--reps is for a smoke run only; the gate needs P.RW_REPS (200) and refuses fewer."""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import data  # noqa: E402
import params as P  # noqa: E402
import pullback as pb  # noqa: E402

OUT = HERE / "results" / "rw.json"
COUNTS = HERE / "results" / "counts_a2.json"


def sim_candles(cd: pd.DataFrame, o, h, lo, c) -> pd.DataFrame:
    """The simulated bars aggregated over the real candles' row ranges."""
    i0, i1 = cd["i0"].to_numpy(), cd["i1"].to_numpy()
    idx = np.ravel(np.column_stack([i0, i1]))
    hh = np.append(h, h[-1])
    ll = np.append(lo, lo[-1])
    out = cd[["start", "end", "i0", "i1"]].copy()
    out["open"], out["close"] = o[i0], c[i1 - 1]
    out["high"] = np.maximum.reduceat(hh, idx)[::2]
    out["low"] = np.minimum.reduceat(ll, idx)[::2]
    return out


def main() -> int:
    reps = int(sys.argv[sys.argv.index("--reps") + 1]) if "--reps" in sys.argv else P.RW_REPS
    cnt = json.loads(COUNTS.read_text())
    if not cnt["gate_p30"]["pass"]:
        raise SystemExit("step 2 did not pass: the random walk is not run")
    n_dev = cnt["counts"]["pooled"]["q1_turns"]
    cuts = tuple(cnt["depth_tercile_cuts_sigma"])
    d = common.prepare(verify_cache=False)
    cd, mt = d["cd"], d["mt"]
    smin = data.per_minute_sigma(d["tbl"], d["m"].index)
    close0 = float(d["m"]["close"].iloc[0])
    pooled = {"q1": [], "q2": [], "q3": []}
    cover = {"q1": [], "q3": []}
    per_rep = []
    t0 = time.time()
    for r in range(reps):
        o, h, lo, c = pb.simulate_minutes(close0, smin, np.random.default_rng((P.BOOT_SEED, r)))
        cs = sim_candles(cd, o, h, lo, c)
        sig = cs["close"].to_numpy() * d["ret_end"]               # sigma_px of the simulated price (D12)
        rec, _, _ = common.run(cs, mt, h, lo, sig)
        t = common.tables(rec)
        pooled["q1"].append(t["q1"][["x", "depth"]])
        pooled["q2"].append(t["q2"]["is_b"])
        pooled["q3"].append(t["q3"]["x"])
        row = {"rep": r, "q1_n": int(len(t["q1"])), "q2_n": int(len(t["q2"])), "q3_n": int(len(t["q3"]))}
        for q in ("q1", "q3"):
            g = t[q]
            month = g["q1_month" if q == "q1" else "q3_month"].to_numpy()
            if len(g) >= P.MIN_EVENTS:
                e = pb.excess_test(month, g["x"].to_numpy(), P.BOOT_B, P.BOOT_SEED + 1000 + r)
                cover[q].append(e["ci95"][0] <= 0 <= e["ci95"][1])
                row[f"{q}_excess"] = e["excess"]
        per_rep.append(row)
        if r % 20 == 0:
            print(f"rep {r}: q1 n {row['q1_n']}, {time.time() - t0:.0f}s", flush=True)
    q1 = pd.concat(pooled["q1"])
    q2 = pd.concat(pooled["q2"])
    q3 = pd.concat(pooled["q3"])
    terc = pb.tercile_of(q1["depth"].to_numpy(), cuts)
    cov1 = float(np.mean(cover["q1"])) if cover["q1"] else float("nan")
    gate = {"reps": reps, "required_reps": P.RW_REPS,
            "pooled_q1_n": int(len(q1)), "required_n": P.RW_MIN_FACTOR * n_dev,
            "pooled_q1_excess": float(q1["x"].mean()), "tolerance": P.RW_TOL,
            "q1_coverage_of_0": cov1, "q1_reps_tested": len(cover["q1"]), "coverage_band": list(P.RW_COVERAGE)}
    gate["pass"] = bool(reps >= P.RW_REPS and len(q1) >= gate["required_n"] and abs(gate["pooled_q1_excess"]) <= P.RW_TOL
                        and len(cover["q1"]) == reps and P.RW_COVERAGE[0] <= cov1 <= P.RW_COVERAGE[1])
    out = {"what": "pullback_structure §5/§11.3: random-walk machinery check on the dev timestamps; no real outcome",
           "checks": d["checks"], "model": d["model"], "dev_turns": n_dev, "gate": gate,
           "q2_null_share_B": float(q2.mean()), "q2_pooled_n": int(len(q2)),
           "q1_terciles_descriptive": {common.TERCILE_NAMES[j]: {"n": int((terc == j).sum()),
                                                                "excess": float(q1["x"].to_numpy()[terc == j].mean())}
                                       for j in range(3)},
           "q3_descriptive": {"pooled_n": int(len(q3)), "pooled_excess": float(q3.mean()) if len(q3) else None,
                              "coverage_of_0": float(np.mean(cover["q3"])) if cover["q3"] else None,
                              "reps_tested": len(cover["q3"])},
           "per_rep": per_rep, "seconds": round(time.time() - t0)}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("gate", "q2_null_share_B", "q1_terciles_descriptive", "q3_descriptive")}, indent=1))
    return 0 if gate["pass"] else 4


if __name__ == "__main__":
    sys.exit(main())
