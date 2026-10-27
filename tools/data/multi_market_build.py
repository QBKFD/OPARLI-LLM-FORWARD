#!/usr/bin/env python3
"""Multi-market data, second half (docs/MULTI_MARKET_DATA.md): validate the day
files that `backfill_ibkr.py --instrument` wrote, write one parquet per
instrument with its manifest entry, and build the DXY proxy. Reuses
backfill_build.py's checks. Never prints a price, return or volume: the report
and the manifest entry hold counts, times, shares and hashes only.

    python tools/data/multi_market_build.py instrument SYM DAY_DIR [--update-manifest]
        DAY_DIR/SYM/ must hold _complete.json, _meta.json and the day files.
        Writes database/ohlcv_data/SYM_ibkr_1min_20160101_20260930.parquet, or
        <name>.rejected.parquet if a check fails (exit 1). Checks (any failure rejects):
          - the download is complete; every day file ends with its day_end
            line, and its count matches;
          - every bar on the minute grid, inside 2016-01-01 .. 2026-09-30,
            nothing at or after 2026-10-01; no conflicting duplicates;
          - UTC by the weekend: on >= 90 % of weekends the last bar before it
            falls on Friday 16:00-17:14 New York and the first bar after it on
            Sunday 17:00-18:59 New York, and no whole-hour shift fits better.
            XAGUSD also gets gold's daily-break check (backfill_build.session_share);
          - completeness: >= 90 % of trade dates have >= 90 % of 1380 minutes.

    python tools/data/multi_market_build.py dxy [--update-manifest]
        DXY = 50.14348112 * EURUSD^-0.576 * USDJPY^0.136 * GBPUSD^-0.119
              * USDCAD^0.091 * USDSEK^0.042 * USDCHF^0.036, on the minutes where
        all six pairs have a bar. open and close come from the pairs' opens and
        closes. high and low are bounds: each pair's high where its exponent is
        positive and its low where it is negative (and the reverse for low), so
        high >= open, close >= low. The six files must match the manifest first.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.datafeed.store import manifest  # noqa: E402

_spec = importlib.util.spec_from_file_location("backfill_build", Path(__file__).with_name("backfill_build.py"))
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

NY = ZoneInfo("America/New_York")
SEAL = "2026-10-01"
RANGE = ("2016-01-01", "2026-10-01")            # [start, end) UTC
FX = ("EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDSEK", "USDCHF")
MULTI = ("XAGUSD",) + FX
DXY_K, DXY_W = 50.14348112, {"EURUSD": -0.576, "USDJPY": 0.136, "GBPUSD": -0.119, "USDCAD": 0.091,
                             "USDSEK": 0.042, "USDCHF": 0.036}
WEEKEND_MIN_SHARE = 0.90


def key(sym: str) -> str:
    return f"database/ohlcv_data/{sym}_ibkr_1min_20160101_20260930.parquet"


DXY_KEY = "database/ohlcv_data/DXY_PROXY_1min_20160101_20260930.parquet"


def read_days(base: Path) -> tuple[dict, list[dict], list[str]]:
    """The bars of every day file, and the problems: a missing day_end, a count
    that doesn't match, or a download without _complete.json."""
    fails = []
    if not (base / "_complete.json").exists():
        fails.append("incomplete download: no _complete.json (rerun the same --instrument command to resume)")
    meta = json.loads((base / "_meta.json").read_text()) if (base / "_meta.json").exists() else {}
    bars = []
    for f in sorted(base.glob("????-??-??.jsonl")):
        lines = f.read_text().splitlines()
        tail = json.loads(lines[-1]) if lines else {}
        rows = [json.loads(x) for x in lines[:-1]]
        if tail.get("kind") != "day_end" or tail.get("bars") != len(rows):
            fails.append(f"{f.name}: no day_end line or a count mismatch")
            continue
        bars += [{**r, "type": meta.get("what_to_show", "MIDPOINT")} for r in rows]
    return meta, bars, fails


