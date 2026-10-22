#!/usr/bin/env python3
"""The XAUUSD backfill, second half: pick whatToShow, validate, write.

docs/BACKFILL_2026_PROPOSAL.md has the plan; tools/data/backfill_ibkr.py
downloads on the EC2 paper gateway. This runs where XAUUSD.txt is (the Mac),
on the JSON-lines files that tool wrote. Every rule below was fixed in the
proposal before any data was read.

    python tools/data/backfill_build.py pick OVERLAP.jsonl --out PICK.json
        For each type in the overlap probe, the share of XAUUSD.txt's minutes
        in 2026-01-19 .. 2026-01-30 that the type reproduces exactly (open,
        high, low and close equal to 1e-6; a minute missing from the type
        counts as a miss). The type with the highest share is picked if that
        share is >= 99.5 %; else no type is picked and the backfill stops
        (exit 1). Also reported: the share whose volume matches too.

    python tools/data/backfill_build.py build DOWNLOAD.jsonl --pick PICK.json [--update-manifest]
        Checks the download, then writes
        database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet, or
        <name>.rejected.parquet if a check fails (exit 1), and prints the
        manifest entry. --update-manifest also writes the entry into
        backend/datafeed/store/manifest.json and, on a pass, fills
        XAUUSD.txt's what_to_show from the pick. Checks (any failure rejects):
          - the file is complete ("end" line), from --download of the picked
            type, on the same contract conId as the probe;
          - every bar on the minute grid, inside 2026-02-01 .. 2026-09-30,
            nothing at or after 2026-10-01;
          - no conflicting duplicates (requests overlap, so equal duplicates
            are expected and dropped);
          - UTC by the session: on >= 90 % of days in each season, the last
            bar before the daily 17:00 New York break is at 16:59 New York and
            the first bar after it at 18:00 New York (21:59 / 23:00 UTC in
            winter, 20:59 / 22:00 UTC from 2026-03-08);
          - completeness: >= 90 % of trade dates have >= 90 % of 1380 minutes.
        Recorded, not checks: bad OHLC bars (counted, never fixed), requests
        with no bars or with IBKR errors, the best whole-hour shift of the
        session test, and the seam to XAUUSD.txt's last bar (as a % gap).
        The entry holds counts, shares, times and hashes, never a price: it
        is committed to the public repo.

Volume: IBKR's -1 ("none") is stored as NaN.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.datafeed.store import manifest  # noqa: E402

NY = ZoneInfo("America/New_York")
TXT_KEY = "database/ohlcv_data/XAUUSD.txt"
KEY = "database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet"
OVERLAP = ("2026-01-19", "2026-01-31")            # [start, end) UTC
RANGE = ("2026-02-01", "2026-10-01")              # [start, end) UTC
SEAL = "2026-10-01"
PICK_MIN_SHARE = 0.995
SESSION_MIN_SHARE = 0.90
COMPLETE_MIN_SHARE, FULL_DAY_MINUTES = 0.90, 1380
EPS = 1e-6


class Rejected(Exception):
    pass


def read_jsonl(path) -> tuple[dict, list[dict], list[dict], dict | None]:
    meta, reqs, bars, end = None, [], [], None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            k = r.get("kind")
            if k == "meta":
                meta = r
            elif k == "request":
                reqs.append(r)
            elif k == "bar":
                bars.append(r)
            elif k == "end":
                end = r
    if meta is None or meta.get("schema") != "backfill_ibkr/1":
        raise SystemExit(f"{path}: not a backfill_ibkr/1 file")
    return meta, reqs, bars, end


def frame(bars: list[dict]):
    import numpy as np
    import pandas as pd
    df = pd.DataFrame(bars)
    if df.empty:
        return pd.DataFrame(columns=["type", "timestamp", "open", "high", "low", "close", "volume"])
    vol = df["v"].astype("float64")
    df = pd.DataFrame({"type": df["type"], "t": df["t"].astype("int64"),
                       "timestamp": pd.to_datetime(df["t"], unit="s", utc=True).astype("datetime64[ns, UTC]"),
                       "open": df["o"].astype("float64"), "high": df["h"].astype("float64"),
                       "low": df["l"].astype("float64"), "close": df["c"].astype("float64"),
                       "volume": vol.where(vol >= 0, np.nan)})
    return df


def dedupe(df) -> tuple[object, int]:
    """Drop equal duplicates (overlapping requests); count conflicting ones."""
    cols = ["type", "t", "open", "high", "low", "close", "volume"]
    df = df.drop_duplicates(subset=cols).sort_values(["type", "t"], kind="stable")
    conflicts = int(df.duplicated(subset=["type", "t"]).sum())
    return df.reset_index(drop=True), conflicts


def load_txt(txt: Path, start: str, end: str):
    import pandas as pd
    manifest.verify(txt)
    t = pd.read_csv(txt, skiprows=1, header=None, names=["tk", "d", "t", "open", "high", "low", "close", "vol"],
                    dtype={"d": str, "t": str})
    t = t[t["d"] >= start.replace("-", "")[:8]]
    ts = pd.to_datetime(t["d"] + t["t"].str.zfill(6), format="%Y%m%d%H%M%S", utc=True)
    out = pd.DataFrame({"timestamp": ts, "open": t["open"].astype("float64"), "high": t["high"].astype("float64"),
                        "low": t["low"].astype("float64"), "close": t["close"].astype("float64"),
                        "volume": t["vol"].astype("float64")})
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    return out[(out["timestamp"] >= s) & (out["timestamp"] < e)].drop_duplicates("timestamp").reset_index(drop=True)


# --- pick -------------------------------------------------------------------------

def pick(meta: dict, reqs: list[dict], bars: list[dict], ref) -> dict:
    df, _ = dedupe(frame(bars))
    table = {}
    for t in sorted({r["type"] for r in reqs}):
        got = df[df["type"] == t].set_index("timestamp")
        m = ref.join(got[["open", "high", "low", "close", "volume"]], on="timestamp", rsuffix="_ib")
        ohlc = ((m["open"] - m["open_ib"]).abs() <= EPS) & ((m["high"] - m["high_ib"]).abs() <= EPS) & \
               ((m["low"] - m["low_ib"]).abs() <= EPS) & ((m["close"] - m["close_ib"]).abs() <= EPS)
        vol = ohlc & ((m["volume"] - m["volume_ib"]).abs() <= EPS)
        errs = sorted({e["code"] for r in reqs if r["type"] == t for e in r["errors"]})
        table[t] = {"bars": int(len(got)), "ohlc_share": float(ohlc.mean()) if len(m) else 0.0,
                    "ohlc_and_volume_share": float(vol.mean()) if len(m) else 0.0, "ibkr_error_codes": errs}
    best = max(table, key=lambda k: table[k]["ohlc_share"]) if table else None
    chosen = best if best and table[best]["ohlc_share"] >= PICK_MIN_SHARE else None
    return {"schema": "backfill_pick/1", "overlap_utc": list(OVERLAP), "reference": TXT_KEY,
            "reference_minutes": int(len(ref)), "rule": f"highest ohlc_share, and >= {PICK_MIN_SHARE}",
            "table": table, "chosen": chosen, "contract": meta["contract"],
            "volume_reproduced": bool(chosen and table[chosen]["ohlc_and_volume_share"] >= PICK_MIN_SHARE)}


# --- build checks -------------------------------------------------------------------

def session_share(ts, shift_h: int = 0) -> dict:
    """Per season (US DST or not), the share of days whose daily break edges sit
    at 16:59 and 18:00 New York: the last bar before 17:00 (Mon-Fri) and the
    first bar from 18:00 (Sun-Thu). ts: UTC DatetimeIndex."""
    import pandas as pd
    et = (pd.DatetimeIndex(ts) + pd.Timedelta(hours=shift_h)).tz_convert(NY)
    f = pd.DataFrame({"day": et.tz_localize(None).normalize(), "mod": et.hour * 60 + et.minute})
    wd = f["day"].dt.weekday
    last = f[(f["mod"] < 17 * 60) & (wd <= 4)].groupby("day")["mod"].max() == 16 * 60 + 59
    first = f[(f["mod"] >= 18 * 60) & wd.isin([6, 0, 1, 2, 3])].groupby("day")["mod"].min() == 18 * 60
    out = {}
    for season in ("dst", "std"):
        def keep(r):
            dst = [bool(datetime(d.year, d.month, d.day, 12, tzinfo=NY).dst()) for d in r.index]
            return r[[x == (season == "dst") for x in dst]]
        both = pd.concat([keep(last), keep(first)])
        out[season] = {"days": int(len(both)), "share": float(both.mean()) if len(both) else None}
    return out


def completeness(ts) -> dict:
    import pandas as pd
    et = pd.DatetimeIndex(ts).tz_convert(NY).tz_localize(None)
    trade_date = (et + pd.Timedelta(hours=6)).normalize()           # a session opens 18:00 NY for the next date
    counts = pd.Series(1, index=trade_date).groupby(level=0).sum()
    counts = counts[counts.index.weekday <= 4]
    full = int((counts >= 0.9 * FULL_DAY_MINUTES).sum())
    short = {str(d.date()): int(n) for d, n in counts[counts < 0.9 * FULL_DAY_MINUTES].items()}
    return {"trade_dates": int(len(counts)), "full": full, "share": full / len(counts) if len(counts) else 0.0,
            "short_days": short}


def build(meta, reqs, bars, end, pk: dict, txt_last=None) -> tuple[object, dict, list[str]]:
    import pandas as pd
    fails = []
    if end is None:
        fails.append("incomplete download: no 'end' line")
    if meta.get("mode") != "download" or meta.get("what_to_show") != pk.get("chosen"):
        fails.append(f"download is {meta.get('mode')} {meta.get('what_to_show')}, the pick is {pk.get('chosen')}")
    if meta["contract"]["con_id"] != pk["contract"]["con_id"]:
        fails.append(f"contract conId {meta['contract']['con_id']} != the probe's {pk['contract']['con_id']}")
    df, conflicts = dedupe(frame(bars))
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
    df = df[(df["timestamp"] >= lo) & (df["timestamp"] < hi)].drop(columns=["t"]).reset_index(drop=True)
    if df.empty:
        fails.append("no bars")
    ts = pd.DatetimeIndex(df["timestamp"])
    sess = session_share(ts) if len(df) else {}
    for season, r in sess.items():
        if not r["days"] or r["share"] < SESSION_MIN_SHARE:
            fails.append(f"UTC session check, {season}: {r['share']} of {r['days']} days (need {SESSION_MIN_SHARE})")
    shifts = {h: session_share(ts, h) for h in range(-6, 7)} if len(df) else {}
    score = {h: sum((r["share"] or 0) for r in v.values()) for h, v in shifts.items()}
    best_shift = max(score, key=score.get) if score else None
    comp = completeness(ts) if len(df) else {"share": 0.0}
    if comp["share"] < COMPLETE_MIN_SHARE:
        fails.append(f"completeness {comp['share']:.3f} < {COMPLETE_MIN_SHARE}")
    bad = int((~((df["high"] >= df[["open", "close"]].max(axis=1)) &
                 (df["low"] <= df[["open", "close"]].min(axis=1)))).sum()) if len(df) else 0
    seam = None
    if txt_last is not None and len(df):
        # times and the relative gap only: the entry goes into the public repo, prices must not
        seam = {"txt_last_ts": txt_last[0], "first_ts": df["timestamp"].iloc[0].isoformat(),
                "first_open_vs_txt_last_close_pct": round((float(df["open"].iloc[0]) / txt_last[1] - 1) * 100, 3)}
    checks = {"conflicting_duplicates": conflicts, "off_grid": off_grid, "outside_range_dropped": outside,
              "sealed": sealed, "session_utc": sess, "best_whole_hour_shift": best_shift,
              "completeness": comp, "bad_ohlc_bars_not_fixed": bad,
              "requests_without_bars": [r["day"] for r in reqs if r["bars"] == 0],
              "requests_with_ibkr_errors": {r["day"]: sorted({e["code"] for e in r["errors"]})
                                            for r in reqs if r["errors"]},
              "seam": seam, "failures": fails}
    return df, checks, fails


def entry(meta, pk, checks, df, path: Path, passed: bool) -> dict:
    d = manifest.describe(path)
    return {
        "in_git": False,
        "status": "unused by any study",
        "source": {
            "provider": "IBKR historical data (tools/data/backfill_ibkr.py on the EC2 paper gateway)",
            "licence": "IBKR market data, the owner's own use only: no redistribution, never in the public repo",
            "contract": meta["contract"], "what_to_show": meta["what_to_show"], "bar_size": meta["bar_size"],
            "use_rth": meta["use_rth"], "format_date": meta["format_date"], "duration_per_request": meta["duration"],
            "client_id": meta["client_id"], "n_requests": meta["n_requests"], "downloaded_utc": meta["started_utc"],
            "what_to_show_pick": {k: pk[k] for k in ("overlap_utc", "reference", "reference_minutes", "rule",
                                                     "table", "chosen", "volume_reproduced")},
        },
        "timezone": "UTC (formatDate=2) and checked against the session: see checks.session_utc",
        "validation": {"passed": passed, **checks},
        "doc": "docs/BACKFILL_2026_PROPOSAL.md",
        **d,
    }


def _plain(o):
    """numpy scalars and timestamps -> JSON."""
    return o.item() if hasattr(o, "item") else str(o)


def write_manifest(key: str, new: dict, txt_what: str | None, path: Path = None) -> None:
    path = Path(path or manifest.MANIFEST)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["files"][key] = new
    if txt_what:
        src = doc["files"][TXT_KEY]["source"]
        src["what_to_show"] = txt_what
        src["what_to_show_note"] = (f"{txt_what}: picked on the 2026-01-19 .. 2026-01-30 overlap by "
                                    f"tools/data/backfill_build.py (see {key}, source.what_to_show_pick)")
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=_plain) + "\n", encoding="utf-8")
    manifest._load.cache_clear()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="backfill_build.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pick")
    p.add_argument("overlap")
    p.add_argument("--txt", default=str(ROOT / TXT_KEY))
    p.add_argument("--out", required=True)
    b = sub.add_parser("build")
    b.add_argument("download")
    b.add_argument("--pick", required=True)
    b.add_argument("--txt", default=str(ROOT / TXT_KEY))
    b.add_argument("--out-dir", default=str(ROOT / "database" / "ohlcv_data"))
    b.add_argument("--update-manifest", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "pick":
        meta, reqs, bars, end = read_jsonl(a.overlap)
        if meta.get("mode") != "probe" or end is None:
            ap.error(f"{a.overlap}: not a complete --probe-overlap file")
        res = pick(meta, reqs, bars, load_txt(Path(a.txt), *OVERLAP))
        Path(a.out).write_text(json.dumps(res, indent=1) + "\n", encoding="utf-8")
        for t, r in res["table"].items():
            print(f"{t:9s} bars {r['bars']:6d}  OHLC exact {r['ohlc_share']:.4%}  "
                  f"+volume {r['ohlc_and_volume_share']:.4%}  errors {r['ibkr_error_codes']}")
        if res["chosen"] is None:
            print(f"no type reaches {PICK_MIN_SHARE:.1%}: STOP, no backfill (docs/BACKFILL_2026_PROPOSAL.md)")
            return 1
        print(f"picked {res['chosen']} (volume reproduced: {res['volume_reproduced']}); wrote {a.out}")
        return 0

    pk = json.loads(Path(a.pick).read_text(encoding="utf-8"))
    if not pk.get("chosen"):
        ap.error(f"{a.pick}: no type was picked; the backfill stops")
    meta, reqs, bars, end = read_jsonl(a.download)
    ref = load_txt(Path(a.txt), *OVERLAP)
    last = (ref["timestamp"].iloc[-1].isoformat(), float(ref["close"].iloc[-1])) if len(ref) else None
    df, checks, fails = build(meta, reqs, bars, end, pk, last)
    name = Path(KEY).name if not fails else Path(KEY).name.replace(".parquet", ".rejected.parquet")
    out = Path(a.out_dir) / name
    if out.exists():
        ap.error(f"{out} exists; refusing to overwrite")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    e = entry(meta, pk, checks, df, out, not fails)
    print(json.dumps(e, indent=1, default=_plain))
    if a.update_manifest:
        write_manifest(KEY if not fails else KEY.replace(".parquet", ".rejected.parquet"), e,
                       pk["chosen"] if not fails else None)
        print(f"manifest updated: {manifest.MANIFEST}", file=sys.stderr)
    if fails:
        print(f"REJECTED, written as {out}: " + "; ".join(fails), file=sys.stderr)
        return 1
    print(f"passed: {out}, {len(df)} bars", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
