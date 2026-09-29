#!/usr/bin/env python3
"""
Step 2 audit of the 2024-01-01 .. 2026-01-30 data (counts only; allowed before
PREREG). No outcome, return or event statistic is computed here.

Reads XAUUSD.txt rows with file date 2023-11-01 .. 2026-01-30 (the Nov/Dec 2023
rows are for warm-up and the first PDH), asserts the study cut-off and the
global seal, caches them (gitignored), and writes DATA_AUDIT_2024.md:
coverage per month, gaps, duplicates, and the UTC check (hourly range profile
vs the known-UTC 2021-2023 reference, plus the 17:00 New York break).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research"))
sys.path.insert(0, str(ROOT / "research" / "vol_features_r2"))
from sealing import assert_before_global_seal  # noqa: E402
import audit_long as al  # noqa: E402

SRC = ROOT / "database" / "ohlcv_data" / "XAUUSD.txt"
CACHE = HERE / "cache" / "xauusd_20231101_20260130.parquet"
STUDY_END = pd.Timestamp("2026-01-31")          # exclusive: nothing after 2026-01-30
TEST_START = pd.Timestamp("2024-01-01")


def assert_study_window(index: pd.DatetimeIndex, what="data"):
    naive = index.tz_convert("UTC").tz_localize(None) if index.tz is not None else index
    if len(naive) and naive.max() >= STUDY_END:
        raise AssertionError(f"STUDY CUT-OFF: {what} has timestamps after 2026-01-30")
    assert_before_global_seal(index, what)


def load_raw() -> pd.DataFrame:
    df = pd.read_csv(SRC, skiprows=1, header=None,
                     names=["tk", "d", "t", "open", "high", "low", "close", "vol"],
                     usecols=["d", "t", "open", "high", "low", "close", "vol"],
                     dtype={"d": np.int64, "t": np.int64, "open": float, "high": float,
                            "low": float, "close": float, "vol": float})
    df = df[(df.d >= 20231101) & (df.d <= 20260130)]
    idx = pd.to_datetime(df.d.astype(str) + df.t.astype(str).str.zfill(6), format="%Y%m%d%H%M%S")
    df = df.drop(columns=["d", "t"])
    df.index = idx
    df.index.name = "file_time"
    assert_study_window(df.index, "raw rows")
    return df


def main() -> int:
    raw = load_raw()
    info = {"rows": len(raw), "monotonic_as_stored": bool(raw.index.is_monotonic_increasing),
            "duplicates": int(raw.index.duplicated().sum())}
    df = raw.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    bad = ~((df.high >= df.low) & (df[["open", "high", "low", "close"]] > 0).all(axis=1))
    info.update(bad_ohlc=int(bad.sum()), first=str(df.index.min()), last=str(df.index.max()))
    CACHE.parent.mkdir(exist_ok=True)
    df.to_parquet(CACHE)

    # reference profile (known UTC): 2021-2023 from the pre-2024 cache
    ref = pd.read_parquet(al.CACHE)
    ref = ref[(ref.index >= "2021-01-01") & (ref.index < "2024-01-01")]
    rd = al.us_dst(ref.index)
    ref_prof = {True: al.hour_range_profile(ref[rd]), False: al.hour_range_profile(ref[~rd])}

    t = df[df.index >= TEST_START]
    sd = pd.Series(al.session_date(t.index), index=t.index)
    months = []
    for mth, g in t.groupby(t.index.to_period("M")):
        s = sd[g.index]
        days = s[s.dt.dayofweek < 5]
        per_day = days.groupby(days).size()
        dt = g.index.to_series().diff()
        months.append({"month": str(mth), "rows": len(g), "trading_days": int(len(per_day)),
                       "median_bars_per_day": float(per_day.median()) if len(per_day) else 0.0,
                       "share_days_ge80": float((per_day >= 0.8 * 1380).mean()) if len(per_day) else 0.0,
                       "gaps_5_60min": int(((dt > pd.Timedelta(minutes=5)) & (dt < pd.Timedelta(hours=1))).sum())})
    tz = []
    for label, g in t.groupby((t.index.year.astype(str) + np.where(t.index.month <= 6, "H1", "H2"))):
        dst = al.us_dst(g.index)
        row = {"half": label}
        for season, mask in (("DST", dst), ("STD", ~dst)):
            if mask.sum() < 5000:
                row[season] = None
                continue
            k, c, mg = al.best_shift(al.hour_range_profile(g[mask]), ref_prof[season == "DST"])
            wk = g[mask][g[mask].index.dayofweek.isin([1, 2, 3])]
            per = wk.groupby(wk.index.hour).size().reindex(range(24), fill_value=0) / max(wk.index.normalize().nunique(), 1)
            row[season] = {"shift": int(k), "corr": round(c, 3), "break_hour": int(per.idxmin()),
                           "bars_in_break_hour": round(float(per.min()), 1)}
        tz.append(row)
    res = {"info": info, "months": months, "timezone": tz}
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / "audit_2024.json").write_text(json.dumps(res, indent=1, default=str))

    L = ["# Data audit: XAUUSD.txt 2024-01-01 .. 2026-01-30 (counts only)", "",
         "Written by `audit_2024.py` **before** the PREREG commit. It computes no outcome, return or event "
         "statistic. Rows with file date 2023-11-01 .. 2026-01-30 were read (Nov/Dec 2023 for "
         "warm-up and the first PDH). The study cut-off (nothing after 2026-01-30) and the global seal "
         "(2026-10-01) are asserted. The source file has no rows after January 2026.", "",
         f"- Rows read: {info['rows']:,}. As stored: monotonic {info['monotonic_as_stored']}, "
         f"duplicate timestamps {info['duplicates']}, bad OHLC rows {info['bad_ohlc']}. "
         f"Range {info['first']} .. {info['last']}.", "",
         "## Coverage per month (test period)", "",
         "| month | rows | trading days | median bars/day | share days >= 80 % | gaps 5-60 min |",
         "|---|---:|---:|---:|---:|---:|"]
    L += [f"| {m['month']} | {m['rows']:,} | {m['trading_days']} | {m['median_bars_per_day']:.0f} | "
          f"{m['share_days_ge80']:.2f} | {m['gaps_5_60min']} |" for m in months]
    L += ["", "## Timezone check (hourly range profile vs the UTC 2021-2023 reference; break hour)", "",
          "| half-year | DST shift / corr | DST break hour (bars) | STD shift / corr | STD break hour (bars) |",
          "|---|---|---|---|---|"]
    fmt = lambda r: ("–", "–") if r is None else (f"{r['shift']:+d} / {r['corr']:.2f}",
                                                    f"{r['break_hour']} UTC ({r['bars_in_break_hour']:.0f})")
    for r in tz:
        a, b = fmt(r["DST"])
        c, d = fmt(r["STD"])
        L.append(f"| {r['half']} | {a} | {b} | {c} | {d} |")
    L += ["", "Expected for UTC: shift 0 in both seasons, break at 21 UTC (US DST) / 22 UTC (US standard), "
          "i.e. 17:00 New York."]
    (HERE / "DATA_AUDIT_2024.md").write_text("\n".join(L) + "\n")
    print(json.dumps({"info": info, "timezone": tz}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
