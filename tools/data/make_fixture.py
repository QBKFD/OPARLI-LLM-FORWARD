#!/usr/bin/env python3
"""Cut the CI fixture out of the clean parquet: the bars of the windows the
scanner selftest reads, and nothing else.

    python tools/data/make_fixture.py --out PATH

The selftest (backend/scanner/selftest.py) reads real bars in three places:
case 1 (the first full day from 2023-06-15, its prior day and week), the
2024-03-07 .. 2024-03-13 replay window, and the 14 days before 2025-06-16
plus its first 8 hours. WINDOWS covers each with margin. Every window ends
before the research seal (2026-10-01). The fixture has the parquet's columns
and dtypes, so any loader of the clean parquet reads it unchanged. The output
is listed with its hashes (as for a manifest entry) on stdout.

The source must match the data manifest. Refuses to overwrite --out.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.datafeed.store import manifest  # noqa: E402

# [start, end) UTC
WINDOWS = (("2023-06-01", "2023-06-20"),       # case 1: first full day >= 2023-06-15, prior day and week
           ("2024-03-04", "2024-03-14"),       # the 2024-03-07 .. 2024-03-13 window
           ("2025-05-30", "2025-06-17"))       # 14 days before 2025-06-16, + its first 8 hours
SEAL = "2026-10-01"


def cut(src: Path):
    import pandas as pd
    parts = []
    for start, end in WINDOWS:
        assert end <= SEAL, (start, end)
        parts.append(pd.read_parquet(src, filters=[("timestamp", ">=", pd.Timestamp(start, tz="UTC")),
                                                   ("timestamp", "<", pd.Timestamp(end, tz="UTC"))]))
    return pd.concat(parts, ignore_index=True).sort_values("timestamp", kind="stable").reset_index(drop=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_fixture.py", description=__doc__.splitlines()[0])
    ap.add_argument("--src", default=str(ROOT / "database" / "ohlcv_data" / "XAUUSD_1min_UTC_clean.parquet"))
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        ap.error(f"{out} exists; refusing to overwrite")
    if manifest.verify(a.src) is None:
        ap.error(f"{a.src} is not a file listed in the data manifest")
    df = cut(Path(a.src))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(json.dumps({"windows": WINDOWS, **manifest.describe(out)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
