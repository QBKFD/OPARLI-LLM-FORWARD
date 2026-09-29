#!/usr/bin/env python3
"""
Data audit of the long-history 1-min file database/ohlcv_data/XAUUSD.txt.

Writes research/vol_features/DATA_AUDIT_LONG.md (the path the round-2 task
names) and research/vol_features_r2/results/audit_long.json.

Only rows before 2024-01-01 are read into the audit (file time; the file turns
out to be UTC, so this is also the UTC seal). The usability rule is the one
fixed in the round-2 task text (see PREREG.md), applied per calendar year:

  U1  timezone identifiable and consistent: in both US-DST seasons the hourly
      range profile correlates best with the known-UTC reference (2021-2023 of
      the clean UTC parquet) at shift 0 (or at one documented fixed shift);
  U2  >= 90 % of expected minutes present on >= 90 % of trading days;
  U3  stale runs (consecutive 1-min bars with identical O,H,L,C) longer than
      30 minutes on no more than 1 % of trading days.
"""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "database" / "ohlcv_data" / "XAUUSD.txt"
CACHE = HERE / "cache" / "xauusd_txt_pre2024_filetime.parquet"
REF = ROOT / "database" / "ohlcv_data" / "XAUUSD_1min_UTC_clean.parquet"
OUT_MD = ROOT / "research" / "vol_features" / "DATA_AUDIT_LONG.md"
OUT_JSON = HERE / "results" / "audit_long.json"
SEAL = pd.Timestamp("2024-01-01")
NY = ZoneInfo("America/New_York")
EXPECTED_MIN = 1380            # 23h trading day, 18:00 -> 17:00 New York
STALE_MIN = 30


def load_raw() -> pd.DataFrame:
    """Rows with file date < 2024-01-01, indexed by file time (naive)."""
    if CACHE.exists():
        df = pd.read_parquet(CACHE)
    else:
        df = pd.read_csv(SRC, skiprows=1, header=None,
                         names=["tk", "d", "t", "open", "high", "low", "close", "vol"],
                         usecols=["d", "t", "open", "high", "low", "close", "vol"],
                         dtype={"d": np.int64, "t": np.int64, "open": float, "high": float,
                                "low": float, "close": float, "vol": float})
        df = df[df.d < 20240101]
        idx = pd.to_datetime(df.d.astype(str) + df.t.astype(str).str.zfill(6),
                             format="%Y%m%d%H%M%S")
        df = df.drop(columns=["d", "t"])
        df.index = idx
        df.index.name = "file_time"
        CACHE.parent.mkdir(exist_ok=True)
        df.to_parquet(CACHE)
    assert df.index.max() < SEAL, "SEALED DATA: audit read a row >= 2024-01-01"
    return df


def us_dst(index: pd.DatetimeIndex) -> np.ndarray:
    days = index.normalize()
    u = days.unique()
    m = {d: bool(pd.Timestamp(d.date()).tz_localize(NY).replace(hour=12).dst()) for d in u}
    return np.asarray(days.map(m), dtype=bool)


