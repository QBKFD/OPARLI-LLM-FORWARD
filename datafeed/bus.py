"""Message bus: Redis Streams in production, an in-memory twin for tests.

Semantics (identical for both implementations):

- publish(stream, msg) appends msg and returns its id. Streams are trimmed to
  roughly `maxlen` entries (Redis: XADD MAXLEN ~, so at least maxlen are kept;
  in-memory: exactly maxlen). The bus is a transport, not storage: the
  recorder's store is the durable record.
- consume(stream, group, consumer) returns a list of Delivery(id, msg). The
  group is created on first use, starting at the beginning of the stream.
  On every call the consumer FIRST gets back its own delivered-but-unacked
  entries (oldest first), and only when it has none does it get new entries.
  So a consumer that crashes before acking sees the same entries again when it
  restarts under the same consumer name: at-least-once delivery.
- ack(stream, group, id) marks an entry handled. Call it only after the
  entry's effects are durable.
- An unacked entry that was trimmed away before redelivery cannot be
  recovered. It is acked, counted in `lost`, and logged, never silently
  dropped.

Messages are JSON-serialised whole into one field, so types round-trip
(int stays int, float stays float).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_MAXLEN = 100_000   # ~70 days of 1-min bars


@dataclass(frozen=True)
class Delivery:
    id: str
    msg: dict


class Bus(ABC):
    def __init__(self) -> None:
        self.lost = 0   # unacked entries trimmed before redelivery

    @abstractmethod
    def publish(self, stream: str, msg: dict) -> str: ...

    @abstractmethod
    def consume(self, stream: str, group: str, consumer: str,
                count: int = 100, block_ms: int | None = None) -> list[Delivery]: ...

    @abstractmethod
    def ack(self, stream: str, group: str, id: str) -> None: ...

    def _lost(self, stream: str, group: str, id: str) -> None:
        self.lost += 1
        log.error("bus: unacked entry %s on %s/%s was trimmed before redelivery",
                  id, stream, group)


class InMemoryBus(Bus):
    """Single-process stand-in with the same delivery semantics as Redis.

    A "crash" is simulated by simply not acking and calling consume() again
    with the same consumer name (as a restarted process would).
    """

    def __init__(self, maxlen: int = DEFAULT_MAXLEN) -> None:
        super().__init__()
        self.maxlen = maxlen
        self._seq: dict[str, int] = {}
        self._entries: dict[str, dict[str, dict]] = {}          # stream -> id -> msg (insertion-ordered)
        self._groups: dict[tuple[str, str], dict] = {}          # (stream, group) -> state

    @staticmethod
    def _n(id: str) -> int:
        return int(id.split("-")[0])

    def publish(self, stream: str, msg: dict) -> str:
        n = self._seq.get(stream, 0) + 1
        self._seq[stream] = n
        id = f"{n}-0"
        entries = self._entries.setdefault(stream, {})
        entries[id] = json.loads(json.dumps(msg))   # same round-trip as Redis
        while len(entries) > self.maxlen:
            del entries[next(iter(entries))]
        return id

    def _group(self, stream: str, group: str) -> dict:
        return self._groups.setdefault((stream, group), {"last": 0, "pending": {}})

    def consume(self, stream, group, consumer, count=100, block_ms=None):
        g = self._group(stream, group)
        entries = self._entries.get(stream, {})
        mine = sorted((id for id, c in g["pending"].items() if c == consumer), key=self._n)
        if mine:
            out = []
            for id in mine:
                if id in entries:
                    out.append(Delivery(id, json.loads(json.dumps(entries[id]))))
                else:
                    self._lost(stream, group, id)
                    del g["pending"][id]
                if len(out) >= count:
                    break
            if out:
                return out
        out = []
        for id, msg in entries.items():
            if self._n(id) <= g["last"]:
                continue
            g["last"] = self._n(id)
            g["pending"][id] = consumer
            out.append(Delivery(id, json.loads(json.dumps(msg))))
            if len(out) >= count:
                break
        return out

    def ack(self, stream, group, id):
        self._group(stream, group)["pending"].pop(id, None)

    def length(self, stream: str) -> int:
        return len(self._entries.get(stream, {}))


class RedisStreamBus(Bus):
    """Redis Streams: XADD MAXLEN ~, XREADGROUP, XACK.

    Connection comes from REDIS_URL (env) unless a client is passed in.
    """

    FIELD = "json"

    def __init__(self, client=None, url: str | None = None,
                 maxlen: int = DEFAULT_MAXLEN) -> None:
        super().__init__()
        if client is None:
            import os
            import redis
            url = url or os.environ.get("REDIS_URL")
            if not url:
                raise RuntimeError("set REDIS_URL")
            client = redis.Redis.from_url(url, decode_responses=True)
        self.r = client
        self.maxlen = maxlen
        self._known_groups: set[tuple[str, str]] = set()

    def publish(self, stream, msg):
        return self.r.xadd(stream, {self.FIELD: json.dumps(msg)},
                           maxlen=self.maxlen, approximate=True)

    def _ensure_group(self, stream, group):
        if (stream, group) in self._known_groups:
            return
        import redis
        try:
            self.r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
        self._known_groups.add((stream, group))

    def _read(self, stream, group, consumer, start, count, block_ms):
        resp = self.r.xreadgroup(group, consumer, {stream: start}, count=count,
                                 block=block_ms)
        out = []
        for _stream, items in resp or []:
            for id, fields in items:
                if not fields:          # pending entry whose data was trimmed
                    self._lost(stream, group, id)
                    self.r.xack(stream, group, id)
                    continue
                out.append(Delivery(id, json.loads(fields[self.FIELD])))
        return out, bool(resp and resp[0][1])

    def consume(self, stream, group, consumer, count=100, block_ms=None):
        self._ensure_group(stream, group)
        # own pending first ("0"), new entries only when nothing is pending (">")
        out, had_pending = self._read(stream, group, consumer, "0", count, None)
        if out or had_pending:
            return out
        out, _ = self._read(stream, group, consumer, ">", count, block_ms)
        return out

    def ack(self, stream, group, id):
        self.r.xack(stream, group, id)
