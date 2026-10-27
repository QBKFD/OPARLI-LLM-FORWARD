#!/usr/bin/env python3
"""The owner's count-only comparison (2026-10-27): turns on dev 2016-2023,
pooled bull + bear, for six timeframe / k / swing-count variants; the median
pullback length (candles from the swing low to the turn); and the holdout n
estimated from the dev turn rate. No outcome, baseline or label is reported.
Variants other than (a) are not PREREG settings. Writes results/variant_counts.json.

    python research/pullback_structure/run_variants.py
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402
import data  # noqa: E402
import params as P  # noqa: E402
import pandas as pd  # noqa: E402
import pullback as pb  # noqa: E402

OUT = HERE / "results" / "variant_counts.json"
VARIANTS = {"a": ("4h", 3, 3), "b": ("4h", 3, 2), "c": ("4h", 2, 3), "d": ("4h", 2, 2), "e": ("1h", 3, 3), "f": ("1h", 3, 2)}
HOLDOUT = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-10-01", tz="UTC"))   # P23


def main() -> int:
    d = common.prepare(verify_cache=False, timeframe="4h")
    h1, h1_info = pb.candles_hourly(d["m"], P.MIN_SHARE)
    sig_h1 = data.at_labels(d["tbl"], f"sigma_px_{P.SIGMA_H}", pd.DatetimeIndex(h1["end"]))
    dev_years = (P.DEV_END_EXCL - P.EVENTS_FROM).days / 365.25
    hold_years = (HOLDOUT[1] - HOLDOUT[0]).days / 365.25
    out = {"what": "turns on dev 2016-01-01 .. 2023-12-31, pooled bull + bear; counts only (no outcome, baseline or label)",
           "dev_years": round(dev_years, 3), "holdout_years": round(hold_years, 3),
           "note": "W, X, the horizon and P18 apply in candles of each variant's timeframe; sigma is the pinned 240-min HAR at the candle's close",
           "candles_1h": h1_info, "variants": {}}
    for name, (tf, k, ns) in VARIANTS.items():
        cd, sig = (d["cd"], d["sig_end"]) if tf == "4h" else (h1, sig_h1)
        run = {**common.RUN, "k": k, "n_swings": ns}
        _, evs, _ = pb.run_both(cd, d["mt"], d["mh"], d["ml"], sig, events_from_ns=P.EVENTS_FROM.value, **run)
        turns = [e for e in evs if e.turn >= 0 and e.kind != "truncated"]
        length = [e.turn - e.i for e in turns]
        out["variants"][name] = {"timeframe": tf, "k": k, "swings": ns, "turns": len(turns),
                                 "median_pullback_candles": float(np.median(length)) if length else None,
                                 "holdout_turns_estimate": round(len(turns) / dev_years * hold_years, 1)}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
