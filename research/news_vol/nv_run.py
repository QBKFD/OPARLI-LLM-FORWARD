#!/usr/bin/env python3
"""research/news_vol (Study A): the machinery check (1c) and the single run (1d).

    python research/news_vol/nv_run.py machinery   # synthetic bars, real event times; no outcome
    python research/news_vol/nv_run.py run         # bumps ATTEMPTS.json, then reads gold dev once

Both refuse unless PREREG.md is committed and identical to origin/main's copy
(merged before any run). `run` also refuses unless results/machinery.json passed.
Writes aggregates only (results/*.json, coefs.csv): no price, no per-row output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import nv_core  # noqa: E402
import nv_data as D  # noqa: E402
import nv_params as V  # noqa: E402
import nv_synth  # noqa: E402

ROOT = HERE.parents[1]
RESULTS = HERE / "results"
ATTEMPTS = HERE / "ATTEMPTS.json"
PREREG = HERE / "PREREG.md"
REL = PREREG.relative_to(ROOT).as_posix()


def _git(*a) -> str:
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def assert_prereg_merged() -> str:
    if _git("status", "--porcelain", "--", REL).strip():
        raise SystemExit("PREREG.md has uncommitted changes")
    on_main = subprocess.run(["git", "show", f"origin/main:{REL}"], cwd=ROOT, capture_output=True)
    if on_main.returncode != 0 or on_main.stdout != PREREG.read_bytes():
        raise SystemExit("PREREG.md is not on origin/main as it is here: merge it before any run")
    return hashlib.sha256(PREREG.read_bytes()).hexdigest()


def log_attempt(field: str, kind: str, what: str) -> int:
    d = json.loads(ATTEMPTS.read_text())
    d[field] += 1
    d["log"].append({"n": len(d["log"]) + 1, "kind": kind, "date": date.today().isoformat(), "what": what,
                     "by": "owner brief, 2026-11-01 (unattended)"})
    ATTEMPTS.write_text(json.dumps(d, indent=1) + "\n")
    return d[field]


def config(seal: bool) -> nv_core.Config:
    return nv_core.Config(train_start=V.DEV_START, end=V.DEV_END_EXCL, edges=V.FOLD_EDGES, horizons=V.HORIZONS,
                          embargo=V.EMBARGO, seal_end=V.DEV_END_EXCL if seal else None)


def stamp(prereg_sha: str) -> dict:
    return {"git_head": _git("rev-parse", "HEAD").strip(), "prereg_sha256": prereg_sha,
            "date": date.today().isoformat()}


def machinery() -> int:
    sha = assert_prereg_merged()
    ev, info = D.events()
    n = log_attempt("machinery_checks", "machinery_check",
                    "synthetic bars on the open-minute calendar 2015-12-25..2024-01-01, real event times; "
                    "effect x4 for 30 min vs null (PREREG section 7)")
    out = nv_synth.machinery_check(ev, config(seal=False))
    out.update({"attempt": n, "calendar": info, **stamp(sha)})
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "machinery.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps({"pass": out["pass"], "checks": out["checks"], "Q1_ratio_to_truth": out["Q1_ratio_to_truth"]},
                     indent=1))
    return 0 if out["pass"] else 1


def run() -> int:
    sha = assert_prereg_merged()
    mc = RESULTS / "machinery.json"
    if not mc.exists() or not json.loads(mc.read_text()).get("pass"):
        raise SystemExit("the machinery check (results/machinery.json) has not passed: no run")
    n = log_attempt("outcome_analyses", "outcome_analysis",
                    "XAUUSD dev 2016-2023: Q1, Q2 (H 240, 60), Q3, BH; verdict by D7 (single run)")
    m, bar_checks = D.bars()
    ev, info = D.events()
    res = nv_core.run(m, ev, config(seal=True))
    a = nv_core.analyse(res, V.PRIMARY_H, V.BOOT_B, V.SEED, V.NW_LAG, V.BH_Q)
    f60 = res.rows[60]
    from nv import boot_diff, month_codes
    a["Q1_H60_reported"] = boot_diff(f60["y"].to_numpy(), f60["rel"].to_numpy(), month_codes(f60.index),
                                     V.BOOT_B, V.SEED)
    per_fold = {}
    for H, g in res.rows.items():
        g = g.assign(d=g["ql_aug"] - g["ql_har"])
        per_fold[str(H)] = {str(k): {"n": int(len(x)), "release": int(x["rel"].sum()), "mean_d": float(x["d"].mean()),
                                     "qlike_har": float(x["ql_har"].mean()), "qlike_aug": float(x["ql_aug"].mean()),
                                     "mean_y_rel": float(x.loc[x["rel"], "y"].mean()),
                                     "mean_y_other": float(x.loc[~x["rel"], "y"].mean())}
                            for k, x in g.groupby("fold")}
        a.setdefault("by_type", {})[str(H)] = {
            t: {"n": int(s.sum()), "mean_y": float(g.loc[s, "y"].mean()), "mean_d": float(g.loc[s, "d"].mean())}
            for t, s in (("data_only", g["d_data"] & ~g["d_fomc"]), ("fomc_only", g["d_fomc"] & ~g["d_data"]),
                         ("both", g["d_data"] & g["d_fomc"]), ("none", ~g["rel"]))}
    out = {"label": "Study A, single run", "attempt": n, **stamp(sha), "bars": bar_checks, "calendar": info,
           "rows": {str(H): int(len(g)) for H, g in res.rows.items()}, "fold_counts": res.counts,
           "per_fold": per_fold, **a}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "run.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    pd.DataFrame(res.coefs).to_csv(RESULTS / "coefs.csv", index=False, float_format="%.6g")
    print(json.dumps({k: out[k] for k in ("verdict", "verdict_why", "BH", "Q1", "Q2", "Q3")}, indent=1, default=float))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("machinery", "run"))
    a = ap.parse_args(argv)
    return machinery() if a.what == "machinery" else run()


if __name__ == "__main__":
    sys.exit(main())
