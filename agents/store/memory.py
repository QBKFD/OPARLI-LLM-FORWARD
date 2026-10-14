"""In-memory AgentStore for tests. Same append-only rules as SQLite."""

from __future__ import annotations

from agents.store.base import RunRecord, StoreConflict, _check_types


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict = {}
        self.runs: dict = {}          # run_id -> {"run", "votes", "decisions", "debate"}

    def save_snapshot(self, snapshot) -> None:
        old = self.snapshots.get(snapshot.hash)
        if old is not None and old != snapshot:
            raise StoreConflict(f"snapshot {snapshot.hash} exists with other content")
        self.snapshots[snapshot.hash] = snapshot

    def save_run(self, run: RunRecord, votes, decisions, debate=None) -> None:
        _check_types(run, votes, decisions, debate)
        if run.run_id in self.runs:
            raise StoreConflict(f"run {run.run_id} already stored")
        if run.snapshot_hash is not None and run.snapshot_hash not in self.snapshots:
            raise StoreConflict(f"run {run.run_id}: snapshot {run.snapshot_hash} was never saved")
        self.runs[run.run_id] = {"run": run, "votes": tuple(votes), "decisions": tuple(decisions),
                                 "debate": debate}
