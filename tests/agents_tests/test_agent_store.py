"""Step 6: SQLite agent store (its own file, append-only) and config."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.config import AgentsConfig
from agents.graph import build_graph, run_once
from agents.schemas import Decision, TriggerEvent, Vote
from agents.store import SqliteAgentStore
from agents.store.base import RunRecord, StoreConflict
from agents.store.sqlite import META, MIGRATIONS, TABLES
from agents_fakes import FakeDiscussant, FixedVoter, bar_ref
from agents_testkit import write_test_prompts

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
FIXED = lambda: 0.0                                           # noqa: E731
CFG = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})


def ev(t=T, i="1"):
    return TriggerEvent(source="test", t=t, trigger_id=i, payload={"k": [1, 2]})


def voters():
    from agents.voters import default_voters
    v = default_voters()
    v.update(visual=FixedVoter("visual", "long"), technical=FixedVoter("technical", "long"))
    return v


@pytest.fixture
def store(tmp_path):
    s = SqliteAgentStore(str(tmp_path / "agents.sqlite"))
    yield s
    s.close()


def columns(con, table):
    return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}


# ---- schema

def test_tables_and_model_columns(store):
    names = {r[0] for r in store.con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert set(TABLES) | {"schema_version"} <= names and "bars" not in names       # not the datafeed store
    for t in ("snapshots", "votes", "debate_turns", "decisions"):
        assert set(META) <= columns(store.con, t), t
    assert "round" in columns(store.con, "votes") and "arm" in columns(store.con, "decisions")
    assert {"claims_json", "n_valid"} <= columns(store.con, "debate_turns")
    assert {"entry", "sl", "tp", "size", "sigma"} <= columns(store.con, "decisions")


def test_reopen_does_not_remigrate(tmp_path):
    p = str(tmp_path / "a.sqlite")
    SqliteAgentStore(p).close()
    s = SqliteAgentStore(p)
    assert s.con.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == len(MIGRATIONS) == 5
    s.close()


def test_v1_database_is_migrated_to_v2(tmp_path, monkeypatch):
    from agents.store import sqlite as sq
    p = str(tmp_path / "v1.sqlite")
    monkeypatch.setattr(sq, "MIGRATIONS", sq.MIGRATIONS[:1])
    sq.SqliteAgentStore(p).close()
    con = sqlite3.connect(p)
    assert "experiment_id" not in columns(con, "decisions")
    con.close()
    monkeypatch.undo()
    s = SqliteAgentStore(p)
    assert "experiment_id" in columns(s.con, "decisions") and "experiment_id" in columns(s.con, "snapshots")
    s.close()


def test_path_from_env(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENTS_DB", raising=False)
    with pytest.raises(RuntimeError):
        SqliteAgentStore()
    monkeypatch.setenv("AGENTS_DB", str(tmp_path / "env.sqlite"))
    s = SqliteAgentStore()
    assert Path(s.path).exists()
    s.close()


# ---- writing through the graph

def discussing_voters():
    """visual long; technical short, which flips to long citing a new bar in
    round 1 (accepted), then tries short again with only a vote: ref (refused)."""
    def tech(view):
        if view.round == 1:
            return {"direction": "long", "confidence": 0.7, "refs": [bar_ref(view.snapshot, 3)]}
        return {"direction": "short", "confidence": 0.9, "refs": ["vote:visual:2"]}
    v = voters()
    v.update(visual=FakeDiscussant("visual", "long"), technical=FakeDiscussant("technical", "short", script=tech))
    return v


def test_graph_run_round_trips(store, replay_db, tmp_path):
    g = build_graph(CFG, store, replay_db, voters=discussing_voters(), clock=FIXED,
                    prompts_dir=write_test_prompts(tmp_path / "prompts"))
    out = run_once(g, ev(), run_id="r1")
    rec = store.load_run("r1")
    assert rec["run"].status == "done" and rec["run"].trigger == ev()
    disc = out["discussion"]
    assert rec["votes"] == tuple(out["votes"][n] for n in sorted(out["votes"])) + disc.votes
    assert rec["decisions"] == (out["final_decisions"]["blind"], out["final_decisions"]["discussion"])
    assert rec["discussion"] == disc
    assert (disc.rounds_run, disc.stopped_early) == (2, False)
    assert out["final_decisions"]["blind"].direction == "none"           # long 1, short 1 (quorum v0)
    assert out["final_decisions"]["discussion"].direction == "long"      # technical changed to long
    refused = store.con.execute("SELECT change_rejected, rejected_proposal_json FROM votes"
                                " WHERE agent = 'technical' AND round = 3").fetchone()
    assert refused[0] == "only vote: refs" and '"direction":"short"' in refused[1]   # logged with the proposal
    assert store.load_snapshot(out["snap"].hash) == out["snap"]


def test_stale_run_has_a_row_and_nothing_else(store, replay_db):
    out = run_once(build_graph(CFG, store, replay_db), ev(datetime(2025, 6, 12, 10, 0, tzinfo=UTC)), run_id="s")
    row = store.con.execute("SELECT status, skip_reason, snapshot_hash FROM runs WHERE run_id = 's'").fetchone()
    assert tuple(row) == ("skipped", out["skip_reason"], None)
    for t in ("snapshots", "votes", "decisions", "discussions", "llm_calls"):
        assert store.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] == 0, t


def test_snapshot_written_once_per_hash(store, replay_db):
    g = build_graph(CFG, store, replay_db)
    run_once(g, ev(T, "a"))
    run_once(g, ev(T.replace(second=5), "b"))                       # same minute -> same snapshot
    assert store.con.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert store.con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2


# ---- append-only and atomic

def test_every_table_is_append_only(store, replay_db, tmp_path):
    run_once(build_graph(CFG, store, replay_db, voters=discussing_voters(), clock=FIXED,
                         prompts_dir=write_test_prompts(tmp_path / "prompts")), ev(), run_id="r")
    for t in (*TABLES, "discussions"):
        if t == "debate_turns":                                   # history only: nothing writes it now
            continue
        assert store.con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] > 0, t
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.con.execute(f"UPDATE {t} SET created_ts = 'x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.con.execute(f"DELETE FROM {t}")


def test_a_run_is_written_once(store, replay_db):
    g = build_graph(CFG, store, replay_db)
    run_once(g, ev(), run_id="dup")
    with pytest.raises(sqlite3.IntegrityError):
        run_once(g, ev(), run_id="dup")
    assert store.con.execute("SELECT COUNT(*) FROM votes WHERE run_id = 'dup'").fetchone()[0] == 4      # visual, technical, sentiment, gex


def test_a_failed_run_write_leaves_no_partial_rows(store, replay_db):
    snap = run_once(build_graph(CFG, store, replay_db), ev(), run_id="ok")["snap"]
    run = RunRecord(run_id="bad", trigger=ev(), as_of=snap.as_of, status="done", snapshot_hash=snap.hash,
                    config_json="{}", config_hash="h", code_version="0")
    d = Decision(run_id="bad", snapshot_hash=snap.hash, arm="blind", direction="none", reason="x", decided_by="a")
    with pytest.raises(sqlite3.IntegrityError):                     # second blind decision violates the key
        store.save_run(run, [Vote.abstained("visual", "x")], [d, d])
    for t in ("runs", "votes", "decisions"):
        assert store.con.execute(f"SELECT COUNT(*) FROM {t} WHERE run_id = 'bad'").fetchone()[0] == 0


def test_run_must_reference_a_saved_snapshot(store):
    run = RunRecord(run_id="x", trigger=ev(), as_of=T.replace(second=0), status="done", snapshot_hash="nope",
                    config_json="{}", config_hash="h", code_version="0")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_run(run, [], [])


def test_snapshot_hash_conflict_is_refused(store, replay_db):
    snap = run_once(build_graph(CFG, store, replay_db), ev())["snap"]
    store.con.execute("DROP TRIGGER snapshots_no_update")           # simulate a corrupted row
    store.con.execute("UPDATE snapshots SET content_json = '{}'")
    with pytest.raises(StoreConflict):
        store.save_snapshot(snap)


def test_tampered_snapshot_fails_on_load(store, replay_db):
    snap = run_once(build_graph(CFG, store, replay_db), ev())["snap"]
    store.con.execute("DROP TRIGGER snapshots_no_update")
    store.con.execute("UPDATE snapshots SET content_json = replace(content_json, '\"close\":', '\"close\":1')")
    with pytest.raises(Exception, match="does not match"):
        store.load_snapshot(snap.hash)


def test_datafeed_store_is_untouched(store, replay_db):
    import hashlib
    before = hashlib.sha256(Path(replay_db).read_bytes()).hexdigest()
    run_once(build_graph(CFG, store, replay_db), ev())
    assert hashlib.sha256(Path(replay_db).read_bytes()).hexdigest() == before
    assert Path(store.path).resolve() != Path(replay_db).resolve()


# ---- config

def test_example_config_is_the_defaults():
    p = Path(__file__).resolve().parents[2] / "agents" / "config.example.toml"
    c = AgentsConfig.from_toml(p)
    assert c == CFG.model_copy(update={"snapshot": CFG.snapshot.model_copy(update={"sigma_model": "parquet2021_2023"})})
    assert CFG.snapshot.sigma_model is None                    # no default model in code
    assert "debate" not in c.model_dump()                      # the discussion is the owner's (agents.toml)


@pytest.mark.parametrize("bad", [
    {},                                                             # snapshot.mode is required
    {"snapshot": {"mode": "paper"}},
    {"snapshot": {"mode": "replay"}, "debate": {"enabled": True}},  # the old debate section is refused
    {"snapshot": {"mode": "replay"}, "node_timeout": 5},            # typo -> refused, not ignored
    {"snapshot": {"mode": "replay", "keep": {"1d": 5, "1m": 1}}},
])
def test_bad_config_is_refused(bad):
    with pytest.raises(Exception):
        AgentsConfig.model_validate(bad)
