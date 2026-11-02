#!/usr/bin/env python3
"""research/news_vol_rep: the machinery check and the single run, both markets.

    python research/news_vol_rep/rep_run.py machinery   # synthetic prices on each market's bar times; no outcome
    python research/news_vol_rep/rep_run.py run         # bumps ATTEMPTS.json, then reads both markets' dev once

Both refuse unless PREREG.md is committed and identical to origin/main's copy
(merged before any run). `run` also refuses unless results/machinery.json passed
for every market, and unless EURUSD's calendar check (R3) finds no difference.
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

import rep_core  # noqa: E402
import nv  # noqa: E402
import nv_core  # noqa: E402
import rep_data as D  # noqa: E402
import rep_params as V  # noqa: E402

ROOT = HERE.parents[1]
RESULTS = HERE / "results"
ATTEMPTS = HERE / "ATTEMPTS.json"
PREREG = HERE / "PREREG.md"
REL = PREREG.relative_to(ROOT).as_posix()
BY = "owner brief, 2026-11-02 (R1-R5 approved)"


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
    d["log"].append({"n": len(d["log"]) + 1, "kind": kind, "date": date.today().isoformat(), "what": what, "by": BY})
    ATTEMPTS.write_text(json.dumps(d, indent=1) + "\n")
    return d[field]


def stamp(prereg_sha: str) -> dict:
    return {"git_head": _git("rev-parse", "HEAD").strip(), "prereg_sha256": prereg_sha,
            "date": date.today().isoformat()}


def machinery() -> int:
    sha = assert_prereg_merged()
    ev, info = D.events()
    n = log_attempt("machinery_checks", "machinery_check",
                    "synthetic prices on each market's dev bar times (XAGUSD, EURUSD), real event times; "
                    "effect x4 for 30 min vs null (Study A PREREG section 7, as PREREG section 7 here)")
    out: dict = {"attempt": n, "calendar": info, **stamp(sha), "markets": {}}
    for mk in V.MARKETS:
        idx, checks = D.bar_times(mk)
        r = rep_core.machinery_check(idx, ev, rep_core.config(seal=False))
        r["bars"] = checks
        out["markets"][mk] = r
    out["pass"] = all(r["pass"] for r in out["markets"].values())
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "machinery.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    print(json.dumps({"pass": out["pass"], **{mk: {"checks": r["checks"], "Q1_ratio_to_truth": r["Q1_ratio_to_truth"]}
                                              for mk, r in out["markets"].items()}}, indent=1))
    return 0 if out["pass"] else 1


def _market(mk: str, ev: pd.DataFrame) -> tuple[dict, list]:
    m, bar_checks = D.bars(mk)
    cal = rep_core.calendar_agreement(m.index, V.DEV_END_EXCL, V.HORIZONS)
    if mk == "EURUSD" and any(a["disagree"] for a in cal.values()):
        raise SystemExit(f"R3: EURUSD's calendar and the metals calendar exclude different windows: {cal}")
    res = nv_core.run(m, ev, rep_core.config(seal=True))
    a = nv_core.analyse(res, V.PRIMARY_H, V.BOOT_B, V.SEED, V.NW_LAG, V.BH_Q)
    # analyse() applies BH over this market's 4 tests; R4's family is both markets' 8 (combine()).
    a["BH_within_market_not_used"] = a.pop("BH")
    a.pop("verdict"), a.pop("verdict_why")
    f60 = res.rows[60]
    a["Q1_H60_reported"] = nv.boot_diff(f60["y"].to_numpy(), f60["rel"].to_numpy(), nv.month_codes(f60.index),
                                        V.BOOT_B, V.SEED)
    per_fold, by_type = {}, {}
    for H, g in res.rows.items():
        g = g.assign(d=g["ql_aug"] - g["ql_har"])
        per_fold[str(H)] = {str(k): {"n": int(len(x)), "release": int(x["rel"].sum()), "mean_d": float(x["d"].mean()),
                                     "qlike_har": float(x["ql_har"].mean()), "qlike_aug": float(x["ql_aug"].mean()),
                                     "mean_y_rel": float(x.loc[x["rel"], "y"].mean()),
                                     "mean_y_other": float(x.loc[~x["rel"], "y"].mean())}
                            for k, x in g.groupby("fold")}
        by_type[str(H)] = {
            t: {"n": int(s.sum()), "mean_y": float(g.loc[s, "y"].mean()), "mean_d": float(g.loc[s, "d"].mean())}
            for t, s in (("data_only", g["d_data"] & ~g["d_fomc"]), ("fomc_only", g["d_fomc"] & ~g["d_data"]),
                         ("both", g["d_data"] & g["d_fomc"]), ("none", ~g["rel"]))}
    out = {"bars": bar_checks, "calendar_agreement": cal,
           "rows": {str(H): int(len(g)) for H, g in res.rows.items()},
           "release_rows": {str(H): int(g["rel"].sum()) for H, g in res.rows.items()},
           "fold_counts": res.counts, "per_fold": per_fold, "by_type": by_type, **a}
    coefs = [{"market": mk, **c} for c in res.coefs]
    return out, coefs


def run() -> int:
    sha = assert_prereg_merged()
    mc = RESULTS / "machinery.json"
    if not mc.exists() or not json.loads(mc.read_text()).get("pass"):
        raise SystemExit("the machinery check (results/machinery.json) has not passed: no run")
    n = log_attempt("outcome_analyses", "outcome_analysis",
                    "XAGUSD and EURUSD dev 2016-2023: Q1, Q2 (H 240, 60), Q3 per market; BH over both markets' "
                    "8 tests; verdict per market by D7 (single run)")
    ev, info = D.events()
    markets, coefs = {}, []
    for mk in V.MARKETS:
        markets[mk], c = _market(mk, ev)
        coefs += c
    comb = rep_core.combine(markets, V.BH_Q)
    out = {"label": "Study A replication, single run", "attempt": n, **stamp(sha), "calendar": info,
           **comb, "markets": markets}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "run.json").write_text(json.dumps(out, indent=1, default=float) + "\n")
    pd.DataFrame(coefs).to_csv(RESULTS / "coefs.csv", index=False, float_format="%.6g")
    print(json.dumps({"replication": out["replication"], "per_market": out["per_market"],
                      "BH_family": out["BH_family"],
                      **{mk: {k: markets[mk][k] for k in ("Q1", "Q2", "Q3")} for mk in V.MARKETS}},
                     indent=1, default=float))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=("machinery", "run"))
    a = ap.parse_args(argv)
    return machinery() if a.what == "machinery" else run()


if __name__ == "__main__":
    sys.exit(main())
