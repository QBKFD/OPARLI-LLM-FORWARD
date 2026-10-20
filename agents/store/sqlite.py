"""SQLite agent store. Its own file ($AGENTS_DB), separate from the datafeed store.

Tables (migration 1; migration 2 adds experiment_id to snapshots and decisions;
migration 3 adds the epoch: runs.epoch_id and runs.epoch_inputs_json, and on
every decision epoch_id, owner_config_sha256 and prompts_sha256_json):
  snapshots     one row per snapshot hash; canonical content JSON
  runs          one row per trigger: done, or skipped + reason
  votes         round-1 votes (round 1 = blind)
  debate_turns  one row per (round, role); claims JSON with validity flags
  decisions     one row per arm (blind, debate); the debate row also carries
                the judge outcome (long/short/hold) and max_rounds
snapshots, votes, debate_turns and decisions all have model_id, prompt_hash,
tokens_in, tokens_out and latency_ms columns. They're null for code-produced
rows, and always null in snapshots for now (code builds them).

Append-only: triggers abort every UPDATE and DELETE. A run is written once, in
one transaction (save_run). A failure rolls back the whole run, so there are no
partial rows. A snapshot is written before any agent runs; the same content
again is a no-op, and different content under the same hash raises.
Timestamps are ISO-8601 UTC text, as in the datafeed store.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Callable

from agents.hashing import canonical_json
from agents.schemas import Claim, DebateResult, Decision, Snapshot, Turn, Vote
from agents.store.base import RunRecord, StoreConflict, _check_types

META_COLS = "model_id TEXT, prompt_hash TEXT, tokens_in INTEGER, tokens_out INTEGER, latency_ms REAL"
META = ("model_id", "prompt_hash", "tokens_in", "tokens_out", "latency_ms")
TABLES = ("snapshots", "runs", "votes", "debate_turns", "decisions")

_V1 = f"""
CREATE TABLE snapshots (
    snapshot_hash  TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    symbol         TEXT NOT NULL,
    as_of          TEXT NOT NULL,
    kinds          TEXT NOT NULL,
    known_by       TEXT,
    newest_bar_ts  TEXT NOT NULL,
    content_json   TEXT NOT NULL,
    {META_COLS},
    created_ts     TEXT NOT NULL
);
CREATE TABLE runs (
    run_id               TEXT PRIMARY KEY,
    trigger_source       TEXT NOT NULL,
    trigger_id           TEXT NOT NULL,
    trigger_t            TEXT NOT NULL,
    trigger_payload_json TEXT NOT NULL,
    as_of                TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('done', 'skipped')),
    skip_reason          TEXT,
    snapshot_hash        TEXT REFERENCES snapshots (snapshot_hash),
    config_json          TEXT NOT NULL,
    config_hash          TEXT NOT NULL,
    code_version         TEXT NOT NULL,
    created_ts           TEXT NOT NULL,
    CHECK ((status = 'skipped') = (skip_reason IS NOT NULL))
);
CREATE INDEX runs_as_of ON runs (as_of);
CREATE INDEX runs_snapshot ON runs (snapshot_hash);
CREATE TABLE votes (
    run_id         TEXT NOT NULL REFERENCES runs (run_id),
    round          INTEGER NOT NULL CHECK (round >= 1),
    agent          TEXT NOT NULL,
    direction      TEXT NOT NULL CHECK (direction IN ('long', 'short', 'none')),
    confidence     REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reason         TEXT NOT NULL,
    abstain        INTEGER NOT NULL CHECK (abstain IN (0, 1)),
    abstain_reason TEXT,
    {META_COLS},
    created_ts     TEXT NOT NULL,
    PRIMARY KEY (run_id, round, agent)
);
CREATE TABLE debate_turns (
    run_id      TEXT NOT NULL REFERENCES runs (run_id),
    round       INTEGER NOT NULL CHECK (round >= 1),
    role        TEXT NOT NULL CHECK (role IN ('bull', 'bear')),
    claims_json TEXT NOT NULL,
    n_claims    INTEGER NOT NULL,
    n_valid     INTEGER NOT NULL,
    conceded    INTEGER NOT NULL CHECK (conceded IN (0, 1)),
    error       TEXT,
    {META_COLS},
    created_ts  TEXT NOT NULL,
    PRIMARY KEY (run_id, round, role)
);
CREATE TABLE decisions (
    run_id            TEXT NOT NULL REFERENCES runs (run_id),
    arm               TEXT NOT NULL CHECK (arm IN ('blind', 'debate')),
    snapshot_hash     TEXT NOT NULL REFERENCES snapshots (snapshot_hash),
    direction         TEXT NOT NULL CHECK (direction IN ('long', 'short', 'none')),
    reason            TEXT NOT NULL,
    decided_by        TEXT NOT NULL,
    debate_outcome    TEXT CHECK (debate_outcome IN ('long', 'short', 'hold')),
    debate_max_rounds INTEGER,
    entry REAL, sl REAL, tp REAL, size REAL, sigma REAL,
    {META_COLS},
    created_ts        TEXT NOT NULL,
    PRIMARY KEY (run_id, arm),
    CHECK ((arm = 'debate') = (debate_outcome IS NOT NULL)),
    CHECK ((arm = 'debate') = (debate_max_rounds IS NOT NULL))
);
""" + "".join(
    f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;\n"
    f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;\n"
    for t in TABLES)

# Owner decision 2026-10-14: experiment_id on every snapshot and decision.
_V2 = """
ALTER TABLE snapshots ADD COLUMN experiment_id TEXT;
ALTER TABLE decisions ADD COLUMN experiment_id TEXT;
CREATE INDEX decisions_experiment ON decisions (experiment_id);
"""

# Agents v2, job 2: the epoch and the SHA-256 of the owner's config and prompts
# on every run and decision (docs/AGENTS_ARCHITECTURE.md §7, §8).
_V3 = """
ALTER TABLE runs ADD COLUMN epoch_id TEXT;
ALTER TABLE runs ADD COLUMN epoch_inputs_json TEXT;
ALTER TABLE decisions ADD COLUMN epoch_id TEXT;
ALTER TABLE decisions ADD COLUMN owner_config_sha256 TEXT;
ALTER TABLE decisions ADD COLUMN prompts_sha256_json TEXT;
CREATE INDEX runs_epoch ON runs (epoch_id);
CREATE INDEX decisions_epoch ON decisions (epoch_id);
"""

MIGRATIONS = [(1, _V1), (2, _V2), (3, _V3)]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(t) -> str | None:
    return None if t is None else t.astimezone(timezone.utc).isoformat()


def migrate(con: sqlite3.Connection) -> int:
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_ts TEXT NOT NULL)")
    have = con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    for version, sql in MIGRATIONS:
        if version > have:
            con.execute("BEGIN IMMEDIATE")
            try:
                for stmt in _statements(sql):
                    con.execute(stmt)
                con.execute("INSERT INTO schema_version VALUES (?, ?)", (version, _utcnow()))
                con.execute("COMMIT")
            except BaseException:
                con.execute("ROLLBACK")
                raise
            have = version
    return have


def _statements(sql: str):
    """Split on ';' at statement ends (CREATE TRIGGER bodies end in 'END;')."""
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip():
                yield buf.strip()
            buf = ""
    if buf.strip():
        raise ValueError(f"incomplete SQL: {buf[:80]!r}")


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    migrate(con)
    return con


def _meta(x) -> tuple:
    return tuple(getattr(x, k) for k in META)


class SqliteAgentStore:
    def __init__(self, path: str | None = None, now: Callable[[], str] = _utcnow) -> None:
        path = path or os.environ.get("AGENTS_DB")
        if not path:
            raise RuntimeError("pass path= or set AGENTS_DB")
        self.path, self.now = path, now
        self.con = connect(path)
        # graph nodes may run on worker threads; writes are serialised here
        self.lock = threading.RLock()

    def close(self) -> None:
        self.con.close()

    # ---------------------------------------------------------------- writes

    def save_snapshot(self, snapshot: Snapshot) -> None:
        with self.lock:
            self._save_snapshot(snapshot)

    def _save_snapshot(self, snapshot: Snapshot) -> None:
        content = snapshot.to_json()
        self.con.execute("BEGIN IMMEDIATE")
        try:
            row = self.con.execute("SELECT content_json FROM snapshots WHERE snapshot_hash = ?",
                                   (snapshot.hash,)).fetchone()
            if row is not None:
                if row[0] != content:
                    raise StoreConflict(f"snapshot {snapshot.hash} exists with other content")
            else:
                self.con.execute(
                    "INSERT INTO snapshots (snapshot_hash, schema_version, symbol, as_of, kinds, known_by,"
                    f" newest_bar_ts, content_json, experiment_id, {', '.join(META)}, created_ts)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (snapshot.hash, snapshot.schema_version, snapshot.symbol, _iso(snapshot.as_of),
                     json.dumps(list(snapshot.kinds)), _iso(snapshot.known_by), _iso(snapshot.newest_bar_ts),
                     content, snapshot.experiment_id, None, None, None, None, None, self.now()))
            self.con.execute("COMMIT")
        except BaseException:
            self.con.execute("ROLLBACK")
            raise

    def save_run(self, run: RunRecord, votes, decisions, debate: DebateResult | None = None) -> None:
        with self.lock:
            self._save_run(run, votes, decisions, debate)

    def _save_run(self, run, votes, decisions, debate) -> None:
        _check_types(run, votes, decisions, debate)
        arms = [d.arm for d in decisions]
        if (debate is not None) != ("debate" in arms):
            raise ValueError("a debate decision and a DebateResult come together")
        now = self.now()
        c = self.con
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("INSERT INTO runs (run_id, trigger_source, trigger_id, trigger_t, trigger_payload_json,"
                      " as_of, status, skip_reason, snapshot_hash, config_json, config_hash, code_version,"
                      " created_ts, epoch_id, epoch_inputs_json)"
                      " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (run.run_id, run.trigger.source, run.trigger.trigger_id, _iso(run.trigger.t),
                       canonical_json(run.trigger.payload), _iso(run.as_of), run.status, run.skip_reason,
                       run.snapshot_hash, run.config_json, run.config_hash, run.code_version, now,
                       run.epoch_id, run.epoch_inputs_json))
            for v in votes:
                c.execute(f"INSERT INTO votes (run_id, round, agent, direction, confidence, reason, abstain,"
                          f" abstain_reason, {', '.join(META)}, created_ts)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (run.run_id, v.round, v.agent, v.direction, v.confidence, v.reason, int(v.abstain),
                           v.abstain_reason, *_meta(v), now))
            for t in (debate.turns if debate is not None else ()):
                c.execute(f"INSERT INTO debate_turns (run_id, round, role, claims_json, n_claims, n_valid,"
                          f" conceded, error, {', '.join(META)}, created_ts)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (run.run_id, t.round, t.role, canonical_json(list(t.claims)), len(t.claims),
                           t.n_valid(), int(t.conceded), t.error, *_meta(t), now))
            for d in decisions:
                deb = debate if d.arm == "debate" else None
                snap_exp = c.execute("SELECT experiment_id FROM snapshots WHERE snapshot_hash = ?",
                                     (d.snapshot_hash,)).fetchone()
                if snap_exp is not None and snap_exp[0] != d.experiment_id:
                    raise ValueError(f"decision {d.arm} has experiment_id {d.experiment_id!r}, "
                                     f"its snapshot has {snap_exp[0]!r}")
                c.execute(f"INSERT INTO decisions (run_id, arm, snapshot_hash, direction, reason, decided_by,"
                          f" debate_outcome, debate_max_rounds, entry, sl, tp, size, sigma, experiment_id,"
                          f" {', '.join(META)}, created_ts, epoch_id, owner_config_sha256, prompts_sha256_json)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (d.run_id, d.arm, d.snapshot_hash, d.direction, d.reason, d.decided_by,
                           deb.outcome if deb else None, deb.max_rounds if deb else None,
                           d.entry, d.sl, d.tp, d.size, d.sigma, d.experiment_id, *_meta(d), now,
                           d.epoch_id, d.owner_config_sha256, canonical_json(d.prompts_sha256)))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # ---------------------------------------------------------------- reads

    def load_snapshot(self, snapshot_hash: str) -> Snapshot:
        """Validated on load: a content/hash mismatch raises."""
        row = self.con.execute("SELECT content_json FROM snapshots WHERE snapshot_hash = ?",
                               (snapshot_hash,)).fetchone()
        if row is None:
            raise KeyError(snapshot_hash)
        return Snapshot.model_validate(json.loads(row[0]))

    def run_ids(self) -> list[str]:
        return [r[0] for r in self.con.execute("SELECT run_id FROM runs ORDER BY created_ts, run_id")]

    def load_run(self, run_id: str) -> dict:
        c = self.con
        r = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if r is None:
            raise KeyError(run_id)
        run = RunRecord(run_id=r["run_id"], as_of=r["as_of"], status=r["status"], skip_reason=r["skip_reason"],
                        snapshot_hash=r["snapshot_hash"], config_json=r["config_json"],
                        config_hash=r["config_hash"], code_version=r["code_version"],
                        epoch_id=r["epoch_id"], epoch_inputs_json=r["epoch_inputs_json"],
                        trigger={"source": r["trigger_source"], "trigger_id": r["trigger_id"],
                                 "t": r["trigger_t"], "payload": json.loads(r["trigger_payload_json"])})
        meta = lambda row: {k: row[k] for k in META}                      # noqa: E731
        votes = tuple(Vote(agent=v["agent"], round=v["round"], direction=v["direction"], confidence=v["confidence"],
                           reason=v["reason"], abstain=bool(v["abstain"]), abstain_reason=v["abstain_reason"],
                           **meta(v))
                      for v in c.execute("SELECT * FROM votes WHERE run_id = ? ORDER BY round, agent", (run_id,)))
        rows = c.execute("SELECT * FROM decisions WHERE run_id = ? ORDER BY arm = 'debate', arm", (run_id,)).fetchall()
        decisions = tuple(Decision(run_id=d["run_id"], snapshot_hash=d["snapshot_hash"], arm=d["arm"],
                                   direction=d["direction"], reason=d["reason"], decided_by=d["decided_by"],
                                   entry=d["entry"], sl=d["sl"], tp=d["tp"], size=d["size"], sigma=d["sigma"],
                                   experiment_id=d["experiment_id"], epoch_id=d["epoch_id"],
                                   owner_config_sha256=d["owner_config_sha256"],
                                   prompts_sha256=json.loads(d["prompts_sha256_json"] or "{}"),
                                   **meta(d)) for d in rows)
        debate = None
        deb_row = next((d for d in rows if d["arm"] == "debate"), None)
        if deb_row is not None:
            turns = tuple(Turn(role=t["role"], round=t["round"], conceded=bool(t["conceded"]), error=t["error"],
                               claims=tuple(Claim(**x) for x in json.loads(t["claims_json"])), **meta(t))
                          for t in c.execute("SELECT * FROM debate_turns WHERE run_id = ?"
                                             " ORDER BY round, role = 'bear'", (run_id,)))
            debate = DebateResult(snapshot_hash=deb_row["snapshot_hash"], max_rounds=deb_row["debate_max_rounds"],
                                  turns=turns, n_valid_bull=sum(t.n_valid() for t in turns if t.role == "bull"),
                                  n_valid_bear=sum(t.n_valid() for t in turns if t.role == "bear"),
                                  judge_name=deb_row["decided_by"], outcome=deb_row["debate_outcome"],
                                  reason=deb_row["reason"])
        return {"run": run, "votes": votes, "decisions": decisions, "debate": debate}
