"""Snapshot builder: what was knowable at t, frozen and hashed.

Time. as_of = floor(t, 1 min), and bars come from
datafeed.store.query.bars_before(symbol, as_of, ...), so every bar has
bar_ts < as_of <= t. Flooring matters: for t = 12:00:30, `bar_ts < t` would
include the 12:00 bar, which is still forming at t (and already complete in a
replay store, i.e. lookahead).

Sources.
- live:   kinds ("live", "history"), known_by = t: only bars the system had
          received by t, so a later backfill can't change a rebuilt snapshot.
- replay: kinds ("replay",), known_by = None: replay ingest_ts is the time the
          replay ran, so known_by would hide every bar.

Seal. as_of after datafeed's SEAL_START (2026-10-01 00:00 UTC, tested equal to
the research seal) raises SealedError. There's no override.

Stale. If the newest 1m bar completed more than max_staleness before as_of (or
there's no bar at all), the result is Stale(reason) and nothing is saved. The
caller skips the run and logs why.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from datafeed.ingest.xauusd_bars import SEAL_START
from datafeed.store.query import bars_before

from agents.config import SnapshotConfig
from agents.resample import bar_from_row, bucket_start, resample
from agents.schemas import TIMEFRAMES, Snapshot

log = logging.getLogger(__name__)

ONE_MIN = timedelta(minutes=1)


class SealedError(RuntimeError):
    pass


@dataclass(frozen=True)
class Stale:
    as_of: datetime
    newest_bar_ts: datetime | None
    reason: str


class SnapshotSink(Protocol):
    def save_snapshot(self, snapshot: Snapshot) -> None: ...


def floor_minute(t: datetime) -> datetime:
    if t.tzinfo is None:
        raise ValueError(f"naive t {t!r}")
    return t.astimezone(timezone.utc).replace(second=0, microsecond=0)


def source_for(mode: str, t: datetime) -> tuple[tuple[str, ...], datetime | None]:
    if mode == "live":
        return ("live", "history"), t.astimezone(timezone.utc)
    if mode == "replay":
        return ("replay",), None
    raise ValueError(f"unknown mode {mode!r}")


def build_snapshot(t: datetime, cfg: SnapshotConfig, db) -> Snapshot | Stale:
    as_of = floor_minute(t)
    if as_of > SEAL_START:
        raise SealedError(f"as_of {as_of.isoformat()} is after the seal {SEAL_START.isoformat()}; no override")
    kinds, known_by = source_for(cfg.mode, t)
    largest = max(cfg.keep, key=TIMEFRAMES.__getitem__)
    start = bucket_start(as_of - cfg.fetch_lookback, largest)
    rows = bars_before(cfg.symbol, as_of, as_of - start, db=db, known_by=known_by, kinds=kinds)
    if not rows:
        return Stale(as_of, None, f"no {'/'.join(kinds)} bars for {cfg.symbol} in "
                                  f"[{start.isoformat()}, {as_of.isoformat()})")
    bars = [bar_from_row(r) for r in rows]
    newest = bars[-1].ts
    age = as_of - (newest + ONE_MIN)
    if age > cfg.max_staleness:
        return Stale(as_of, newest, f"stale: newest 1m bar {newest.isoformat()} completed {age} before "
                                    f"as_of {as_of.isoformat()} (max {cfg.max_staleness})")
    series = {tf: resample(bars, tf, as_of)[-cfg.keep[tf]:] for tf in TIMEFRAMES if tf in cfg.keep}
    return Snapshot(symbol=cfg.symbol, as_of=as_of, kinds=kinds, known_by=known_by, bars=series,
                    newest_bar_ts=newest)


def prepare_snapshot(t: datetime, cfg: SnapshotConfig, db, sink: SnapshotSink) -> Snapshot | Stale:
    """Build, then save before anything else sees it. Stale: log, save nothing."""
    out = build_snapshot(t, cfg, db)
    if isinstance(out, Stale):
        log.warning("run skipped at as_of %s: %s", out.as_of.isoformat(), out.reason)
        return out
    sink.save_snapshot(out)
    return out
