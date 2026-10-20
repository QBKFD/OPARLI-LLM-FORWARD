"""Owner decision 1 (2026-10-14): seal and live mode.

- replay keeps the seal: no override, with or without an experiment.
- live is allowed only with an experiment_id whose
  agents/experiments/<id>/PREREG.md exists, and only for t within 120 s of the
  wall clock.
- experiment_id is stored on every snapshot and decision.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from datafeed.ingest.xauusd_bars import SEAL_START, Bar as FeedBar, bar_message
from datafeed.store import schema as feed_schema

from agents import prereg
from agents.config import AgentsConfig, SnapshotConfig
from agents.graph import build_graph, run_once
from agents.prereg import ExperimentError
from agents.schemas import TriggerEvent
from agents.snapshot import LiveClockError, SealedError, Stale, build_snapshot
from agents.store import SqliteAgentStore
from agents_fakes import FakeDiscussant, FixedVoter
from agents_testkit import write_test_prompts

UTC = timezone.utc
T_LIVE = datetime(2026, 10, 14, 14, 30, 30, tzinfo=UTC)          # Wednesday, after the seal
WALL = lambda: T_LIVE + timedelta(seconds=30)                    # noqa: E731
T_REPLAY = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
EXP = "exp-0001"


@pytest.fixture
def experiments(tmp_path, monkeypatch):
    root = tmp_path / "experiments"
    (root / EXP).mkdir(parents=True)
    (root / EXP / "PREREG.md").write_text("# exp-0001\nHypothesis, arms, scoring, stopping rule.\n")
    (root / "empty").mkdir()
    (root / "empty" / "PREREG.md").write_text("")
    monkeypatch.setattr(prereg, "EXPERIMENTS_DIR", root)
    return root


@pytest.fixture
def live_db(tmp_path):
    """Synthetic LIVE bars after the seal, 10:00..14:29 UTC on 2026-10-14, each
    ingested 2 s after it closed. One minute (13:00) has no live bar, only a
    history bar that was backfilled an hour after T_LIVE."""
    path = str(tmp_path / "live_feed.sqlite")
    con = feed_schema.connect(path)
    con.execute("BEGIN")
    t, px = datetime(2026, 10, 14, 10, 0, tzinfo=UTC), 4000.0
    while t < datetime(2026, 10, 14, 14, 30, tzinfo=UTC):
        bar = FeedBar("XAUUSD", t, px, px + 1, px - 1, px + 0.5, 12)
        if t.strftime("%H:%M") == "13:00":
            msg = bar_message(bar, source="ibkr", history=True, ingest_ts=T_LIVE + timedelta(hours=1))
        else:
            msg = bar_message(bar, source="ibkr", history=False, ingest_ts=t + timedelta(seconds=62))
        feed_schema.insert_bar(con, msg)
        t, px = t + timedelta(minutes=1), px + 0.5
    con.execute("COMMIT")
    con.close()
    return path


def live_cfg(**over):
    return AgentsConfig.model_validate({"snapshot": {"mode": "live", "experiment_id": EXP, **over}})


def ev(t):
    return TriggerEvent(source="test", t=t, trigger_id=t.isoformat())


# ---- replay keeps the seal

@pytest.mark.parametrize("exp", [None, EXP])
def test_replay_keeps_the_seal_with_or_without_experiment(experiments, replay_db, exp):
    cfg = SnapshotConfig(mode="replay", experiment_id=exp)
    with pytest.raises(SealedError, match="no override"):
        build_snapshot(SEAL_START + timedelta(minutes=1), cfg, replay_db, now=lambda: SEAL_START)


def test_replay_before_the_seal_stores_the_experiment_id(experiments, replay_db, tmp_path):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay", "experiment_id": EXP}})
    out = run_once(build_graph(cfg, store, replay_db), ev(T_REPLAY), run_id="r")
    assert out["snap"].experiment_id == EXP and out["final_decisions"]["blind"].experiment_id == EXP
    assert store.con.execute("SELECT experiment_id FROM snapshots").fetchone()[0] == EXP
    assert store.con.execute("SELECT experiment_id FROM decisions WHERE run_id = 'r'").fetchone()[0] == EXP
    store.close()


def test_replay_without_experiment_stores_null(replay_db, tmp_path):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    run_once(build_graph(AgentsConfig.model_validate({"snapshot": {"mode": "replay"}}), store, replay_db),
             ev(T_REPLAY), run_id="r")
    assert store.con.execute("SELECT experiment_id FROM snapshots").fetchone()[0] is None
    assert store.con.execute("SELECT experiment_id FROM decisions").fetchone()[0] is None
    store.close()


def test_replay_with_unknown_experiment_is_refused(experiments, replay_db):
    with pytest.raises(ExperimentError, match="no PREREG"):
        build_snapshot(T_REPLAY, SnapshotConfig(mode="replay", experiment_id="exp-9999"), replay_db)


# ---- live mode: experiment + PREREG required

def test_live_without_experiment_is_refused(live_db):
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "live"}})
    with pytest.raises(ExperimentError, match="live mode needs"):
        build_graph(cfg, None, live_db)
    with pytest.raises(ExperimentError, match="live mode needs"):
        build_snapshot(T_LIVE, cfg.snapshot, live_db, now=WALL)


@pytest.mark.parametrize("exp, match", [("exp-9999", "no PREREG"), ("empty", "is empty")])
def test_live_needs_an_existing_non_empty_prereg(experiments, live_db, exp, match):
    with pytest.raises(ExperimentError, match=match):
        build_graph(live_cfg(experiment_id=exp), None, live_db, wall_clock=WALL)
    with pytest.raises(ExperimentError, match=match):
        build_snapshot(T_LIVE, SnapshotConfig(mode="live", experiment_id=exp), live_db, now=WALL)


def test_the_real_experiments_folder_is_checked(live_db):
    # no monkeypatch: agents/experiments/ in the repo has no such experiment
    assert prereg.EXPERIMENTS_DIR.name == "experiments" and prereg.EXPERIMENTS_DIR.parent.name == "agents"
    with pytest.raises(ExperimentError, match="no PREREG"):
        build_snapshot(T_LIVE, SnapshotConfig(mode="live", experiment_id="exp-0001"), live_db, now=WALL)


@pytest.mark.parametrize("bad", ["../escape", "a/b", "", ".hidden", "x" * 65, "exp 1"])
def test_bad_experiment_ids_are_refused(bad):
    with pytest.raises(ValidationError):
        SnapshotConfig(mode="live", experiment_id=bad)
    with pytest.raises(ExperimentError):
        prereg.prereg_path(bad)


# ---- live mode: t within 120 s of the wall clock

@pytest.mark.parametrize("skew_s, ok", [(0, True), (120, True), (-120, True), (121, False), (-121, False),
                                        (86400, False)])
def test_live_t_must_be_within_120s_of_wall_clock(experiments, live_db, skew_s, ok):
    cfg = SnapshotConfig(mode="live", experiment_id=EXP)
    now = lambda: T_LIVE + timedelta(seconds=skew_s)             # noqa: E731
    if ok:
        assert not isinstance(build_snapshot(T_LIVE, cfg, live_db, now=now), Stale)
    else:
        with pytest.raises(LiveClockError, match="from the wall clock"):
            build_snapshot(T_LIVE, cfg, live_db, now=now)


def test_live_cannot_read_sealed_history_at_an_arbitrary_t(experiments, live_db):
    # a valid experiment doesn't help when t isn't "now"
    with pytest.raises(LiveClockError):
        build_snapshot(T_LIVE, SnapshotConfig(mode="live", experiment_id=EXP), live_db,
                       now=lambda: T_LIVE + timedelta(days=30))


# ---- a full live run

def test_live_run_after_the_seal(experiments, live_db, tmp_path):
    from agents.voters import default_voters
    voters = default_voters()
    voters.update(visual=FakeDiscussant("visual", "long"), technical=FixedVoter("technical", "long"))
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "live", "experiment_id": EXP}})
    store = SqliteAgentStore(str(tmp_path / "agents.sqlite"))
    g = build_graph(cfg, store, live_db, voters=voters, clock=lambda: 0.0, wall_clock=WALL,
                    prompts_dir=write_test_prompts(tmp_path / "prompts"))
    out = run_once(g, ev(T_LIVE), run_id="live1")
    assert out["status"] == "done"
    snap = store.load_snapshot(out["snap"].hash)
    assert snap.as_of > SEAL_START and snap.kinds == ("live", "history") and snap.known_by == T_LIVE
    assert snap.experiment_id == EXP and snap.newest_bar_ts == datetime(2026, 10, 14, 14, 29, tzinfo=UTC)
    # known_by = t: the 13:00 history bar, backfilled after t, is invisible
    assert datetime(2026, 10, 14, 13, 0, tzinfo=UTC) not in {b.ts for b in snap.bars["1m"]}
    rows = store.con.execute("SELECT arm, experiment_id FROM decisions WHERE run_id = 'live1' ORDER BY arm")
    assert [tuple(r) for r in rows] == [("blind", EXP), ("discussion", EXP)]
    assert store.con.execute("SELECT experiment_id FROM snapshots").fetchone()[0] == EXP
    loaded = store.load_run("live1")["decisions"]
    assert {d.experiment_id for d in loaded} == {EXP}
    store.close()


def test_live_run_without_bars_is_stale_and_never_uses_replay(experiments, replay_db):
    out = build_snapshot(T_REPLAY, SnapshotConfig(mode="live", experiment_id=EXP), replay_db,
                         now=lambda: T_REPLAY)
    assert isinstance(out, Stale) and out.reason.startswith("no live/history bars")


def test_decision_experiment_must_match_its_snapshot(experiments, replay_db, tmp_path):
    from agents.schemas import Decision
    from agents.store.base import RunRecord
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay", "experiment_id": EXP}})
    snap = run_once(build_graph(cfg, store, replay_db), ev(T_REPLAY))["snap"]
    run = RunRecord(run_id="bad", trigger=ev(T_REPLAY), as_of=snap.as_of, status="done", snapshot_hash=snap.hash,
                    config_json="{}", config_hash="h", code_version="0")
    wrong = Decision(run_id="bad", snapshot_hash=snap.hash, arm="blind", direction="none", reason="x",
                     decided_by="a", experiment_id="exp-other")
    with pytest.raises(ValueError, match="experiment_id"):
        store.save_run(run, [], [wrong])
    assert store.con.execute("SELECT COUNT(*) FROM runs WHERE run_id = 'bad'").fetchone()[0] == 0
    store.close()


def test_experiment_id_is_part_of_the_snapshot_hash(experiments, replay_db):
    a = build_snapshot(T_REPLAY, SnapshotConfig(mode="replay"), replay_db)
    b = build_snapshot(T_REPLAY, SnapshotConfig(mode="replay", experiment_id=EXP), replay_db)
    assert a.bars == b.bars and a.hash != b.hash and a.schema_version == b.schema_version == 3


def test_migration_keeps_append_only(experiments, replay_db, tmp_path):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay", "experiment_id": EXP}})
    run_once(build_graph(cfg, store, replay_db), ev(T_REPLAY))
    for t in ("snapshots", "decisions"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.con.execute(f"UPDATE {t} SET experiment_id = 'other'")
    store.close()
