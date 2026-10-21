#!/usr/bin/env python3
"""
Deterministic level scanner + decision log.

A MEASUREMENT INSTRUMENT for a human's discretionary judgment. There is no
agent, no LLM, no filtering intelligence anywhere in this loop. It watches price
arrive at pre-defined levels and records that arrival; the human decides.

An alert means "price has ARRIVED at a level, start watching" — NOT "trade now".
Alerts stay open and are referenced by id; a decision may come 40+ minutes later.

Level engine: reused verbatim from notebooks/backtest.ipynb (calibrated on
2023-2025, do not retune). Each level is a row (type, value, active_from,
active_to); active_from is the moment the source period CLOSES, so a level
cannot be touched before it exists — no look-ahead by construction. Building
levels from the full history is therefore safe (proven by the corrupt-future
unit test in --selftest), which keeps the replay and live paths identical.

Replay:   python backend/deterministic_scanner.py --date 2025-06-16
Self-test: python backend/deterministic_scanner.py --selftest
Live:     python backend/deterministic_scanner.py --live | --dry-live

Live bar source: SCANNER_BAR_SOURCE=ibkr (default) | stream.
  ibkr    this process's own IBKR connection (run_live). Unchanged; it is the
          rollback.
  stream  the datafeed stream bars.xauusd.1m via backend.datafeed.bus, consumer group
          "scanner" (run_live_stream). Needs REDIS_URL and DATAFEED_DB. Never
          connects to IBKR: the datafeed feed is the only gateway client.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from backend.levels.build import MIN_BARS_FULL_DAY, SESSION_CARRY_H, SESSIONS, build_levels, session_of  # noqa: F401
from backend.levels.touch import TOL, WINDOW_MIN, Scanner  # noqa: F401
from backend.scanner.market import _market_open, _open_minutes, _utc, _valid_bar  # noqa: F401
from backend.scanner.config import BAR_SOURCES, DB_PATH, PARQUET, RECONNECT_BACKOFF_S, bar_source  # noqa: F401
from backend.scanner.journal.schema import ALERT_STATUSES, JOURNAL_VERSION, PRE_MIGRATION_EPOCH, _migrate_journal, open_log, start_epoch  # noqa: F401
from backend.scanner.journal.writes import MISSED_AFTER_H, TELEGRAM_OFFSET_KEY, _alert_epoch, _journal_new_alert, insert_alert, insert_counterfactual, last_live_bar, load_offset, record_decision, record_outcome, record_progress, save_offset, sweep_auto_missed, undecided_alerts  # noqa: F401
from backend.scanner.journal.outcomes import CF_HORIZONS, OUTCOME_EXIT_TYPES, OUTCOME_MAX_HOLD_MIN, OUTCOME_SETTLE_MIN, alerts_needing_counterfactual, counterfactual, insert_outcome, score_take, sweep_counterfactuals, sweep_outcomes, take_problem, takes_needing_outcome  # noqa: F401
from backend.scanner.telegram import Telegram, TelegramMessage, _handle_reply, _isnum, _send_quietly, format_alert, parse_reply, parse_updates  # noqa: F401
from backend.scanner.engine import AlertSink, LiveLevels, _alert_sender, beat  # noqa: F401
from backend.scanner.sources.replay import load_data, replay_day  # noqa: F401
from backend.scanner.sources.stream import STORE_RETRY_S, STREAM_BATCH, STREAM_BLOCK_MS, STREAM_GROUP, STREAM_STALE_MIN, WARMUP_DAYS, WARMUP_MAX_STALE_MIN, WARMUP_MIN_COVERAGE, StoreNotReady, StreamFeed, StreamStaleness, _only_valid, _store_bars, run_live_stream, stream_warmup, wait_for_store, warmup_start  # noqa: F401
from backend.scanner.selftest import selftest  # noqa: F401


SILENCE_WARN_S = 1800            # connected but no bars this long (market hours) -> Telegram warn


# ── Live loop (reuses tws_connector for the feed, Postgres for level history) ─
def _load_recent_history(symbol: str, days: int, host: str, port: int, client_id: int) -> pd.DataFrame:
    """Recent 1-min bars straight from IBKR (MIDPOINT, same pricing the live feed
    builds from) so the level engine has yesterday's / last week's / today's
    session data to build from. Deliberately NOT the Postgres ohlcv_1min view —
    that stops at the last backfill and would leave the scanner with no levels."""
    from ib_insync import IB, Contract
    ib = IB()
    ib.connect(host, port, clientId=client_id, timeout=10, readonly=True)
    try:
        c = ib.qualifyContracts(Contract(symbol=symbol, secType="CMDTY", exchange="SMART", currency="USD"))[0]
        bars = ib.reqHistoricalData(c, endDateTime="", durationStr=f"{days} D",
                                    barSizeSetting="1 min", whatToShow="MIDPOINT",
                                    useRTH=False, formatDate=2)
    finally:
        ib.disconnect()
    df = pd.DataFrame([(b.date, b.open, b.high, b.low, b.close) for b in bars],
                      columns=["timestamp", "open", "high", "low", "close"]).set_index("timestamp")
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[["open", "high", "low", "close"]].astype(float).sort_index()


def run_live(symbol: str = "XAUUSD", dry: bool = False):
    """Live path: identical scanner code, fed by real IBKR bars.

    dry=False: alerts go to Telegram, TAKE/SKIP replies are logged, MISSED swept.
    dry=True : alerts print to the terminal, no Telegram at all — for watching the
               live feed + scanner without spending messages or needing creds."""
    import os
    import threading
    import time as _time
    from services.tws_connector import TWSConnector

    host = os.environ.get("IB_HOST", "127.0.0.1")
    port = int(os.environ.get("IB_PORT", "7497"))
    client_id = int(os.environ.get("IB_CLIENT_ID", "11"))

    tg = None if dry else Telegram()
    con = open_log(check_same_thread=False)
    epoch = start_epoch(con, "ibkr")
    lock = threading.Lock()

    # history first (separate short-lived connection, distinct clientId). A failure
    # here takes the same crash-only path as a failed tws.connect(): back off, exit,
    # let Docker restart us clean.
    try:
        hist = _load_recent_history(symbol, days=10, host=host, port=port, client_id=client_id + 10)
    except Exception as e:
        print(f"history load failed ({e!r}); backing off then exiting for restart", flush=True)
        _time.sleep(RECONNECT_BACKOFF_S)
        raise SystemExit(1)
    hist = hist[[_valid_bar(*r) for r in hist.itertuples(name=None)]]
    down_from = last_live_bar(con)            # before this process records any progress
    live = LiveLevels(hist, pd.Timestamp.now(tz="UTC"))
    sink = AlertSink(con, epoch, "ibkr", _alert_sender(tg, dry), down_from=down_from,
                     warm_end=hist.index.max() if len(hist) else None)
    sink.startup(live.warmup_alerts)

    hb = {"last": _time.time(), "warned": False}   # last final bar (wall clock)

    def on_bar(_sym, bar):
        if not bar.get("is_final"):
            return
        ts = pd.Timestamp(bar["timestamp"]); ts = ts.tz_convert("UTC") if ts.tz else ts.tz_localize("UTC")
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        if not _valid_bar(ts, o, h, l, c):
            return
        hb["last"] = _time.time()
        with lock:
            for a in live.on_bar(ts, o, h, l, c):
                sink(a)
            sink.progress(ts)

    tws = TWSConnector(host=host, port=port, client_id=client_id, readonly=True)
    # Crash-only design: surface disconnects instead of relying on the connector's
    # in-place reconnect, whose market-data resubscribe is a stub (a silent
    # reconnect would leave us connected with NO bars). On any drop we back off
    # and exit; Docker's restart policy + the entrypoint port-wait do a clean full
    # reconnect + resubscribe via run_live's cold start. One code path to get
    # right (cold start), not two.
    tws.max_retries = 0
    tws.on_bar_update(on_bar)
    if not tws.connect():
        print("could not connect to gateway; backing off then exiting for restart", flush=True)
        _time.sleep(RECONNECT_BACKOFF_S)
        raise SystemExit(1)
    tws.subscribe_bars(symbol, exchange="SMART", sec_type="CMDTY", currency="USD")
    active = sum(1 for L in build_levels(live.hist)
                 if L["active_from"] <= pd.Timestamp.now(tz="UTC") < L["active_to"])
    mode = "DRY (terminal)" if dry else "LIVE (Telegram)"
    print(f"{mode}: scanning {symbol}, {active} levels active, journal epoch {epoch}. "
          f"Ctrl-C to stop.", flush=True)

    offset = load_offset(con)
    last_sweep = 0.0
    heartbeat = os.environ.get("SCANNER_HEARTBEAT")
    try:
        while True:
            beat(heartbeat)
            # Crash-only: a dropped gateway (daily restart / weekly 2FA) surfaces
            # as not-connected. Back off, then exit so Docker restarts us into a
            # clean full reconnect. The backoff stops a fast restart-loop while the
            # gateway is down for minutes.
            if not tws.connected:
                print("gateway connection lost — backing off then exiting so Docker reconnects clean", flush=True)
                try: tws.disconnect()
                except Exception: pass
                _time.sleep(RECONNECT_BACKOFF_S)
                raise SystemExit(1)

            # Heartbeat: connected but no bars for a while during market hours is a
            # silent stall the self-heal won't catch -> warn once via Telegram.
            now = pd.Timestamp.now(tz="UTC")
            if not _market_open(now):
                # silence only counts while the market is open, so the first
                # minutes after a reopen don't read as a 30-min stall
                hb["last"] = _time.time()
            if not dry and _market_open(now) and _time.time() - hb["last"] > SILENCE_WARN_S:
                if not hb["warned"]:
                    try:
                        tg.send(f"scanner: no XAUUSD bars for >{SILENCE_WARN_S // 60} min "
                                f"during market hours — check the feed")
                    except Exception:
                        pass
                    hb["warned"] = True
            else:
                hb["warned"] = False

            if dry:
                _time.sleep(2)
                continue
            polled_from = offset
            try:
                msgs, offset = tg.poll(offset)
            except Exception as e:
                # a Telegram/network hiccup must not kill the feed (it used to:
                # "connection reset by peer" -> crash -> restart gap). offset is
                # unchanged, so unread replies are fetched again next round.
                print(f"telegram poll failed ({e!r}); retrying", flush=True)
                _time.sleep(5)
                msgs = []
            for msg in msgs:
                _handle_reply(con, lock, tg, msg, epoch)
            if msgs or offset != polled_from:
                with lock:
                    save_offset(con, offset)
            if _time.time() - last_sweep > 300:      # every 5 min
                with lock:
                    now = pd.Timestamp.now(tz="UTC")
                    sweep_auto_missed(con, now)
                    # counterfactuals need the future bars, so fill from the
                    # accumulated live frame once each alert's 4h window elapses
                    sweep_counterfactuals(con, live.hist.sort_index(), now)
                    sweep_outcomes(con, live.hist.sort_index(), now)
                last_sweep = _time.time()
    except KeyboardInterrupt:
        print("\nstopping."); tws.disconnect()


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
            run_live_stream(dry=args.dry_live)
        else:
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
