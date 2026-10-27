#!/usr/bin/env python3
"""PREREG §6 step 2 as amended (§11.2): event counts on dev, no outcome, and the
dev depth tercile cut-points (§11.1, descriptive). Writes results/counts_a1.json
and exits 3 if there are fewer than P30 pooled turns. (results/counts.json is the
original step 2, which stopped on the depth buckets.)

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

OUT = HERE / "results" / "counts_a1.json"


def main() -> int:
    d = common.prepare(verify_cache=True)
    rec, _, refused = common.run(d["cd"], d["mt"], d["mh"], d["ml"], d["sig_end"])
    c = common.counts(rec)
    short = c["pooled"]["q1_turns"] < P.MIN_EVENTS
    out = {"what": "pullback_structure step 2 (Amendment 1): event counts on dev (2016-01-01 .. 2023-12-31); counts only, no outcome",
           "checks": d["checks"], "model": d["model"], "candles": d["candle_info"],
           "candles_without_sigma": int((~(d["sig_end"] > 0)).sum()),
           "refused_starts": refused, "counts": c,
           "depth_tercile_cuts_sigma": common.tercile_cuts(rec),
           "gate_p30": {"min_events": P.MIN_EVENTS, "pooled_turns": c["pooled"]["q1_turns"], "pass": not short}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"candles": out["candles"], "counts": c, "cuts": out["depth_tercile_cuts_sigma"],
                      "gate_p30": out["gate_p30"]}, indent=1))
    return 0 if not short else 3


if __name__ == "__main__":
    sys.exit(main())
