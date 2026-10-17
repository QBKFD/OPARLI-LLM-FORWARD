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

Access rules (check_access), checked before any bar is read:
- replay: as_of after datafeed's SEAL_START (2026-10-01 00:00 UTC, tested
  equal to the research seal) raises SealedError. There's no override, with or
  without an experiment.
- live: allowed only with snapshot.experiment_id naming an existing PREREG
  (agents/experiments/<id>/PREREG.md, see agents.prereg), and only when t is
  within 120 s of the wall clock. Otherwise it raises ExperimentError or
  LiveClockError. So live mode can't be used to read sealed history at an
  arbitrary t.
- Any experiment_id given, in either mode, must have its PREREG.

Stale (owner decision 2026-10-14). Staleness is measured against the last
market-open minute before as_of, not against the clock. The age is
last_open_minute(as_of) - newest 1m bar_ts, and the run is stale if
age > max_staleness. While the market is open, last_open_minute is as_of - 1
min, so nothing changes there. Across a daily break or a weekend, the minutes
the market was closed don't count: the trigger at the reopen (newest bar =
the last minute before the close) is fresh. "Open" is datafeed's _market_open
(the scanner's rule; holidays aren't modelled). No bars at all is also stale.
A stale result is Stale(reason): nothing is saved, and the caller skips the run
and logs why.

Slots (schema 3). The alert's Telegram facts come from the trigger payload's
"alert" entry, allowlisted field by field (ALERT_FIELDS); the rest of the
payload never enters the snapshot. Levels, features, metrics, events and
charts have no builder here yet: each stays empty and is named in
`unavailable` with the reason, so a prompt that renders it says so instead of
showing an empty list as if it were data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol

from pydantic import ValidationError

from datafeed.ingest.xauusd_bars import SEAL_START, _market_open
from datafeed.store.query import bars_before

from agents.config import SnapshotConfig
from agents.prereg import ExperimentError, require_prereg
from agents.resample import bar_from_row, bucket_start, resample
from agents.schemas import ALERT_FIELDS, TIMEFRAMES, AlertFacts, Snapshot

log = logging.getLogger(__name__)

ONE_MIN = timedelta(minutes=1)
LIVE_MAX_SKEW = timedelta(seconds=120)
# Slots without a builder in this package yet, and why they are empty.
NOT_BUILT = {
    "levels": "no level source yet",
    "features": "no feature builder yet",
    "metrics": "no metric table yet",
    "events": "no calendar store yet",
    "charts": "no chart renderer yet",
}
CLOSED_SEARCH_LIMIT = timedelta(days=9)     # the longest closure is a weekend (+ holidays, unmodelled)


class SealedError(RuntimeError):
    pass


class LiveClockError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def last_open_minute(as_of: datetime) -> datetime:
    """The latest minute m < as_of in which the market was open.

    _market_open closes whole NY hours (the daily 17:00 break, the weekend). NY
    offsets are whole hours, so a closed minute means its whole UTC hour is
    closed, and the search steps back an hour at a time."""
    m = as_of - ONE_MIN
    while not _market_open(m):
        m = m.replace(minute=0, second=0, microsecond=0) - ONE_MIN
        if as_of - m > CLOSED_SEARCH_LIMIT:
            raise RuntimeError(f"no market-open minute in the {CLOSED_SEARCH_LIMIT} before {as_of.isoformat()}")
    return m