def weekend_share(ts, shift_h: int = 0) -> dict:
    """Weekends (gaps > 24 h) whose last bar before falls on Friday 16:00-17:14 New
    York and whose first bar after falls on Sunday 17:00-18:59 New York."""
    import numpy as np
    import pandas as pd
    t = pd.DatetimeIndex(ts) + pd.Timedelta(hours=shift_h)
    if len(t) < 2:
        return {"weekends": 0, "share": None}
    g = np.flatnonzero(np.diff(t.asi8) > 24 * 3600 * 10**9)
    a, b = t[g].tz_convert(NY), t[g + 1].tz_convert(NY)
    ma, mb = a.hour * 60 + a.minute, b.hour * 60 + b.minute
    ok = (a.weekday == 4) & (ma >= 16 * 60) & (ma < 17 * 60 + 15) & (b.weekday == 6) & (mb >= 17 * 60) & (mb < 19 * 60)
    return {"weekends": int(len(g)), "share": float(ok.mean()) if len(g) else None}


def build_instrument(sym: str, base: Path) -> tuple[object, dict, list[str], dict]:
    import pandas as pd
    meta, bars, fails = read_days(base)
    raw = bb.frame(bars)
    df, conflicts = bb.dedupe(raw)
    exact = int(len(raw) - len(df))
    if conflicts:
        fails.append(f"{conflicts} conflicting duplicate bars")
    df = df.drop_duplicates("t").drop(columns=["type"])
    off_grid = int((df["t"] % 60 != 0).sum())
    if off_grid:
        fails.append(f"{off_grid} bars off the minute grid")
    lo, hi = pd.Timestamp(RANGE[0], tz="UTC"), pd.Timestamp(RANGE[1], tz="UTC")
    outside = int(((df["timestamp"] < lo) | (df["timestamp"] >= hi)).sum())
    sealed = int((df["timestamp"] >= pd.Timestamp(SEAL, tz="UTC")).sum())
    if sealed:
        fails.append(f"{sealed} bars at or after {SEAL} (sealed)")
    df = df[(df["timestamp"] >= lo) & (df["timestamp"] < hi)].drop(columns=["t"]).sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        fails.append("no bars")
    ts = pd.DatetimeIndex(df["timestamp"])
    wk = {h: weekend_share(ts, h) for h in range(-6, 7)} if len(df) else {}
    best = max(wk, key=lambda h: wk[h]["share"] or 0) if wk else None
    if wk and ((wk[0]["share"] or 0) < WEEKEND_MIN_SHARE):
        fails.append(f"UTC weekend check: {wk[0]['share']} of {wk[0]['weekends']} weekends (need {WEEKEND_MIN_SHARE})")
    if best not in (None, 0) and (wk[best]["share"] or 0) > (wk[0]["share"] or 0):
        fails.append(f"timezone: the weekend fits best shifted by {best} h, not 0 (UTC expected)")
    sess = bb.session_share(ts) if (sym == "XAGUSD" and len(df)) else {}
    if sess and not any(r["days"] for r in sess.values()):
        fails.append("UTC session check (daily break): no full days")
    for season, r in sess.items():
        if r["days"] and r["share"] < bb.SESSION_MIN_SHARE:                 # a season absent from the data is skipped
            fails.append(f"UTC session check (daily break), {season}: {r['share']} of {r['days']} days")
    comp = bb.completeness(ts) if len(df) else {"share": 0.0}
    if comp["share"] < bb.COMPLETE_MIN_SHARE:
        fails.append(f"completeness {comp['share']:.3f} < {bb.COMPLETE_MIN_SHARE}")
    bad = int((~((df["high"] >= df[["open", "close"]].max(axis=1)) &
                 (df["low"] <= df[["open", "close"]].min(axis=1)))).sum()) if len(df) else 0
    done = sorted((base / "_done").glob("*.json"))
    markers = [json.loads(p.read_text()) for p in done]
    checks = {"rows": int(len(df)), "timestamps_tz_aware_utc": bool(len(df)) and str(df["timestamp"].dtype) == "datetime64[ns, UTC]",
              "first_utc": df["timestamp"].iloc[0].isoformat() if len(df) else None,
              "last_utc": df["timestamp"].iloc[-1].isoformat() if len(df) else None,
              "exact_duplicates_dropped": exact, "conflicting_duplicates": conflicts, "off_grid": off_grid,
              "outside_range_dropped": outside, "sealed": sealed,
              "weekend_utc": wk.get(0), "best_whole_hour_shift": best, "session_utc": sess,
              "completeness": comp, "gaps_in_session": bb.gaps(ts) if len(df) else {},
              "rows_per_trade_date": bb.rows_per_day(ts) if len(df) else {},
              "bad_ohlc_bars_not_fixed": bad,
              "chunks": {"total": len(markers), "fetched_by_day": sum(m["mode"] == "day" for m in markers),
                         "with_ibkr_errors": {m["chunk_end"][:10]: m["errors"] for m in markers if m["errors"]}},
              "failures": fails}
    return df, checks, fails, meta


