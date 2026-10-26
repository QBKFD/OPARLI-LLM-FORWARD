#!/usr/bin/env python3
"""The committed CI fixture: synthetic 1-min XAUUSD-like bars, no real data.

    python tools/data/make_synthetic_fixture.py [--out PATH]

XAUUSD.txt is IBKR data, licensed for the owner's own use only, so no real
bar may be in the public repo. This writes made-up bars instead, in the clean
parquet's columns and dtypes, over the windows the scanner selftest reads
(make_fixture.WINDOWS), so the selftest can run in CI:

- trading minutes as gold trades: Sunday 18:00 to Friday 17:00 New York, with
  the daily 17:00-18:00 New York break (so 22:00/21:00 UTC by season);
- prices a random walk from a fixed seed (stdlib `random`, so the bars are
  the same on every Python and numpy version), rounded to 3 decimals;
- volume a made-up positive integer.

Nothing in it is a real price; it only exercises the code paths. The bars
are checked by content: content_sha256 must equal the manifest's entry
(tests/datafeed/test_manifest.py regenerates and compares).
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.datafeed.store import manifest  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "xauusd_synthetic_selftest.parquet"
KEY = "tests/fixtures/xauusd_synthetic_selftest.parquet"
NY = ZoneInfo("America/New_York")
SEED = 20261022
# window start (UTC) -> first open price; windows as in make_fixture.WINDOWS
WINDOWS = (("2023-06-01", "2023-06-20", 1950.0),
           ("2024-03-04", "2024-03-14", 2150.0),
           ("2025-05-30", "2025-06-17", 3350.0))


def is_open(t: datetime) -> bool:
    et = t.astimezone(NY)
    wd, h = et.weekday(), et.hour
    if wd == 5 or (wd == 6 and h < 18) or (wd == 4 and h >= 17):
        return False
    return h != 17


def bars():
    rng = random.Random(SEED)
    rows = []
    for start, end, price in WINDOWS:
        t = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
        stop = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
        close = price
        while t < stop:
            if is_open(t):
                o = close
                c = round(o * (1 + rng.gauss(0.0, 0.0003)), 3)
                h = round(max(o, c) + abs(rng.gauss(0.0, 0.25)), 3)
                lo = round(min(o, c) - abs(rng.gauss(0.0, 0.25)), 3)
                rows.append((t, o, h, lo, c, float(rng.randint(1, 500) * 10)))
                close = c
            t += timedelta(minutes=1)
    return rows


def frame():
    import pandas as pd
    r = bars()
    return pd.DataFrame({"timestamp": pd.DatetimeIndex([x[0] for x in r]).as_unit("ns"),
                         **{c: [float(x[i + 1]) for x in r] for i, c in enumerate(manifest.CONTENT_COLUMNS[1:])}})


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="make_synthetic_fixture.py", description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)
    df = frame()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    got = manifest.content_sha256(df)
    want = manifest.load()["files"].get(KEY, {}).get("content_sha256")
    print(f"wrote {out}: {len(df)} synthetic bars, content SHA-256 {got}")
    if want != got:
        print(f"the manifest's {KEY} has {want}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
