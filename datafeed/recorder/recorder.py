"""Recorder: bus stream -> store. At-least-once in, exactly-once rows out.

Loop: consume a batch (own unacked entries first, see bus.py), write it in ONE
transaction, COMMIT, and only then ack. The two crash windows:

  crash before COMMIT          the transaction rolls back; the entries are
                               still unacked and are redelivered on restart,
                               so nothing is lost (no gap)
  crash after COMMIT, before   the entries are redelivered and rewritten;
  ack                          insert_bar sees identical values -> "duplicate"
                               no-op, so nothing is doubled

A message that fails validation can never succeed, so it is counted, logged
and acked (otherwise it would be redelivered forever). A store error rolls
the batch back, is counted, and the batch is retried after a pause. An ack
error propagates: crash-only, the restart redelivers.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timezone

from datafeed.ingest.base import parse_utc, validate_message
from datafeed.store import schema

log = logging.getLogger(__name__)

GROUP = "recorder"
RETRY_S = 5.0


def _utcnow():
    return datetime.now(timezone.utc)


class Recorder:
    def __init__(self, bus, con, stream: str, consumer: str = "recorder-1", *,
                 count: int = 500, block_ms: int | None = 5000, clock=_utcnow,
                 sleep=_time.sleep) -> None:
        self.bus, self.con, self.stream, self.consumer = bus, con, stream, consumer
        self.count, self.block_ms, self.clock, self._sleep = count, block_ms, clock, sleep
        self.stats = dict(batches=0, inserted=0, duplicate=0, conflict=0, rejected=0, errors=0)
        self.last_bar_ts: str | None = None
        self.last_error: str | None = None

    def step(self) -> int:
        """Handle one batch. Returns the number of entries consumed."""
        batch = self.bus.consume(self.stream, GROUP, self.consumer, count=self.count,
                                 block_ms=self.block_ms)
        if not batch:
            return 0
        results, newest = [], None
        try:
            self.con.execute("BEGIN")
            for d in batch:
                try:
                    validate_message(d.msg)
                    for f in ("bar_ts", "open", "high", "low", "close"):
                        if f not in d.msg:
                            raise ValueError(f"bar message missing {f}")
                    ts = schema.canonical(d.msg["bar_ts"])
                except (ValueError, TypeError) as e:
                    results.append("rejected")
                    log.error("recorder: rejected %s: %s", d.id, e)
                    continue
                results.append(schema.insert_bar(self.con, d.msg))
                newest = ts if newest is None or ts > newest else newest
            self.con.execute("COMMIT")
        except Exception as e:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
            self.stats["errors"] += 1
            self.last_error = repr(e)
            log.exception("recorder: batch failed, rolled back; retrying in %ss", RETRY_S)
            self._sleep(RETRY_S)
            return len(batch)
        for d in batch:                                # only after the commit
            self.bus.ack(self.stream, GROUP, d.id)
        for r in results:
            self.stats[r] += 1
        self.stats["batches"] += 1
        if newest and (self.last_bar_ts is None or newest > self.last_bar_ts):
            self.last_bar_ts = newest
        return len(batch)

    def run(self, max_batches: int | None = None, stop_when_idle: bool = False) -> None:
        n = 0
        while max_batches is None or n < max_batches:
            got = self.step()
            n += 1
            if not got and stop_when_idle:
                return

    def health(self) -> dict:
        lag = None
        if self.last_bar_ts:
            # a bar completes one minute after its bar_ts; lag counts from then
            lag = (self.clock() - parse_utc(self.last_bar_ts)).total_seconds() - 60
        return {"stream": self.stream, "consumer": self.consumer,
                "last_recorded_bar_ts": self.last_bar_ts,
                "lag_s": None if lag is None else round(lag, 1),
                "error_count": self.stats["errors"], "last_error": self.last_error,
                **self.stats}
