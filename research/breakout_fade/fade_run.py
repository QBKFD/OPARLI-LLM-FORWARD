#!/usr/bin/env python3
"""research/breakout_fade, PREREG §3, in its order:

    python research/breakout_fade/fade_run.py counts   # B18: breaks and short-day exclusions, no outcome
    python research/breakout_fade/fade_run.py rw       # B15: the random-walk check (stops the study on a fail)
    python research/breakout_fade/fade_run.py dev      # step 4: H1, H2, BH, the verdict, once

Each step refuses to start until the one before it has passed; `dev` refuses
unless ATTEMPTS.json has outcome_analyses 0, and increments it before any
outcome is read. Results go to results/*.json, aggregates only.
"""
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fade  # noqa: E402  (puts pullback_structure on the path)
import fade_data as D  # noqa: E402
import fade_params as F  # noqa: E402
from fade import P0, pb  # noqa: E402

import run_rw as ps_rw  # noqa: E402  (pullback_structure/run_rw.py: sim_candles, D12)

RES = HERE / "results"
ATTEMPTS = HERE / "ATTEMPTS.json"


def check_prereg() -> None:
    """Every step runs against the pinned pre-registration only."""
    import hashlib
    got = hashlib.sha256((HERE / "PREREG.md").read_bytes()).hexdigest()
    if got != F.PREREG_SHA256:
        raise SystemExit(f"PREREG.md differs from the pre-registration ({F.PREREG_REF}; "
                         f"sha256 {got[:12]} != {F.PREREG_SHA256[:12]}): nothing runs")


def prepare() -> dict:
    check_prereg()
    m, checks = D.load()
    cd, info = pb.candles_hourly(m, P0.MIN_SHARE)
    tbl, model = D.sigma_table(m)
    ends = pd.DatetimeIndex(cd["end"])
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    checks = {**checks, "prereg": F.PREREG_REF, "prereg_sha256": F.PREREG_SHA256, "git_head": head}
    return {"m": m, "cd": cd, "candles": info, "tbl": tbl, "checks": checks,
            "model": {"name": model.name, "sha256": model.sha256},
            "sig": D.at_labels(tbl, f"sigma_px_{F.SIGMA_H}", ends), "ret": D.at_labels(tbl, f"sigma_ret_{F.SIGMA_H}", ends),
            "short": fade.short_dates(m, F.SHORT_DAY_ROWS),
            "mt": m.index.as_unit("ns").asi8, "mh": m["high"].to_numpy(), "ml": m["low"].to_numpy()}


def collect(d, cd=None, mh=None, ml=None, sig=None):
    return fade.collect(d["cd"] if cd is None else cd, d["mt"], d["mh"] if mh is None else mh,
                        d["ml"] if ml is None else ml, d["sig"] if sig is None else sig, d["short"], F.DEV_START.value)


