"""Store interface. The SQLite implementation is agents/store/sqlite.py, and
MemoryStore is for tests. Both are append-only: nothing is updated or deleted."""

from __future__ import annotations

from typing import Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict

from agents.schemas import Decision, DiscussionResult, Snapshot, TriggerEvent, UtcDatetime, Vote


class RunRecord(BaseModel):
    """One row per trigger: done, or skipped with the reason."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    run_id: str
    trigger: TriggerEvent
    as_of: UtcDatetime
    status: Literal["done", "skipped"]
    skip_reason: str | None = None
    snapshot_hash: str | None = None     # None when no snapshot was saved (stale)
    config_json: str
    config_hash: str
    code_version: str
    epoch_id: str | None = None          # agents/epoch.py; set by the graph
    epoch_inputs_json: str | None = None # what the epoch id is the hash of (for `python -m agents.epoch --db`)


class AgentStore(Protocol):
    def save_snapshot(self, snapshot: Snapshot) -> None:
        """Called before any agent node runs. The same content again is a no-op."""

    def save_run(self, run: RunRecord, votes: Sequence[Vote], decisions: Sequence[Decision],
                 discussion: DiscussionResult | None = None) -> None:
        """Everything else about a run, in one transaction, written once."""


class StoreConflict(RuntimeError):
    pass


def _check_types(run, votes, decisions, discussion) -> None:
    if not (isinstance(run, RunRecord) and all(isinstance(v, Vote) for v in votes)
            and all(isinstance(d, Decision) for d in decisions)
            and (discussion is None or isinstance(discussion, DiscussionResult))):
        raise TypeError("save_run takes a RunRecord, Votes, Decisions and an optional DiscussionResult")
