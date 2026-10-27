#!/usr/bin/env python3
"""PREREG §6 step 4 as amended (§11): Q1 (pooled), Q2, Q3 on dev, the BH family,
and the dev verdict (§11.4). THIS IS AN OUTCOME ANALYSIS: it refuses to start
unless the random-walk gate passed and ATTEMPTS.json has outcome_analyses 0, and
it increments the counter before any outcome is read. Writes results/dev.json
(aggregates only). The holdout is not touched here.

    python research/pullback_structure/run_dev.py [--check]

--check runs everything before the attempt and stops."""
import json
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import params as P  # noqa: E402

OUT = HERE / "results" / "dev.json"
ATTEMPTS = HERE / "ATTEMPTS.json"
RW = HERE / "results" / "rw.json"
COUNTS = HERE / "results" / "counts_a2.json"


def bump_attempt(what: str, by: str) -> int:
    d = json.loads(ATTEMPTS.read_text())
    d["outcome_analyses"] += 1
    d["log"].append({"n": len(d["log"]) + 1, "kind": "outcome_analysis", "date": date.today().isoformat(),
                     "what": what, "by": by})
    ATTEMPTS.write_text(json.dumps(d, indent=1) + "\n")
    return d["outcome_analyses"]


def main() -> int:
    if json.loads(ATTEMPTS.read_text())["outcome_analyses"] != 0:
        raise SystemExit("ATTEMPTS.json outcome_analyses is not 0: dev has been run; it is not rerun")
    rw = json.loads(RW.read_text())
    if not rw["gate"]["pass"]:
        raise SystemExit("the random-walk gate did not pass (§5): the study is stopped at machinery")
    cuts = tuple(json.loads(COUNTS.read_text())["depth_tercile_cuts_sigma"])
    null_share = rw["q2_null_share_B"]
    d = common.prepare(verify_cache=False)
    if "--check" in sys.argv:
        print(f"check ok: {len(d['cd']):,} candles; null share {null_share:.4f}; cuts {cuts}; attempts still 0")
        return 0
    n = bump_attempt("dev (2016-2023): Q1 pooled, Q2, Q3, BH; verdict by PREREG §11.4",
                     "owner, 2026-10-27 (Amendment 1; run on dev only)")
    assert n == 1
    # ---- from here on, outcomes are read
    rec, _, refused = common.run(d["cd"], d["mt"], d["mh"], d["ml"], d["sig_end"])
    res = common.analyse(rec, null_share, P.BOOT_SEED, cuts)
    fam = common.family(res)
    v = common.verdict(res["q1"], fam["bh"])
    out = {"what": "pullback_structure dev (2016-01-01 .. 2023-12-31), outcome analysis 1; aggregates only",
           "attempt": n, "checks": d["checks"], "model": d["model"], "refused_starts": refused,
           "verdict_q1_dev": v, "holdout_next": v == "PASS",
           "family": fam, **res}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"verdict_q1_dev": v, "q1": res["q1"], "q2": res["q2"], "q3": res["q3"], "family": fam}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
