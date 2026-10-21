"""The scanner's command line: python -m backend.scanner (flags as before).

Replay:    python -m backend.scanner --date 2025-06-16 [--db FILE]
Self-test: python -m backend.scanner --selftest
Live:      python -m backend.scanner --live | --dry-live

The live bar source is SCANNER_BAR_SOURCE (ibkr, the default, or stream); the
live loop is imported only when it runs, so --date and --selftest need only
pandas (no ib_insync, no backend.datafeed).
"""
from __future__ import annotations

import argparse

import pandas as pd

from backend.levels.touch import TOL, WINDOW_MIN
from backend.scanner.config import bar_source
from backend.scanner.journal.schema import open_log
from backend.scanner.selftest import selftest
from backend.scanner.sources.replay import replay_day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="UTC day to replay, e.g. 2025-06-16")
    ap.add_argument("--db", default=None, help="write alerts+counterfactuals to this sqlite file")
    ap.add_argument("--live", action="store_true", help="run live + Telegram; bars from SCANNER_BAR_SOURCE (ibkr: needs TWS; stream: needs REDIS_URL + DATAFEED_DB)")
    ap.add_argument("--dry-live", action="store_true", help="run live but print alerts to terminal (no Telegram)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        raise SystemExit(0 if selftest() else 1)
    if args.live or args.dry_live:
        if bar_source() == "stream":
            from backend.scanner.sources.stream import run_live_stream
            run_live_stream(dry=args.dry_live)
        else:
            from backend.scanner.sources.ibkr import run_live      # imports ib_insync: ibkr live mode only
            run_live(dry=args.dry_live)
        return
    if not args.date:
        ap.error("give --date YYYY-MM-DD, --live, --dry-live, or --selftest")

    con = open_log(args.db) if args.db else None
    out = replay_day(args.date, con)
    if out.empty:
        print(f"{args.date}: 0 alerts")
        return
    show = ["alert_id", "alert_ts_utc", "session", "level_types", "level_price",
            "price_at_alert", "confluence_count", "mfe_up_1h", "mfe_dn_1h", "mfe_up_4h", "mfe_dn_4h"]
    print(f"\n{args.date} — {len(out)} alerts (first-touch-only, {WINDOW_MIN}-min collapse, ${TOL:.2f} tol)\n")
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(out[show].to_string(index=False))


if __name__ == "__main__":
    main()