def session_date(index_utc: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """New York trading date: 18:00 NY opens the next date's session."""
    ny = index_utc.tz_localize("UTC").tz_convert(NY)
    return (ny + pd.Timedelta(hours=6)).normalize().tz_localize(None)


def hour_range_profile(df: pd.DataFrame) -> pd.Series:
    wk = df[df.index.dayofweek.isin([1, 2, 3])]
    r = ((wk.high - wk.low) / wk.close).groupby(wk.index.hour).mean()
    return r.reindex(range(24))


def best_shift(profile: pd.Series, ref: pd.Series):
    """Circular hour shift k maximising corr(profile(h + k), ref(h)).

    Hours missing in either profile (the daily break has no bars in the
    reference) are left out of each correlation.
    """
    p = profile.to_numpy(dtype=float)
    q = ref.to_numpy(dtype=float)
    cors = {}
    for k in range(-12, 12):
        pr = np.roll(p, -k)
        ok = np.isfinite(pr) & np.isfinite(q)
        cors[k] = float(np.corrcoef(pr[ok], q[ok])[0, 1]) if ok.sum() >= 18 else -1.0
    k = max(cors, key=cors.get)
    second = max(v for kk, v in cors.items() if kk != k)
    return k, cors[k], cors[k] - second


def stale_run_days(g: pd.DataFrame, sdate: pd.Series) -> set:
    px = g[["open", "high", "low", "close"]].to_numpy()
    same = np.r_[False, (px[1:] == px[:-1]).all(axis=1)]
    # run id increments whenever a bar differs from the previous one
    run_id = np.cumsum(~same)
    lengths = pd.Series(1, index=run_id).groupby(level=0).transform("size").to_numpy()
    long_mask = lengths > STALE_MIN          # run of > 30 identical bars (> 30 minutes)
    return set(pd.DatetimeIndex(np.asarray(sdate)[long_mask]).unique())


def decimals(x: pd.Series) -> int:
    s = x.sample(min(len(x), 20000), random_state=0)
    for k in range(0, 7):
        if (np.abs(s - s.round(k)) < 1e-9).mean() > 0.99:
            return k
    return 7


def main() -> int:
    raw = load_raw()
    info = {"source": str(SRC.relative_to(ROOT)), "rows_pre2024": len(raw),
            "monotonic_as_stored": bool(raw.index.is_monotonic_increasing),
            "duplicates": int(raw.index.duplicated().sum())}
    df = raw.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    info.update(first=str(df.index.min()), last=str(df.index.max()),
                bad_ohlc=int((~((df.high >= df.low) & (df[["open", "high", "low", "close"]] > 0)
                               .all(axis=1))).sum()))

    # known-UTC parquet: 2020-2023 for the exact overlap check, 2021-2023 as the reference profile
    ref = pd.read_parquet(REF, filters=[("timestamp", ">=", pd.Timestamp("2020-01-01", tz="UTC")),
                                        ("timestamp", "<", pd.Timestamp("2024-01-01", tz="UTC"))])
    ref = ref.set_index("timestamp")
    ref.index = ref.index.tz_convert(None)
    overlap = {y: float((df.loc[str(y), "close"].reindex(ref.loc[str(y)].index).values
                         == ref.loc[str(y), "close"].values).mean()) for y in range(2020, 2024)}
    ref = ref[ref.index >= "2021-01-01"]
    rdst = us_dst(ref.index)
    ref_prof = {True: hour_range_profile(ref[rdst]), False: hour_range_profile(ref[~rdst])}

    dst = us_dst(df.index)
    sdate = pd.Series(session_date(df.index), index=df.index)
    years = {}
    for y in range(df.index.year.min(), 2024):
        g = df[df.index.year == y]
        if g.empty:
            continue
        gd = dst[df.index.year == y]
        sd = sdate[g.index]
        # U1 timezone
        tz = {}
        for season, mask in (("DST", gd), ("STD", ~gd)):
            if mask.sum() < 5000:
                tz[season] = {"shift": None, "corr": None, "margin": None}
                continue
            k, c, mg = best_shift(hour_range_profile(g[mask]), ref_prof[season == "DST"])
            wk = g[mask][g[mask].index.dayofweek.isin([1, 2, 3])]
            per = wk.groupby(wk.index.hour).size().reindex(range(24), fill_value=0) / max(
                wk.index.normalize().nunique(), 1)
            tz[season] = {"shift": k, "corr": round(c, 3), "margin": round(mg, 3),
                          "break_hour": int(per.idxmin()), "bars_in_break_hour": round(float(per.min()), 1)}
        shifts = {v["shift"] for v in tz.values() if v["shift"] is not None}
        u1 = len(shifts) == 1 and all(v["shift"] is not None and v["corr"] >= 0.8 for v in tz.values())
        # U2 coverage: NY session days Mon-Fri
        days = sd[sd.dt.dayofweek < 5]
        per_day = days.groupby(days).size()
        per_day = per_day[per_day.index.year == y]
        cov = (per_day / EXPECTED_MIN).clip(upper=1.0)
        share_ok = float((cov >= 0.9).mean()) if len(cov) else 0.0
        u2 = share_ok >= 0.9
        # U3 stale runs
        sdays = stale_run_days(g, sd.to_numpy()) & set(per_day.index)
        stale_share = len(sdays) / max(len(per_day), 1)
        u3 = stale_share <= 0.01
        # descriptive
        mins = g.index.to_series().diff()
        gaps5 = int(((mins > pd.Timedelta(minutes=5)) & (mins < pd.Timedelta(hours=1))).sum())
        years[y] = {
            "rows": len(g), "trading_days": int(len(per_day)),
            "median_bars_per_day": float(per_day.median()) if len(per_day) else 0.0,
            "share_days_cov90": round(share_ok, 3), "median_coverage": round(float(cov.median()), 3),
            "stale_days": len(sdays), "stale_share": round(stale_share, 4),
            "intraday_gaps_5_60min": gaps5, "price_decimals": decimals(g.close),
            "tz": tz, "U1_tz": bool(u1), "U2_coverage": bool(u2), "U3_stale": bool(u3),
            "usable": bool(u1 and u2 and u3),
        }

    usable = [y for y, v in years.items() if v["usable"]]
    confirm = [y for y in usable if y <= 2020]
    res = {"info": info, "overlap_exact_close_match": overlap, "years": years,
           "usable_years": usable, "confirmation_years": confirm}
    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(res, indent=2, default=str))
    write_md(res)
    print(json.dumps({"usable": usable, "confirmation": confirm}, indent=0))
    return 0