def entry(meta: dict, checks: dict, path: Path, passed: bool, sym: str) -> dict:
    return {"in_git": False, "status": "unused by any study",
            "source": {"provider": "IBKR historical data (tools/data/backfill_ibkr.py --instrument on the EC2 paper gateway)",
                       "licence": "IBKR market data, the owner's own use only: no redistribution, never in the public repo",
                       "instrument": sym, "contract": meta.get("contract"), "what_to_show": meta.get("what_to_show"),
                       "bar_size": meta.get("bar_size"), "use_rth": meta.get("use_rth"), "format_date": meta.get("format_date"),
                       "client_id": meta.get("client_id"), "downloaded_utc": meta.get("started_utc")},
            "timezone": "UTC (formatDate=2), checked against the weekend (and the daily break for XAGUSD)",
            "periods": "research/SEALING.md, Instrument periods", "validation": {"passed": passed, **checks},
            "doc": "docs/MULTI_MARKET_DATA.md", **manifest.describe(path)}


def report(e: dict, name: str) -> str:
    v = e["validation"]
    g, c = v.get("gaps_in_session") or {}, v.get("completeness") or {}
    per = sorted((v.get("rows_per_trade_date") or {}).values())
    w = v.get("weekend_utc") or {}
    lines = [f"INTEGRITY REPORT (no prices): {name}", f"sha256: {e.get('sha256')}",
             f"passed: {'yes' if v['passed'] else 'NO'}",
             f"rows: {v.get('rows')}   first UTC: {v.get('first_utc')}   last UTC: {v.get('last_utc')}",
             f"timestamps tz-aware UTC: {v.get('timestamps_tz_aware_utc')}   off grid: {v.get('off_grid')}   "
             f"at/after {SEAL}: {v.get('sealed')}   outside range dropped: {v.get('outside_range_dropped')}",
             f"duplicates: exact dropped {v.get('exact_duplicates_dropped')}, conflicting {v.get('conflicting_duplicates')}",
             f"timezone: best whole-hour shift {v.get('best_whole_hour_shift')} h (must be 0); weekend check "
             f"{w.get('share')} of {w.get('weekends')} weekends" +
             ("; daily break " + ", ".join(f"{k} {r['share']} of {r['days']}" for k, r in v["session_utc"].items())
              if v.get("session_utc") else ""),
             f"completeness: {c.get('full')} of {c.get('trade_dates')} trade dates full (share {c.get('share')})",
             f"rows per trade date: min {per[0] if per else None}, median {per[len(per) // 2] if per else None}, max {per[-1] if per else None}",
             f"short days: {len(c.get('short_days') or {})}",
             f"gaps inside sessions: {g.get('count')} gaps, {g.get('missing_minutes')} missing minutes",
             "longest gaps: " + "; ".join(f"{x['after_utc']} +{x['missing_minutes']} min" for x in g.get("longest", [])),
             f"bad OHLC bars (counted, not fixed): {v.get('bad_ohlc_bars_not_fixed')}",
             f"chunks: {v['chunks']['total']}, fetched day by day {v['chunks']['fetched_by_day']}, "
             f"with IBKR errors {len(v['chunks']['with_ibkr_errors'])}",
             f"failures: {v.get('failures') or 'none'}"]
    return "\n".join(lines)


