"""The LLM ledger: every attempt (llm_calls) and the response cache
(llm_responses). Both are append-only.

- **The cache is keyed by prompt_hash.** A response is stored before it is
  used, and the stored copy is what gets used, so a cache-only rerun
  reproduces it byte for byte. If two writers race on one hash, the first
  stored response wins.
- **llm_calls has one row per attempt, cache hits included** (`cached = 1`).
  It references the snapshot by hash, because calls happen before the run's
  own row is written. The budget counts the attempts of a UTC day, cache
  hits excluded.

SqliteLedger shares the agent store's connection and lock (store migration
4). MemoryLedger is the same contract in memory, for tests.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Protocol

from agents.hashing import canonical_json
from agents.llm.client import LLMResponse

if TYPE_CHECKING:
    from agents.store.sqlite import SqliteAgentStore


@dataclass(frozen=True)
class CallRecord:
    call_id: str
    snapshot_hash: str
    agent: str
    round: int
    mode: str                      # live | rerun
    attempt: int                   # 1-based; a cache hit is attempt 0
    model_requested: str
    effort: str
    max_tokens: int
    prompt_hash: str
    cached: bool
    utc_day: str                   # YYYY-MM-DD of created_ts
    created_ts: str
    model_returned: str | None = None
    prompt_file_sha256: str | None = None
    config_sha256: str | None = None
    epoch_id: str | None = None
    request_id: str | None = None
    stop_reason: str | None = None
    refusal_category: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    cost_usd: float | None = None


class LLMLedger(Protocol):
    def get_response(self, prompt_hash: str) -> LLMResponse | None: ...

    def put_response(self, prompt_hash: str, response: LLMResponse, created_ts: str) -> LLMResponse:
        """Store, unless the hash is already stored; return the stored response."""

    def record_call(self, rec: CallRecord) -> None: ...

    def attempts_on(self, utc_day: str) -> int:
        """Attempts sent on that UTC day (attempt >= 1): cache hits and rerun
        misses (attempt 0) don't count."""


class MemoryLedger:
    def __init__(self) -> None:
        self.responses: dict[str, LLMResponse] = {}
        self.calls: list[CallRecord] = []
        self._lock = threading.Lock()

    def get_response(self, prompt_hash: str) -> LLMResponse | None:
        with self._lock:
            return self.responses.get(prompt_hash)

    def put_response(self, prompt_hash: str, response: LLMResponse, created_ts: str) -> LLMResponse:
        with self._lock:
            return self.responses.setdefault(prompt_hash, response)

    def record_call(self, rec: CallRecord) -> None:
        with self._lock:
            if any(c.call_id == rec.call_id for c in self.calls):
                raise ValueError(f"call {rec.call_id} already recorded")
            self.calls.append(rec)

    def attempts_on(self, utc_day: str) -> int:
        with self._lock:
            return sum(1 for c in self.calls if c.utc_day == utc_day and not c.cached and c.attempt >= 1)


_RESPONSE_COLS = ("prompt_hash", "model", "request_id", "stop_reason", "refusal_category", "text", "usage_json",
                  "response_json", "latency_ms", "created_ts")


class SqliteLedger:
    """On the agent store's connection (agents/store/sqlite.py, migration 4)."""

    def __init__(self, store: "SqliteAgentStore") -> None:
        self.con: sqlite3.Connection = store.con
        self.lock = store.lock

    def get_response(self, prompt_hash: str) -> LLMResponse | None:
        with self.lock:
            row = self.con.execute(f"SELECT {', '.join(_RESPONSE_COLS)} FROM llm_responses WHERE prompt_hash = ?",
                                   (prompt_hash,)).fetchone()
        if row is None:
            return None
        return LLMResponse(text=row["text"], model=row["model"], request_id=row["request_id"],
                           stop_reason=row["stop_reason"], refusal_category=row["refusal_category"],
                           usage=json.loads(row["usage_json"]), raw_json=row["response_json"],
                           latency_ms=row["latency_ms"])

    def put_response(self, prompt_hash: str, response: LLMResponse, created_ts: str) -> LLMResponse:
        with self.lock:
            self.con.execute(
                f"INSERT OR IGNORE INTO llm_responses ({', '.join(_RESPONSE_COLS)})"
                f" VALUES ({', '.join('?' * len(_RESPONSE_COLS))})",
                (prompt_hash, response.model, response.request_id, response.stop_reason, response.refusal_category,
                 response.text, canonical_json(response.usage), response.raw_json, response.latency_ms, created_ts))
        stored = self.get_response(prompt_hash)
        assert stored is not None
        return stored

    def record_call(self, rec: CallRecord) -> None:
        d = asdict(rec)
        d["cached"] = int(rec.cached)
        cols = list(d)
        with self.lock:
            self.con.execute(f"INSERT INTO llm_calls ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [d[c] for c in cols])

    def attempts_on(self, utc_day: str) -> int:
        with self.lock:
            return int(self.con.execute("SELECT COUNT(*) FROM llm_calls WHERE utc_day = ? AND cached = 0 AND attempt >= 1",
                                        (utc_day,)).fetchone()[0])


__all__ = ["CallRecord", "LLMLedger", "MemoryLedger", "SqliteLedger"]