def write_md(res):
    i = res["info"]
    L = ["# Data audit: long-history XAUUSD 1-min file", "",
         f"Written by `research/vol_features_r2/audit_long.py`. Source `{i['source']}`. "
         f"Only rows before 2024-01-01 were read into the audit.", "",
         "## File", "",
         f"- {i['rows_pre2024']:,} rows before 2024, {i['first']} to {i['last']}.",
         "- Format: `TICKER,YYYYMMDD,HHMMSS,O,H,L,C,VOL`, one row per 1-min bar with at least one tick.",
         "  The header line has stray text in front of it (`should contai<TICKER>...`), so it is skipped.",
         f"- Rows are not sorted as stored (monotonic: {i['monotonic_as_stored']}). "
         f"Duplicate timestamps: {i['duplicates']} (first kept). Bad OHLC rows: {i['bad_ohlc']}.",
         "- **Timezone: UTC in every year where it can be identified.** 2020–2023 match the clean "
         "UTC parquet close for close with no shift (share of exact matches: "
         + ", ".join(f"{y}: {v:.3f}" for y, v in res["overlap_exact_close_match"].items())
         + "), so the parquet was built from this file. Earlier years: the hourly range profile "
         "correlates best with the UTC reference at shift 0 in both US-DST seasons, and the "
         "daily break sits at 21 UTC (US summer) / 22 UTC (US winter), which is 17:00 New York.",
         "", "## Usability by year", "",
         "U1 timezone (best shift 0 in both seasons, corr >= 0.8). U2 = share of trading days "
         "with >= 90 % of 1380 expected minutes (needs >= 0.90). U3 = share of days with a "
         "stale run > 30 min (needs <= 0.01).", "",
         "| year | rows | days | med bars/day | U2 share | stale share | DST shift/corr | STD shift/corr | break bars/h (DST/STD) | U1 | U2 | U3 | usable |",
         "|---|---:|---:|---:|---:|---:|---|---|---|:-:|:-:|:-:|:-:|"]
    for y, v in res["years"].items():
        d, s = v["tz"]["DST"], v["tz"]["STD"]
        f = lambda t: "–" if t["shift"] is None else f"{t['shift']:+d} / {t['corr']:.2f}"
        b = lambda t: "–" if t.get("bars_in_break_hour") is None else f"{t['bars_in_break_hour']:.0f}"
        ok = lambda x: "✓" if x else "✗"
        L.append(f"| {y} | {v['rows']:,} | {v['trading_days']} | {v['median_bars_per_day']:.0f} | "
                 f"{v['share_days_cov90']:.2f} | {v['stale_share']:.3f} | {f(d)} | {f(s)} | "
                 f"{b(d)}/{b(s)} | {ok(v['U1_tz'])} | {ok(v['U2_coverage'])} | {ok(v['U3_stale'])} | "
                 f"**{ok(v['usable'])}** |")
    L += ["", f"**Usable years:** {', '.join(map(str, res['usable_years']))}.",
          f"**Confirmation years (usable, <= 2020):** {', '.join(map(str, res['confirmation_years']))}.",
          "", "Price precision (decimals, 99 % of closes): "
          + ", ".join(f"{y}: {v['price_decimals']}" for y, v in res["years"].items()) + ".",
          "", "Intraday gaps of 5–60 minutes per year: "
          + ", ".join(f"{y}: {v['intraday_gaps_5_60min']:,}" for y, v in res["years"].items()) + ".",
          ""]
    OUT_MD.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
