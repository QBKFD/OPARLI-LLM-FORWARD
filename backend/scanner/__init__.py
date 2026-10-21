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

Replay:   python -m backend.scanner --date 2025-06-16
Self-test: python -m backend.scanner --selftest
Live:     python -m backend.scanner --live | --dry-live

Live bar source: SCANNER_BAR_SOURCE=ibkr (default) | stream.
  ibkr    this process's own IBKR connection (run_live). Unchanged; it is the
          rollback.
  stream  the datafeed stream bars.xauusd.1m via backend.datafeed.bus, consumer group
          "scanner" (run_live_stream). Needs REDIS_URL and DATAFEED_DB. Never
          connects to IBKR: the datafeed feed is the only gateway client.

The package: backend/levels (the pure level engine), and here config, market,
engine, telegram, journal/ (schema, writes, outcomes, summary), sources/
(replay, stream, ibkr), selftest and __main__ (the CLI). This module gives the
names the former backend/deterministic_scanner.py had, from where they now
live. They load on first use, so `import backend.scanner` imports nothing:
ib_insync loads only with the ibkr names, backend.datafeed only when the
stream path runs.
"""
from __future__ import annotations

import importlib

_MODULES = {
    "backend.levels.build": ("build_levels", "MIN_BARS_FULL_DAY", "SESSION_CARRY_H", "session_of", "SESSIONS"),
    "backend.levels.touch": ("Scanner", "TOL", "WINDOW_MIN"),
    "backend.scanner.config": ("bar_source", "BAR_SOURCES", "DB_PATH", "PARQUET", "RECONNECT_BACKOFF_S"),
    "backend.scanner.market": ("_market_open", "_open_minutes", "_utc", "_valid_bar"),
    "backend.scanner.journal.schema": ("_migrate_journal", "ALERT_STATUSES", "JOURNAL_VERSION", "open_log", "PRE_MIGRATION_EPOCH", "start_epoch"),
    "backend.scanner.journal.writes": ("_alert_epoch", "_journal_new_alert", "insert_alert", "insert_counterfactual", "last_live_bar", "load_offset", "MISSED_AFTER_H", "record_decision", "record_outcome", "record_progress", "save_offset", "sweep_auto_missed", "TELEGRAM_OFFSET_KEY", "undecided_alerts"),
    "backend.scanner.journal.outcomes": ("alerts_needing_counterfactual", "CF_HORIZONS", "counterfactual", "insert_outcome", "OUTCOME_EXIT_TYPES", "OUTCOME_MAX_HOLD_MIN", "OUTCOME_SETTLE_MIN", "score_take", "sweep_counterfactuals", "sweep_outcomes", "take_problem", "takes_needing_outcome"),
    "backend.scanner.telegram": ("_handle_reply", "_isnum", "_send_quietly", "format_alert", "parse_reply", "parse_updates", "Telegram", "TelegramMessage"),
    "backend.scanner.engine": ("_alert_sender", "AlertSink", "beat", "LiveLevels"),
    "backend.scanner.sources.replay": ("load_data", "replay_day"),
    "backend.scanner.sources.stream": ("_only_valid", "_store_bars", "run_live_stream", "STORE_RETRY_S", "StoreNotReady", "STREAM_BATCH", "STREAM_BLOCK_MS", "STREAM_GROUP", "STREAM_STALE_MIN", "stream_warmup", "StreamFeed", "StreamStaleness", "wait_for_store", "WARMUP_DAYS", "WARMUP_MAX_STALE_MIN", "WARMUP_MIN_COVERAGE", "warmup_start"),
    "backend.scanner.sources.ibkr": ("_load_recent_history", "run_live", "SILENCE_WARN_S", "TWSConnector"),
    "backend.scanner.selftest": ("selftest",),
    "backend.scanner.__main__": ("main",),
}
_WHERE = {name: module for module, names in _MODULES.items() for name in names}
__all__ = sorted(_WHERE)


def __getattr__(name: str):
    module = _WHERE.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)


def __dir__():
    return __all__
