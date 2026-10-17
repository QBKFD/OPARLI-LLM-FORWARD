"""SQLite agent store. Its own file ($AGENTS_DB), separate from the datafeed store.

Tables, by migration:
  1  snapshots     one row per snapshot hash; canonical content JSON
     runs          one row per trigger: done, or skipped + reason
     votes         every vote; round 1 = blind, round 2.. = discussion rounds
     debate_turns  the old bull/bear debate; history only, no longer written
     decisions     one row per arm
  2  experiment_id on snapshots and decisions
  3  the epoch: runs.epoch_id, runs.epoch_inputs_json; on every decision
     epoch_id, owner_config_sha256, prompts_sha256_json
  4  llm_calls (one row per attempt) and llm_responses (the cache); votes gain
     evidence_json and call_id
  5  votes gain changed, change_rejected, rejected_proposal_json; discussions
     (one row per run); decisions rebuilt for the arms blind, discussion and
     technical_v1 (old debate rows stay, and load_run leaves them out), with
     meta_rule and meta_version
snapshots, votes and decisions have model_id, prompt_hash, tokens_in,
tokens_out and latency_ms columns. They're null for code-produced rows.

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
from agents.schemas import Decision, DiscussionResult, Evidence, RejectedChange, Snapshot, Vote
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

# Agents v2, job 3: the LLM ledger (agents/llm/ledger.py) and the evidence of
# every vote. llm_calls references the snapshot, not the run: calls happen
# before the run's own row is written. Both new tables are append-only.
LLM_TABLES = ("llm_calls", "llm_responses")
_V4 = """
CREATE TABLE llm_responses (
    prompt_hash      TEXT PRIMARY KEY,
    model            TEXT NOT NULL,
    request_id       TEXT,
    stop_reason      TEXT,
    refusal_category TEXT,
    text             TEXT,
    usage_json       TEXT NOT NULL,
    response_json    TEXT NOT NULL,
    latency_ms       REAL,
    created_ts       TEXT NOT NULL
);
CREATE TABLE llm_calls (
    call_id            TEXT PRIMARY KEY,
    snapshot_hash      TEXT NOT NULL,
    agent              TEXT NOT NULL,
    round              INTEGER NOT NULL CHECK (round >= 1),
    mode               TEXT NOT NULL CHECK (mode IN ('live', 'rerun')),
    attempt            INTEGER NOT NULL CHECK (attempt >= 0),
    model_requested    TEXT NOT NULL,
    effort             TEXT NOT NULL,
    max_tokens         INTEGER NOT NULL,
    prompt_hash        TEXT NOT NULL,
    cached             INTEGER NOT NULL CHECK (cached IN (0, 1)),
    utc_day            TEXT NOT NULL,
    created_ts         TEXT NOT NULL,
    model_returned     TEXT,
    prompt_file_sha256 TEXT,
    config_sha256      TEXT,
    epoch_id           TEXT,
    request_id         TEXT,
    stop_reason        TEXT,
    refusal_category   TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    latency_ms         REAL,
    error              TEXT,
    cost_usd           REAL
);
CREATE INDEX llm_calls_day ON llm_calls (utc_day, cached);
CREATE INDEX llm_calls_snapshot ON llm_calls (snapshot_hash);
ALTER TABLE votes ADD COLUMN evidence_json TEXT;
ALTER TABLE votes ADD COLUMN call_id TEXT;
""" + "".join(
    f"CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;\n"
    f"CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT, '{t} is append-only'); END;\n"
    for t in LLM_TABLES)

# Agents v2, job 4: the discussion replaces the debate.
# - votes: changed / change_rejected / rejected_proposal_json for every round.
# - discussions: one row per run.
# - decisions is rebuilt (SQLite can't change a CHECK in place). The arms are
#   now blind, discussion and technical_v1; old debate rows stay valid. It
#   gains meta_rule and meta_version. Every row is copied unchanged, and the
#   append-only triggers and indexes are recreated.
# debate_turns stays, as history; nothing writes it any more.
_DEC_COLS_V4 = "run_id, arm, snapshot_hash, direction, reason, decided_by, debate_outcome, debate_max_rounds, entry, sl, tp, size, sigma, model_id, prompt_hash, tokens_in, tokens_out, latency_ms, created_ts, experiment_id, epoch_id, owner_config_sha256, prompts_sha256_json"
_V5 = f"""
ALTER TABLE votes ADD COLUMN changed INTEGER NOT NULL DEFAULT 0 CHECK (changed IN (0, 1));
ALTER TABLE votes ADD COLUMN change_rejected TEXT;
ALTER TABLE votes ADD COLUMN rejected_proposal_json TEXT;
CREATE TABLE discussions (
    run_id        TEXT PRIMARY KEY REFERENCES runs (run_id),
    snapshot_hash TEXT NOT NULL REFERENCES snapshots (snapshot_hash),
    max_rounds    INTEGER NOT NULL CHECK (max_rounds >= 0),
    rounds_run    INTEGER NOT NULL CHECK (rounds_run >= 0),
    stopped_early INTEGER NOT NULL CHECK (stopped_early IN (0, 1)),
    skip_reason   TEXT,
    speakers_json TEXT NOT NULL,
    created_ts    TEXT NOT NULL,
    CHECK ((rounds_run = 0) = (skip_reason IS NOT NULL))
);
CREATE TRIGGER discussions_no_update BEFORE UPDATE ON discussions BEGIN SELECT RAISE(ABORT, 'discussions is append-only'); END;
CREATE TRIGGER discussions_no_delete BEFORE DELETE ON discussions BEGIN SELECT RAISE(ABORT, 'discussions is append-only'); END;
DROP TRIGGER decisions_no_update;
DROP TRIGGER decisions_no_delete;
DROP INDEX decisions_experiment;
DROP INDEX decisions_epoch;
ALTER TABLE decisions RENAME TO decisions_v4;
CREATE TABLE decisions (
    run_id              TEXT NOT NULL REFERENCES runs (run_id),
    arm                 TEXT NOT NULL CHECK (arm IN ('blind', 'discussion', 'technical_v1', 'debate')),
    snapshot_hash       TEXT NOT NULL REFERENCES snapshots (snapshot_hash),
    direction           TEXT NOT NULL CHECK (direction IN ('long', 'short', 'none')),
    reason              TEXT NOT NULL,
    decided_by          TEXT NOT NULL,
    debate_outcome      TEXT CHECK (debate_outcome IN ('long', 'short', 'hold')),
    debate_max_rounds   INTEGER,
    entry REAL, sl REAL, tp REAL, size REAL, sigma REAL,
    {META_COLS},
    created_ts          TEXT NOT NULL,
    experiment_id       TEXT,
    epoch_id            TEXT,
    owner_config_sha256 TEXT,
    prompts_sha256_json TEXT,
    meta_rule           TEXT,
    meta_version        TEXT,
    PRIMARY KEY (run_id, arm),
    CHECK ((arm = 'debate') = (debate_outcome IS NOT NULL))
);
INSERT INTO decisions ({_DEC_COLS_V4}) SELECT {_DEC_COLS_V4} FROM decisions_v4;
DROP TABLE decisions_v4;
CREATE INDEX decisions_experiment ON decisions (experiment_id);
CREATE INDEX decisions_epoch ON decisions (epoch_id);
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions BEGIN SELECT RAISE(ABORT, 'decisions is append-only'); END;
"""

MIGRATIONS = [(1, _V1), (2, _V2), (3, _V3), (4, _V4), (5, _V5)]


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

    def save_run(self, run: RunRecord, votes, decisions, discussion: DiscussionResult | None = None) -> None:
        with self.lock:
            self._save_run(run, votes, decisions, discussion)

    def _save_run(self, run, votes, decisions, discussion) -> None:
        _check_types(run, votes, decisions, discussion)
        arms = [d.arm for d in decisions]
        if (discussion is not None) != ("discussion" in arms):
            raise ValueError("a discussion decision and a DiscussionResult come together")
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
                          f" abstain_reason, {', '.join(META)}, created_ts, evidence_json, call_id, changed,"
                          " change_rejected, rejected_proposal_json)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (run.run_id, v.round, v.agent, v.direction, v.confidence, v.reason, int(v.abstain),
                           v.abstain_reason, *_meta(v), now, canonical_json(list(v.evidence)), v.call_id,
                           int(v.changed), v.change_rejected,
                           None if v.rejected_proposal is None else canonical_json(v.rejected_proposal)))
            if discussion is not None:
                c.execute("INSERT INTO discussions (run_id, snapshot_hash, max_rounds, rounds_run, stopped_early,"
                          " skip_reason, speakers_json, created_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                          (run.run_id, discussion.snapshot_hash, discussion.max_rounds, discussion.rounds_run,
                           int(discussion.stopped_early), discussion.skip_reason,
                           canonical_json(list(discussion.speakers)), now))
            for d in decisions:
                snap_exp = c.execute("SELECT experiment_id FROM snapshots WHERE snapshot_hash = ?",
                                     (d.snapshot_hash,)).fetchone()
                if snap_exp is not None and snap_exp[0] != d.experiment_id:
                    raise ValueError(f"decision {d.arm} has experiment_id {d.experiment_id!r}, "
                                     f"its snapshot has {snap_exp[0]!r}")
                c.execute(f"INSERT INTO decisions (run_id, arm, snapshot_hash, direction, reason, decided_by,"
                          f" entry, sl, tp, size, sigma, experiment_id, {', '.join(META)}, created_ts, epoch_id,"
                          " owner_config_sha256, prompts_sha256_json, meta_rule, meta_version)"
                          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                          (d.run_id, d.arm, d.snapshot_hash, d.direction, d.reason, d.decided_by,
                           d.entry, d.sl, d.tp, d.size, d.sigma, d.experiment_id, *_meta(d), now,
                           d.epoch_id, d.owner_config_sha256, canonical_json(d.prompts_sha256),
                           d.meta_rule, d.meta_version))
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
                           evidence=tuple(Evidence(**e) for e in json.loads(v["evidence_json"] or "[]")),
                           call_id=v["call_id"], changed=bool(v["changed"]), change_rejected=v["change_rejected"],
                           rejected_proposal=(None if v["rejected_proposal_json"] is None else
                                              RejectedChange.model_validate_json(v["rejected_proposal_json"])),
                           **meta(v))
                      for v in c.execute("SELECT * FROM votes WHERE run_id = ? ORDER BY round, agent", (run_id,)))
        rows = c.execute("SELECT * FROM decisions WHERE run_id = ? ORDER BY CASE arm WHEN 'blind' THEN 0"
                         " WHEN 'discussion' THEN 1 ELSE 2 END", (run_id,)).fetchall()
        decisions = tuple(Decision(run_id=d["run_id"], snapshot_hash=d["snapshot_hash"], arm=d["arm"],
                                   direction=d["direction"], reason=d["reason"], decided_by=d["decided_by"],
                                   entry=d["entry"], sl=d["sl"], tp=d["tp"], size=d["size"], sigma=d["sigma"],
                                   experiment_id=d["experiment_id"], epoch_id=d["epoch_id"],
                                   owner_config_sha256=d["owner_config_sha256"],
                                   prompts_sha256=json.loads(d["prompts_sha256_json"] or "{}"),
                                   meta_rule=d["meta_rule"], meta_version=d["meta_version"],
                                   **meta(d)) for d in rows if d["arm"] != "debate")
        discussion = None
        drow = c.execute("SELECT * FROM discussions WHERE run_id = ?", (run_id,)).fetchone()
        if drow is not None:
            disc_votes = tuple(v for v in votes if v.round >= 2)
            final = {v.agent: v for v in votes if v.round == 1}
            final.update({v.agent: v for v in disc_votes})               # votes are in round order
            discussion = DiscussionResult(snapshot_hash=drow["snapshot_hash"], max_rounds=drow["max_rounds"],
                                          rounds_run=drow["rounds_run"], stopped_early=bool(drow["stopped_early"]),
                                          skip_reason=drow["skip_reason"], speakers=tuple(json.loads(drow["speakers_json"])),
                                          votes=disc_votes, final=final)
        return {"run": run, "votes": votes, "decisions": decisions, "discussion": discussion}