def read(name):
    p = RES / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def counts() -> int:
    d = prepare()
    df, refused = collect(d)
    keep = df[~df["short"]]
    out = {"what": "breakout_fade B18: Q2 breaks on silver dev 2016-2023; counts only, no outcome",
           "checks": d["checks"], "model": d["model"], "candles": d["candles"], "short_trade_dates": len(d["short"]),
           "breaks": {"bear": int((df["dir"] == "bear").sum()), "bull": int((df["dir"] == "bull").sum()), "all": int(len(df))},
           "excluded_short_day": int(df["short"].sum()), "breaks_used": int(len(keep)),
           "refused_starts": refused, "gate": {"min_events": F.MIN_EVENTS, "pass": bool(len(keep) >= F.MIN_EVENTS)}}
    import math
    z = 1.959964 + 0.841621
    out["power"] = {"n": int(len(keep)), "h1_mde_at_0.344": round(z * math.sqrt(0.344 * 0.656 / max(1, len(keep))), 4),
                    "h2_mde_bound": round(z * 0.5 / math.sqrt(max(1, len(keep))), 4)}
    RES.mkdir(exist_ok=True)
    (RES / "counts.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("candles", "short_trade_dates", "breaks", "excluded_short_day", "breaks_used", "gate", "power")}, indent=1))
    return 0 if out["gate"]["pass"] else 3


def rw() -> int:
    c = read("counts")
    if not c or not c["gate"]["pass"]:
        raise SystemExit("B18 counts have not passed: the random walk is not run")
    d = prepare()
    smin = D.per_minute_sigma(d["tbl"], d["m"].index)
    close0 = float(d["m"]["close"].iloc[0])
    h1_all, h2_all, per = [], [], []
    t0 = time.time()
    for r in range(F.RW_REPS):
        o, h, lo, cc = pb.simulate_minutes(close0, smin, np.random.default_rng((F.BOOT_SEED, r)))
        cs = ps_rw.sim_candles(d["cd"], o, h, lo, cc)
        df, _ = collect(d, cs, h, lo, cs["close"].to_numpy() * d["ret"])
        df = df[~df["short"]]
        res = df[df["h2"].isin(["hit", "stop"])]
        h1_all.append(df.assign(rep=r))
        h2_all.append(res.assign(rep=r))
        per.append({"rep": r, "breaks": int(len(df)), "h2_resolved": int(len(res))})
        if r % 40 == 0:
            print(f"rep {r}: {len(df)} breaks, {time.time() - t0:.0f}s", flush=True)
    H1, H2 = pd.concat(h1_all), pd.concat(h2_all)
    H2 = H2.assign(q=fade.quartile(H2["target_sigma"].to_numpy(), F.H2_EDGES), x=H2["hit"] - H2["p"])
    null = float(H1["is_b"].mean())
    S_all, N_all = fade.strata_sums(H2["q"].to_numpy(), H2["x"].to_numpy())
    rw_means = S_all / N_all
    cov1, cov2, stats2 = [], [], []
    for r in range(F.RW_REPS):
        g1, g2 = H1[H1["rep"] == r], H2[H2["rep"] == r]
        if len(g1) >= F.MIN_EVENTS:
            t = fade.h1_test(g1["month"].to_numpy(), g1["is_b"].to_numpy(), null, F.BOOT_B, F.BOOT_SEED + 1000 + r)
            cov1.append(t["ci95"][0] <= 0 <= t["ci95"][1])
        if len(g2) >= F.MIN_EVENTS:
            s_r, n_r = fade.strata_sums(g2["q"].to_numpy(), g2["x"].to_numpy())
            loo = (S_all - s_r) / (N_all - n_r)                       # A1 §6.2: the other 199 paths
            t = fade.h2_stratified(g2["month"].to_numpy(), g2["q"].to_numpy(), g2["x"].to_numpy(), loo,
                                   F.BOOT_B, F.BOOT_SEED + 3000 + r)
            stats2.append(t["h2"])
            cov2.append(t["ci95"][0] <= 0 <= t["ci95"][1])
    h2_mean = float(np.nanmean(stats2)) if stats2 else float("nan")
    c1 = float(np.mean(cov1)) if cov1 else float("nan")
    c2 = float(np.mean(cov2)) if cov2 else float("nan")
    lo_c, hi_c = F.RW_COVERAGE
    gate = {"reps": F.RW_REPS, "h2_statistic": "A1 §6.1 stratified (silver - random walk), path against the other 199",
            "h2_mean_path_statistic": h2_mean, "tolerance": F.RW_TOL, "h2_coverage_of_0": c2, "h2_reps_tested": len(cov2),
            "h1_coverage_of_0": c1, "h1_reps_tested": len(cov1), "coverage_band": [lo_c, hi_c],
            "h2_unstratified_pooled_excess_descriptive": float(H2["x"].mean())}
    gate["pass"] = bool(abs(h2_mean) <= F.RW_TOL and len(cov1) == len(cov2) == F.RW_REPS
                        and lo_c <= c1 <= hi_c and lo_c <= c2 <= hi_c)
    out = {"what": "breakout_fade B15: random-walk check on the silver dev timestamps; no real outcome",
           "checks": d["checks"], "model": d["model"], "gate": gate, "h1_null_share_B": null, "h1_pooled_n": int(len(H1)),
           "h2_edges_sigma": list(F.H2_EDGES), "h2_rw_quartile_means": [float(v) for v in rw_means],
           "h2_rw_quartile_n": [int(v) for v in N_all],
           "descriptive": {"share_B_by_W": {str(w): float((H1[f"bc_w{w}"] == "B").mean()) for w in P0.W_ALT},
                           "move5_sigma_mean": float(H1["move5"].dropna().mean()),
                           "move30_sigma_mean": float(H1["move30"].dropna().mean())},
           "per_rep": per, "seconds": round(time.time() - t0)}
    (RES / "rw.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"gate": gate, "h1_null_share_B": null, "h2_rw_quartile_means": out["h2_rw_quartile_means"],
                      "descriptive": out["descriptive"]}, indent=1))
    return 0 if gate["pass"] else 4


