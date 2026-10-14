"""Recorder: bus stream -> store. At-least-once in, exactly-once rows out.

Loop: consume a batch (own unacked entries first, see bus.py), write it in ONE
transaction, COMMIT, and only then ack. The two crash windows:

  crash before COMMIT          the transaction rolls back; the entries are
                               still unacked and are redelivered on restart,
                               so nothing is lost (no gap)
  crash after COMMIT, before   the entries are redelivered and rewritten;
  ack                          insert_bar sees identical values -> "duplicate"
                               no-op, so nothing is doubled

A message that fails validation can never succeed, so it is written to
rejected_messages (stage 'invalid') in the batch's own transaction, counted,
logged and acked with the batch (otherwise it would be redelivered forever).
If the batch rolls back, its rejection rows roll back with it. A store error
rolls the batch back, is counted, and the batch is retried after a pause. An
ack error propagates: crash-only, the restart redelivers.

POISON MESSAGES
    A message that passes validation but makes the store write fail would
    fail its batch forever. Every failed batch counts a failure against each
    of its entries. Once an entry has failed MAX_BATCH_FAILURES times, its
    batch (redelivered whole, since pending entries come first) is reprocessed
    one message per transaction. A message that still fails is written to
    rejected_messages (raw JSON, error, first_seen, stream id) and acked; the
    rest are recorded as usual. If even the rejected_messages row can't be
    written, the store itself is failing: nothing more is acked and the batch
    is retried after a pause, so a broken store never turns into dropped bars.
    Failure counts live in memory; after a restart a batch gets
    MAX_BATCH_FAILURES fresh attempts.
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
MAX_BATCH_FAILURES = 3      # failed attempts of a batch before it is split one per transaction


def _utcnow():
    return datetime.now(timezone.utc)


class Recorder:
    def __init__(self, bus, con, stream: str, consumer: str = "recorder-1", *,
                 count: int = 500, block_ms: int | None = 5000, clock=_utcnow,
                 sleep=_time.sleep) -> None:
        self.bus, self.con, self.stream, self.consumer = bus, con, stream, consumer
        self.count, self.block_ms, self.clock, self._sleep = count, block_ms, clock, sleep
        self.stats = dict(batches=0, inserted=0, duplicate=0, conflict=0, rejected=0,
                          poison=0, split_batches=0, errors=0)
        self.last_bar_ts: str | None = None
        self.last_error: str | None = None
        self._failures: dict[str, int] = {}      # stream id -> failed batch attempts
        self._first_seen: dict[str, str] = {}    # stream id -> time of its first failure

    def step(self) -> int:
        """Handle one batch. Returns the number of entries consumed."""
        batch = self.bus.consume(self.stream, GROUP, self.consumer, count=self.count,
                                 block_ms=self.block_ms)
        if not batch:
            return 0
        if any(self._failures.get(d.id, 0) >= MAX_BATCH_FAILURES for d in batch):
            self._one_by_one(batch)
        else:
            self._whole(batch)
        return len(batch)

    def _record(self, d) -> tuple[str, str | None]:
        """Validate and write one entry inside the open transaction.
        Returns (result, canonical bar_ts or None)."""
        try:
            validate_message(d.msg)
            for f in ("bar_ts", "open", "high", "low", "close"):
                if f not in d.msg:
                    raise ValueError(f"bar message missing {f}")
            ts = schema.canonical(d.msg["bar_ts"])
        except (ValueError, TypeError) as e:
            schema.reject_message(self.con, self.stream, d.id, d.msg, repr(e), "invalid",
                                  self._first_seen.get(d.id) or self.clock())
            log.error("recorder: rejected %s: %s", d.id, e)
            return "rejected", None
        return schema.insert_bar(self.con, d.msg), ts

    def _whole(self, batch) -> None:
        try:
            self.con.execute("BEGIN")
            done = [self._record(d) for d in batch]
            self.con.execute("COMMIT")
        except Exception as e:
            self._rollback()
            now = schema.canonical(self.clock())
            for d in batch:
                self._failures[d.id] = self._failures.get(d.id, 0) + 1
                self._first_seen.setdefault(d.id, now)
            self._error(e, "batch failed, rolled back")
            return
        for d in batch:                                # only after the commit
            self.bus.ack(self.stream, GROUP, d.id)
        self._done(batch, done)
        self.stats["batches"] += 1

    def _one_by_one(self, batch) -> None:
        log.warning("recorder: batch of %d failed %d times; one message per transaction",
                    len(batch), MAX_BATCH_FAILURES)
        self.stats["split_batches"] += 1
        for d in batch:
            try:
                self.con.execute("BEGIN")
                done = self._record(d)
                self.con.execute("COMMIT")
            except Exception as e:
                self._rollback()
                try:
                    self.con.execute("BEGIN")
                    schema.reject_message(self.con, self.stream, d.id, d.msg, repr(e), "poison",
                                          self._first_seen.get(d.id) or self.clock())
                    self.con.execute("COMMIT")
                except Exception as e2:
                    self._rollback()
                    self._error(e2, "could not record poison message either, store failing")
                    return
                log.error("recorder: poison message %s moved to rejected_messages: %r", d.id, e)
                done = ("poison", None)
            self.bus.ack(self.stream, GROUP, d.id)       # only after its commit
            self._done([d], [done])

    def _done(self, entries, done) -> None:
        for d, (result, ts) in zip(entries, done):
            self.stats[result] += 1
            self._failures.pop(d.id, None)
            self._first_seen.pop(d.id, None)
            if ts and (self.last_bar_ts is None or ts > self.last_bar_ts):
                self.last_bar_ts = ts

    def _rollback(self) -> None:
        if self.con.in_transaction:
            self.con.execute("ROLLBACK")

    def _error(self, e: Exception, what: str) -> None:
        self.stats["errors"] += 1
        self.last_error = repr(e)
        log.exception("recorder: %s; retrying in %ss", what, RETRY_S)
        self._sleep(RETRY_S)

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