def alert_from_payload(payload: Mapping[str, Any] | None) -> AlertFacts | str:
    """The alert facts from a trigger payload's "alert" entry, or the reason
    there are none. Only ALERT_FIELDS are read. A journal row's naive
    `alert_ts_utc` ("YYYY-MM-DD HH:MM:SS", UTC) and comma-separated
    `level_types` are accepted as the scanner writes them."""
    raw = (payload or {}).get("alert")
    if raw is None:
        return "the trigger has no alert"
    if not isinstance(raw, Mapping):
        return f"trigger alert is a {type(raw).__name__}, not a mapping"
    data = {k: raw[k] for k in ALERT_FIELDS if k in raw}
    if "alert_ts" not in data and isinstance(raw.get("alert_ts_utc"), str):
        try:
            data["alert_ts"] = datetime.fromisoformat(raw["alert_ts_utc"]).replace(tzinfo=timezone.utc)
        except ValueError:
            return f"trigger alert: bad alert_ts_utc {raw['alert_ts_utc']!r}"
    if isinstance(data.get("level_types"), str):
        data["level_types"] = tuple(x for x in data["level_types"].split(",") if x)
    try:
        return AlertFacts.model_validate(data)
    except ValidationError as e:
        err = e.errors()[0]
        return f"trigger alert: {'.'.join(map(str, err['loc']))}: {err['msg']}"


def check_access(t: datetime, cfg: SnapshotConfig, now=_utcnow) -> None:
    """Seal and live-mode rules. Raises; never skips."""
    as_of = floor_minute(t)
    if cfg.experiment_id is not None:
        require_prereg(cfg.experiment_id)
    if cfg.mode == "replay":
        if as_of > SEAL_START:
            raise SealedError(f"as_of {as_of.isoformat()} is after the seal {SEAL_START.isoformat()}; "
                              "replay has no override")
        return
    if cfg.experiment_id is None:
        raise ExperimentError("live mode needs snapshot.experiment_id with a PREREG under agents/experiments/")
    wall = now()
    if abs(t - wall) > LIVE_MAX_SKEW:
        raise LiveClockError(f"live t {t.isoformat()} is {abs(t - wall)} from the wall clock "
                             f"{wall.isoformat()} (max {LIVE_MAX_SKEW})")


def build_snapshot(t: datetime, cfg: SnapshotConfig, db, now=_utcnow,
                   payload: Mapping[str, Any] | None = None) -> Snapshot | Stale:
    """now: the wall clock (injectable for tests); used by live mode only.
    payload: the trigger payload; only its allowlisted alert facts are used."""
    as_of = floor_minute(t)
    check_access(t, cfg, now)
    kinds, known_by = source_for(cfg.mode, t)
    largest = max(cfg.keep, key=TIMEFRAMES.__getitem__)
    start = bucket_start(as_of - cfg.fetch_lookback, largest)
    rows = bars_before(cfg.symbol, as_of, as_of - start, db=db, known_by=known_by, kinds=kinds)
    if not rows:
        return Stale(as_of, None, f"no {'/'.join(kinds)} bars for {cfg.symbol} in "
                                  f"[{start.isoformat()}, {as_of.isoformat()})")
    bars = [bar_from_row(r) for r in rows]
    newest = bars[-1].ts
    last_open = last_open_minute(as_of)
    age = last_open - newest
    if age > cfg.max_staleness:
        return Stale(as_of, newest, f"stale: newest 1m bar {newest.isoformat()} is {age} behind the last "
                                    f"market-open minute {last_open.isoformat()} (max {cfg.max_staleness})")
    series = {tf: resample(bars, tf, as_of)[-cfg.keep[tf]:] for tf in TIMEFRAMES if tf in cfg.keep}
    alert = alert_from_payload(payload)
    unavailable = dict(NOT_BUILT)
    if isinstance(alert, str):
        unavailable["alert"] = alert
    return Snapshot(symbol=cfg.symbol, as_of=as_of, kinds=kinds, known_by=known_by, bars=series,
                    newest_bar_ts=newest, alert=alert if isinstance(alert, AlertFacts) else None,
                    unavailable=unavailable, experiment_id=cfg.experiment_id)


def prepare_snapshot(t: datetime, cfg: SnapshotConfig, db, sink: SnapshotSink, now=_utcnow,
                     payload: Mapping[str, Any] | None = None) -> Snapshot | Stale:
    """Build, then save before anything else sees it. Stale: log, save nothing."""
    out = build_snapshot(t, cfg, db, now, payload)
    if isinstance(out, Stale):
        log.warning("run skipped at as_of %s: %s", out.as_of.isoformat(), out.reason)
        return out
    sink.save_snapshot(out)
    return out