def build_dxy(paths: dict) -> tuple[object, dict]:
    """The proxy on the common minutes (values are never printed)."""
    import numpy as np
    import pandas as pd
    frames = {s: pd.read_parquet(p).set_index("timestamp")[["open", "high", "low", "close"]] for s, p in paths.items()}
    common = frames[FX[0]].index
    for s in FX[1:]:
        common = common.intersection(frames[s].index)
    common = common.sort_values()
    out = pd.DataFrame(index=common)
    for f in ("open", "close"):
        out[f] = DXY_K * np.prod([frames[s].loc[common, f].to_numpy() ** w for s, w in DXY_W.items()], axis=0)
    out["high"] = DXY_K * np.prod([frames[s].loc[common, "high" if w > 0 else "low"].to_numpy() ** w for s, w in DXY_W.items()], axis=0)
    out["low"] = DXY_K * np.prod([frames[s].loc[common, "low" if w > 0 else "high"].to_numpy() ** w for s, w in DXY_W.items()], axis=0)
    out["volume"] = np.nan                                   # the manifest's content columns; a proxy has no volume
    out = out.reset_index().rename(columns={"index": "timestamp"})[["timestamp", "open", "high", "low", "close", "volume"]]
    lo, hi = pd.Timestamp(RANGE[0], tz="UTC"), pd.Timestamp(RANGE[1], tz="UTC")
    if len(out) and (out["timestamp"].min() < lo or out["timestamp"].max() >= hi):
        raise SystemExit("DXY proxy: rows outside the range or sealed")
    ok_hl = bool(((out["high"] >= out[["open", "close"]].max(axis=1) - 1e-12) &
                  (out["low"] <= out[["open", "close"]].min(axis=1) + 1e-12)).all())
    checks = {"rows": int(len(out)), "minutes_per_pair": {s: int(len(f)) for s, f in frames.items()},
              "dropped_not_all_six": {s: int(len(f) - len(out)) for s, f in frames.items()},
              "first_utc": out["timestamp"].iloc[0].isoformat() if len(out) else None,
              "last_utc": out["timestamp"].iloc[-1].isoformat() if len(out) else None,
              "high_low_bounds_hold": ok_hl, "formula": "50.14348112 * EURUSD^-0.576 * USDJPY^0.136 * GBPUSD^-0.119 * USDCAD^0.091 * USDSEK^0.042 * USDCHF^0.036"}
    return out, checks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="multi_market_build.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("instrument")
    i.add_argument("sym", choices=MULTI)
    i.add_argument("day_dir")
    i.add_argument("--out-dir", default=str(ROOT / "database" / "ohlcv_data"))
    i.add_argument("--update-manifest", action="store_true")
    x = sub.add_parser("dxy")
    x.add_argument("--data-dir", default=str(ROOT / "database" / "ohlcv_data"))
    x.add_argument("--update-manifest", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "instrument":
        df, checks, fails, meta = build_instrument(a.sym, Path(a.day_dir) / a.sym)
        k = key(a.sym) if not fails else key(a.sym).replace(".parquet", ".rejected.parquet")
        out = Path(a.out_dir) / Path(k).name
        if out.exists():
            ap.error(f"{out} exists; refusing to overwrite")
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        e = entry(meta, checks, out, not fails, a.sym)
        print(report(e, out.name))
        if a.update_manifest:
            bb.write_manifest(k, e, None)
        if fails:
            print(f"REJECTED, written as {out}", file=sys.stderr)
            return 1
        print(f"passed: {out}", file=sys.stderr)
        return 0
    data = Path(a.data_dir)
    paths = {s: data / Path(key(s)).name for s in FX}
    for s, p in paths.items():
        if manifest.verify(p) != key(s):
            ap.error(f"{p} doesn't match its manifest entry")
    df, checks = build_dxy(paths)
    out = data / Path(DXY_KEY).name
    if out.exists():
        ap.error(f"{out} exists; refusing to overwrite")
    df.to_parquet(out, index=False)
    e = {"in_git": False, "status": "unused by any study",
         "source": {"derived_from": [key(s) for s in FX], "how": checks["formula"] + " on the minutes where all six have a bar; high/low are bounds",
                    "licence": "derived from IBKR market data, the owner's own use only"},
         "periods": "research/SEALING.md, Instrument periods", "validation": checks, "doc": "docs/MULTI_MARKET_DATA.md",
         **manifest.describe(out)}
    if a.update_manifest:
        bb.write_manifest(DXY_KEY, e, None)
    print(f"DXY PROXY (no prices): {out.name}\nsha256: {e.get('sha256')}\nrows: {checks['rows']}   first UTC: {checks['first_utc']}   "
          f"last UTC: {checks['last_utc']}\ndropped (minute not in all six): {checks['dropped_not_all_six']}\n"
          f"high/low bounds hold: {checks['high_low_bounds_hold']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
