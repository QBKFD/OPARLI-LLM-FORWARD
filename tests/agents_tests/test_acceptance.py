"""Step 7: the acceptance list, through the full graph + SQLite agent store, on
replay data. One test per item (A1..A9), plus a run over one real parquet day
(skipped when the untracked parquet is absent).

A1 a snapshot never contains a bar >= t
A2 the same inputs give the same snapshot hash
A3 a node that raises becomes an abstain and the graph still finishes
A4 debate disabled -> only the blind decision exists
A5 debate enabled -> two decisions on the same snapshot (blind, debate); round-1 rows unchanged
A6 a claim with a nonexistent evidence_ref is invalid and ignored
A7 tie -> hold; a debater error -> the debate still finishes
A8 a debater sees nothing outside the snapshot + round-1 votes + opponent's previous turn
A9 same snapshot + same fake debaters -> identical DebateResult
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agents.config import AgentsConfig
from agents.debate import CodeJudgeV0, DebaterView
from agents.debate.subgraph import content_only
from agents.graph import build_graph, run_once
from agents.schemas import TriggerEvent
from agents.store import SqliteAgentStore
from agents.voters import default_voters
from agents_fakes import FakeBear, FakeBull, FixedVoter, RaisingDebater, RaisingVoter, RecordingDebater, SpyJudge
from agents_testkit import DATA_END, DATA_START, make_replay_db

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
FIXED = lambda: 0.0                                           # noqa: E731
OFF = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
ON = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}, "debate": {"enabled": True}})
PARQUET = Path(__file__).resolve().parents[2] / "database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet"


def ev(t=T, i="1"):
    return TriggerEvent(source="acceptance", t=t, trigger_id=i)


def long_voters():
    v = default_voters()
    v.update(visual=FixedVoter("visual", "long"), technical=FixedVoter("technical", "long"))
    return v


@pytest.fixture
def store(tmp_path):
    s = SqliteAgentStore(str(tmp_path / "agents.sqlite"))
    yield s
    s.close()


def rows(store, table, run_id, drop=("run_id", "created_ts")):
    cur = store.con.execute(f"SELECT * FROM {table} WHERE run_id = ? ORDER BY 1, 2, 3", (run_id,))
    return [{k: r[k] for k in r.keys() if k not in drop} for r in cur]


def debate_graph(store, db, bull, bear, voters=None, judge=None):
    return build_graph(ON, store, db, voters=voters or long_voters(), debaters={"bull": bull, "bear": bear},
                       judge=judge, clock=FIXED)


# A1 ------------------------------------------------------------------------

def test_a1_snapshot_never_contains_a_bar_at_or_after_t(store, replay_db):
    g = build_graph(OFF, store, replay_db)
    t, n_done = DATA_START + timedelta(hours=6, seconds=41), 0
    while t < DATA_END + timedelta(hours=2):                  # every 37 min, off the minute, past the data end
        out = run_once(g, ev(t, t.isoformat()))
        if out.get("snap") is not None:
            snap = store.load_snapshot(out["snap"].hash)       # what was stored, not the in-memory object
            for series in snap.bars.values():
                assert all(b.ts < snap.as_of <= t for b in series)
            n_done += out["status"] == "done"
        t += timedelta(minutes=37, seconds=13)
    assert n_done > 50


# A2 ------------------------------------------------------------------------

def test_a2_same_inputs_same_snapshot_hash(tmp_path, replay_db):
    a = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    b = SqliteAgentStore(str(tmp_path / "b.sqlite"))
    other_db = make_replay_db(tmp_path)                       # datafeed store rebuilt from the same parquet
    ha = run_once(build_graph(OFF, a, replay_db), ev())["snap"].hash
    hb = run_once(build_graph(OFF, b, other_db), ev(T.replace(second=2)))["snap"].hash
    assert ha == hb
    assert a.con.execute("SELECT content_json FROM snapshots").fetchone()[0] == \
        b.con.execute("SELECT content_json FROM snapshots").fetchone()[0]
    assert run_once(build_graph(OFF, a, replay_db), ev(T + timedelta(minutes=1)))["snap"].hash != ha


# A3 ------------------------------------------------------------------------

def test_a3_raising_node_becomes_abstain_and_graph_finishes(store, replay_db):
    voters = long_voters()
    voters["technical"] = RaisingVoter()
    out = run_once(build_graph(OFF, store, replay_db, voters=voters, clock=FIXED), ev(), run_id="a3")
    assert out["status"] == "done"
    tech = [r for r in rows(store, "votes", "a3") if r["agent"] == "technical"][0]
    assert (tech["abstain"], tech["direction"], tech["confidence"], tech["abstain_reason"]) == \
        (1, "none", 0.0, "error: RuntimeError: boom")
    assert store.load_run("a3")["run"].status == "done"
    assert [r["arm"] for r in rows(store, "decisions", "a3")] == ["blind"]


# A4 ------------------------------------------------------------------------

def test_a4_debate_disabled_only_blind(store, replay_db):
    bull, bear = RecordingDebater(FakeBull()), RecordingDebater(FakeBear())
    run_once(build_graph(OFF, store, replay_db, voters=long_voters(), debaters={"bull": bull, "bear": bear}),
             ev(), run_id="a4")
    assert [r["arm"] for r in rows(store, "decisions", "a4")] == ["blind"]
    assert rows(store, "debate_turns", "a4") == [] and store.load_run("a4")["debate"] is None
    assert bull.calls == bear.calls == []


# A5 ------------------------------------------------------------------------

def test_a5_debate_enabled_two_decisions_round1_unchanged(store, replay_db):
    run_once(build_graph(OFF, store, replay_db, voters=long_voters(), clock=FIXED), ev(), run_id="off")
    run_once(debate_graph(store, replay_db, FakeBull(n_valid=2), FakeBear()), ev(), run_id="on")
    dec = rows(store, "decisions", "on")
    assert [d["arm"] for d in dec] == ["blind", "debate"]
    run_snap = store.con.execute("SELECT snapshot_hash FROM runs WHERE run_id = 'on'").fetchone()[0]
    assert dec[0]["snapshot_hash"] == dec[1]["snapshot_hash"] == run_snap
    assert run_snap == store.con.execute("SELECT snapshot_hash FROM runs WHERE run_id = 'off'").fetchone()[0]
    # round-1 rows are the same with and without the debate
    assert rows(store, "votes", "on") == rows(store, "votes", "off")
    assert all(r["round"] == 1 for r in rows(store, "votes", "on"))
    assert dec[0] == rows(store, "decisions", "off")[0]
    assert (dec[1]["direction"], dec[1]["debate_outcome"], dec[1]["decided_by"]) == ("long", "long", "code_v0")


# A6 ------------------------------------------------------------------------

def test_a6_nonexistent_evidence_ref_is_invalid_and_ignored(store, replay_db):
    spy = SpyJudge(CodeJudgeV0())
    voters = default_voters()
    voters.update(visual=FixedVoter("visual", "short"), technical=FixedVoter("technical", "short"))
    # bull: 6 claims a round, 5 unsupported; bear: 2 supported. Counting all claims, bull would win.
    out = run_once(debate_graph(store, replay_db, FakeBull(n_valid=1, n_invalid=5), FakeBear(n_valid=2),
                                voters=voters, judge=spy), ev(), run_id="a6")
    turns = rows(store, "debate_turns", "a6")
    bull = [c for t in turns if t["role"] == "bull" for c in json.loads(t["claims_json"])]
    assert len(bull) == 12 and sum(not c["valid"] for c in bull) == 10                     # kept, flagged
    assert all(c["invalid_reason"] for c in bull if not c["valid"])
    (inp,) = spy.inputs
    assert all(c.valid for t in inp.turns for c in t.claims)                               # judge saw valid only
    assert sum(len(t.claims) for t in inp.turns if t.role == "bull") == 2
    assert out["final_decisions"]["debate"].direction == "short"


# A7 ------------------------------------------------------------------------

def test_a7_tie_is_hold(store, replay_db):
    out = run_once(debate_graph(store, replay_db, FakeBull(n_valid=2), FakeBear(n_valid=2)), ev(), run_id="tie")
    d = rows(store, "decisions", "tie")[1]
    assert (d["debate_outcome"], d["direction"], d["reason"]) == ("hold", "none", "tie: bull 4, bear 4 valid claims")
    assert out["final_decisions"]["blind"].direction == "long"                             # blind arm unaffected


def test_a7_debater_error_debate_still_finishes(store, replay_db):
    out = run_once(debate_graph(store, replay_db, RaisingDebater(), FakeBear()), ev(), run_id="err")
    assert out["status"] == "done"
    turns = rows(store, "debate_turns", "err")
    assert len(turns) == 4
    assert all(t["error"] == "error: RuntimeError: debater down" and t["n_claims"] == 0
               for t in turns if t["role"] == "bull")
    assert rows(store, "decisions", "err")[1]["debate_outcome"] == "hold"     # bear ahead but round 1 is long


# A8 ------------------------------------------------------------------------

def test_a8_debater_sees_only_snapshot_votes_and_opponent_previous_turn(store, replay_db):
    bull, bear = RecordingDebater(FakeBull(n_valid=2, n_invalid=1)), RecordingDebater(FakeBear())
    run_once(debate_graph(store, replay_db, bull, bear), ev(), run_id="a8")
    rec = store.load_run("a8")
    snap = store.load_snapshot(rec["run"].snapshot_hash)
    votes = tuple(content_only(v) for v in rec["votes"])
    turns = {(t.role, t.round): t for t in rec["debate"].turns}
    for role, r in (("bull", bull), ("bear", bear)):
        assert len(r.calls) == 2
        for args, kwargs in r.calls:
            assert kwargs == {} and len(args) == 1 and type(args[0]) is DebaterView
            view = args[0]
            assert set(view.model_dump()) == {"role", "round", "snapshot", "round1_votes", "opponent_previous_turn"}
            assert view.role == role and view.snapshot == snap and view.round1_votes == votes
            opp = "bear" if role == "bull" else "bull"
            want = None if view.round == 1 else content_only(turns[(opp, view.round - 1)])
            assert view.opponent_previous_turn == want


# A9 ------------------------------------------------------------------------

def test_a9_same_snapshot_same_fakes_identical_debate_result(tmp_path, replay_db):
    results = []
    for name, db in (("x", replay_db), ("y", make_replay_db(tmp_path))):
        s = SqliteAgentStore(str(tmp_path / f"{name}.sqlite"))
        run_once(debate_graph(s, db, FakeBull(n_valid=2, n_invalid=2), FakeBear(n_valid=1, n_invalid=1)),
                 ev(), run_id=name)
        results.append(s.load_run(name)["debate"])
        s.close()
    assert results[0] == results[1] and results[0].turns


# real parquet ----------------------------------------------------------------

def test_real_parquet_day_through_the_graph(tmp_path):
    if not PARQUET.exists():
        pytest.skip("historical parquet not present (untracked)")
    from datafeed.bus import InMemoryBus
    from datafeed.ingest.xauusd_bars import STREAM, ReplaySource
    from datafeed.recorder.recorder import Recorder
    from datafeed.store import schema

    bus = InMemoryBus()
    ReplaySource(str(PARQUET), start="2025-06-04", end="2025-06-12").run(lambda m: bus.publish(STREAM, m))
    db = str(tmp_path / "datafeed.sqlite")
    con = schema.connect(db)
    Recorder(bus, con, STREAM, block_ms=None, sleep=lambda s: None).run(stop_when_idle=True)
    con.close()

    store = SqliteAgentStore(str(tmp_path / "agents.sqlite"))
    g = debate_graph(store, db, FakeBull(n_valid=2, n_invalid=1), FakeBear(n_valid=1))
    t0 = datetime(2025, 6, 11, tzinfo=UTC) + timedelta(seconds=17)
    outs = [run_once(g, ev(t0 + timedelta(minutes=30 * i), str(i))) for i in range(48)]
    done = [o for o in outs if o["status"] == "done"]
    skipped = [o for o in outs if o["status"] == "skipped"]
    # Only the daily break (21:00-22:00 UTC in June) is skipped. 22:00 too: the
    # market has just reopened, but the newest bar is still 20:59, so it's stale.
    assert [o["as_of"].strftime("%H:%M") for o in skipped] == ["21:00", "21:30", "22:00"]
    assert skipped[0]["skip_reason"].startswith("gate: market closed")
    assert all(o["skip_reason"].startswith("stale:") for o in skipped[1:])
    assert len(done) == 45
    for o in done:
        snap = store.load_snapshot(o["snap"].hash)
        assert all(b.ts < o["event"].t for s in snap.bars.values() for b in s)
        assert len(snap.bars["1m"]) == 240 and snap.bars["4h"]
        assert {d.arm for d in store.load_run(o["run_id"])["decisions"]} == {"blind", "debate"}
    store.close()
