#!/usr/bin/env python3
"""PREREG §6 step 2: event counts on dev, no outcome. Writes results/counts.json
and exits 3 if a pooled depth bucket has fewer than P30 turns (§6.2: stop).

    python research/pullback_structure/run_counts.py

The state machine resolves events internally (P18 needs each event's end), but
nothing here reports an outcome: only how many events reach each question."""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import params as P  # noqa: E402

OUT = HERE / "results" / "counts.json"


def main() -> int:
    d = common.prepare(verify_cache=True)
    rec, _, refused = common.run(d["cd"], d["mt"], d["mh"], d["ml"], d["sig_end"])
    c = common.counts(rec)
    pooled = c["pooled"]["q1_turns_per_bucket"]
    short = [b for b, n in pooled.items() if n < P.MIN_EVENTS]
    out = {"what": "pullback_structure step 2: event counts on dev (2016-01-01 .. 2023-12-31); counts only, no outcome",
           "checks": d["checks"], "model": d["model"], "candles": d["candle_info"],
           "candles_without_sigma": int((~(d["sig_end"] > 0)).sum()),
           "refused_starts": refused, "counts": c,
           "gate_p30": {"min_events": P.MIN_EVENTS, "buckets_below": short, "pass": not short}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"candles": out["candles"], "counts": c, "gate_p30": out["gate_p30"]}, indent=1))
    return 0 if not short else 3


if __name__ == "__main__":
    sys.exit(main())
