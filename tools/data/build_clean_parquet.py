#!/usr/bin/env python3
"""Rebuild database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet from XAUUSD.txt.

The parquet was a one-off export, and its script was never in git. This
rebuilds it by the rule that reproduces it exactly (checked 2026-10-22: every
one of its 2,156,239 rows equals the XAUUSD.txt row with the same timestamp,
open, high, low, close and volume, and no XAUUSD.txt row in its range is left
out):

1. Skip XAUUSD.txt's header line, and parse the rest as
   TICKER,YYYYMMDD,HHMMSS,OPEN,HIGH,LOW,CLOSE,VOL.
2. Read the date and time as UTC (research/vol_features/DATA_AUDIT_LONG.md:
   UTC in every year where it can be identified; 2020-2023 match this parquet
   close for close with no shift).
3. Keep the rows from 2020-01-01 00:00 UTC on (the first is 2020-01-01 23:00).
4. Sort by timestamp. Stop if the kept rows hold a duplicate timestamp or a
   bar with high < max(open, close) or low > min(open, close); the kept range
   has none, and silently picking one would change the data.
5. Columns timestamp (datetime64[ns, UTC]), open, high, low, close, volume
   (float64), written with pandas/pyarrow.

The bytes differ from the original file (parquet writers don't reproduce
them), so the result is checked by content: content_sha256 must equal the
manifest's (backend/datafeed/store/manifest.json).

    python tools/data/build_clean_parquet.py [--txt PATH] [--out PATH]

Refuses to overwrite --out. Exit 0 when the content matches, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.datafeed.store import manifest  # noqa: E402

START = "2020-01-01"
PARQUET_KEY = "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"


def build(txt: Path):
    import pandas as pd
    t = pd.read_csv(txt, skiprows=1, header=None, names=["ticker", "d", "t", "open", "high", "low", "close", "vol"],
                    dtype={"d": str, "t": str})
    ts = pd.to_datetime(t["d"] + t["t"].str.zfill(6), format="%Y%m%d%H%M%S", utc=True)
    df = pd.DataFrame({"timestamp": ts, "open": t["open"].astype("float64"), "high": t["high"].astype("float64"),
                       "low": t["low"].astype("float64"), "close": t["close"].astype("float64"),
                       "volume": t["vol"].astype("float64")})
    df = df[df["timestamp"] >= pd.Timestamp(START, tz="UTC")].sort_values("timestamp", kind="stable")
    df = df.reset_index(drop=True)
    dups = int(df["timestamp"].duplicated().sum())
    bad = int((~((df["high"] >= df[["open", "close"]].max(axis=1)) &
                 (df["low"] <= df[["open", "close"]].min(axis=1)))).sum())
    if dups or bad:
        raise SystemExit(f"{txt}: {dups} duplicate timestamps and {bad} bad OHLC bars from {START} on; "
                         f"the rule doesn't say which to keep")
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="build_clean_parquet.py", description=__doc__.splitlines()[0])
    ap.add_argument("--txt", default=str(ROOT / "database" / "ohlcv_data" / "XAUUSD.txt"))
    ap.add_argument("--out", required=True, help="where to write the parquet (must not exist)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    if out.exists():
        ap.error(f"{out} exists; refusing to overwrite")
    manifest.verify(a.txt)                       # the input must be the listed XAUUSD.txt
    df = build(Path(a.txt))
    got = manifest.content_sha256(df)
    want = manifest.load()["files"][PARQUET_KEY]["content_sha256"]
    df.to_parquet(out, index=False)
    print(f"wrote {out}: {len(df)} rows, {df['timestamp'].min()} .. {df['timestamp'].max()}")
    if got != want:
        print(f"content SHA-256 {got} != the manifest's {want}", file=sys.stderr)
        return 1
    print(f"content SHA-256 matches the manifest ({got})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