def bump(what: str) -> int:
    a = json.loads(ATTEMPTS.read_text())
    a["outcome_analyses"] += 1
    a["log"].append({"n": len(a["log"]) + 1, "kind": "outcome_analysis", "date": date.today().isoformat(),
                     "what": what, "by": "owner, 2026-10-28 (PREREG #54/#55)"})
    ATTEMPTS.write_text(json.dumps(a, indent=1) + "\n")
    return a["outcome_analyses"]


def dev() -> int:
    if json.loads(ATTEMPTS.read_text())["outcome_analyses"] != 0:
        raise SystemExit("outcome_analyses is not 0: dev has been run; it is not rerun")
    c, w = read("counts"), read("rw")
    if not c or not c["gate"]["pass"] or not w or not w["gate"]["pass"]:
        raise SystemExit("B18 counts or the B15 random-walk check have not passed: dev is not run")
    d = prepare()
    n = bump("silver dev 2016-2023: H1 (B share vs random walk), H2 (Amendment 1: stratified, vs random walk), BH; verdict by B13")
    assert n == 1
    # ---- from here on, outcomes are read
    df, refused = collect(d)
    use = df[~df["short"]]
    res = use[use["h2"].isin(["hit", "stop"])]
    null = w["h1_null_share_B"]
    h1 = fade.h1_test(use["month"].to_numpy(), use["is_b"].to_numpy(), null, F.BOOT_B, F.BOOT_SEED)
    if w.get("h2_edges_sigma") != list(F.H2_EDGES):
        raise SystemExit("rw.json was not made with Amendment 1's quartile edges: rerun rw")
    q = fade.quartile(res["target_sigma"].to_numpy(), F.H2_EDGES)
    h2 = fade.h2_stratified(res["month"].to_numpy(), q, (res["hit"] - res["p"]).to_numpy(float),
                            np.asarray(w["h2_rw_quartile_means"]), F.BOOT_B, F.BOOT_SEED + 10)
    h2_unstrat = fade.h2_test(res["month"].to_numpy(), res["hit"].to_numpy(float), res["p"].to_numpy(), F.BOOT_B, F.BOOT_SEED + 20)
    bh = pb.bh({"H1": h1["p_one_sided"], "H2": h2["p_one_sided"]}, F.BH_Q)
    v = fade.verdict(h1, bh, F.MIN_EVENTS)
    be = fade.break_even_sigma(res["hit"].to_numpy(float), res["target_sigma"].to_numpy())
    counts_ = {"breaks": int(len(df)), "excluded_short_day": int(df["short"].sum()), "used": int(len(use)),
               "B": int(use["is_b"].sum()), "C": int((~use["is_b"]).sum()),
               "h2_resolved": int(len(res)), "h2_unresolved": int((use["h2"] == "unresolved").sum()),
               "h2_truncated": int((use["h2"] == "truncated").sum())}
    desc = {"by_direction": {k: {"n": int(len(g)), "share_B": float(g["is_b"].mean()),
                                 "h2_excess": float((g.loc[g["h2"].isin(["hit", "stop"]), "hit"] - g.loc[g["h2"].isin(["hit", "stop"]), "p"]).mean())}
                             for k, g in use.groupby("dir")},
            "share_B_by_W": {str(x): {"silver": float((use[f"bc_w{x}"] == "B").mean()),
                                      "random_walk": w["descriptive"]["share_B_by_W"][str(x)]} for x in P0.W_ALT},
            "move5_sigma_mean": {"silver": float(use["move5"].dropna().mean()), "random_walk": w["descriptive"]["move5_sigma_mean"]},
            "move30_sigma_mean": {"silver": float(use["move30"].dropna().mean()), "random_walk": w["descriptive"]["move30_sigma_mean"]}}
    out = {"what": "breakout_fade dev (silver 2016-2023), outcome analysis 1; aggregates only", "attempt": n,
           "checks": d["checks"], "model": d["model"], "verdict_h1": v, "h1": h1, "h2": h2, "bh": bh,
           "break_even_spread_sigma": be, "counts": counts_,
           "descriptive": {**desc, "h2_unstratified_analytic_excess": h2_unstrat}, "refused_starts": refused}
    (RES / "dev.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({k: out[k] for k in ("verdict_h1", "h1", "h2", "bh", "break_even_spread_sigma", "counts")}, indent=1))
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    sys.exit({"counts": counts, "rw": rw, "dev": dev}[cmd]())
