"""Graph wiring, voter isolation, abstain on failure, skips. The Meta rules are
tested in test_meta.py, the discussion in test_discussion.py."""

import threading
from datetime import datetime, timedelta, timezone

import pytest

from agents.config import AgentsConfig
from agents.graph import build_graph, merge_disjoint, run_once
from agents.schemas import Snapshot, TriggerEvent, Vote
from agents.snapshot import SealedError
from agents.store import MemoryStore
from agents.trigger import drive
from agents.voters import default_voters
from agents.voters.base import safe_vote, validate_vote
from agents_fakes import FixedVoter, MutatingVoter, RaisingVoter, RecordingVoter, SlowVoter

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
CFG = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})


def ev(t=T, i="1"):
    return TriggerEvent(source="test", t=t, trigger_id=i, payload={"note": "unit"})


def three(**over):
    v = default_voters()
    v.update(over)
    return v


# ---- the default graph

def test_stub_graph_runs_to_a_blind_none(replay_db):
    store = MemoryStore()
    out = run_once(build_graph(CFG, store, replay_db), ev(), run_id="r1")
    assert out["status"] == "done"
    assert sorted(out["votes"]) == ["gex", "sentiment", "technical", "visual"]
    assert all(v.abstain for v in out["votes"].values())
    assert out["votes"]["gex"].abstain_reason == "no options data source"
    blind = out["final_decisions"]["blind"]
    assert (blind.arm, blind.direction, blind.decided_by, blind.meta_rule) == ("blind", "none", "quorum_v0",
                                                                              "quorum_v0")
    assert blind.reason == "long 0, short 0, none 0, abstain 4 of 4: no direction has 2 agreeing non-abstaining votes"
    disc = out["final_decisions"]["discussion"]
    assert (disc.direction, out["discussion"].skip_reason) == ("none", "no LLM discussants in this graph")
    assert (blind.entry, blind.sl, blind.tp, blind.size, blind.sigma) == (None,) * 5     # risk stub
    rec = store.runs["r1"]
    assert rec["run"].status == "done" and rec["run"].snapshot_hash == out["snap"].hash
    assert [v.agent for v in rec["votes"]] == ["gex", "sentiment", "technical", "visual"]
    assert [d.arm for d in rec["decisions"]] == ["blind", "discussion"]
    assert rec["discussion"] == out["discussion"]
    assert rec["run"].trigger.payload == {"note": "unit"} and rec["run"].config_hash == CFG.config_hash()


def test_risk_stub_is_pass_through(replay_db):
    out = run_once(build_graph(CFG, MemoryStore(), replay_db), ev())
    assert out["final_decisions"] == out["decisions"]


# ---- failures become abstains; the graph still finishes

def test_a_raising_node_becomes_an_abstain(replay_db):
    store = MemoryStore()
    voters = three(technical=RaisingVoter(), visual=FixedVoter("visual", "long"), sentiment=FixedVoter("sentiment", "long"))
    out = run_once(build_graph(CFG, store, replay_db, voters=voters), ev(), run_id="r")
    t = out["votes"]["technical"]
    assert t.abstain and t.abstain_reason == "error: RuntimeError: boom" and t.direction == "none"
    assert out["votes"]["visual"].direction == "long"
    assert out["status"] == "done" and store.runs["r"]["run"].status == "done"
    assert out["final_decisions"]["blind"].direction == "long"          # 2 of 3 still a strict majority


def test_a_timeout_becomes_an_abstain(replay_db):
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}, "node_timeout_s": 0.2})
    out = run_once(build_graph(cfg, MemoryStore(), replay_db, voters=three(visual=SlowVoter(5))), ev())
    v = out["votes"]["visual"]
    assert v.abstain and v.abstain_reason == "timeout: no result after 0.2s"
    assert out["status"] == "done"


@pytest.mark.parametrize("out, reason", [
    (None, "invalid vote: no vote returned"),
    ("long", "invalid vote: expected Vote or dict, got str"),
    ({"direction": "LONG", "confidence": 0.5}, "invalid vote: direction: Input should be 'long', 'short' or 'none'"),
    ({"direction": "long", "confidence": "0.9"}, "invalid vote: confidence: Input should be a valid number"),
    ({"direction": "long"}, "invalid vote: confidence: Field required"),
    ({"agent": "visual", "direction": "long", "confidence": 0.5}, "invalid vote: names agent 'visual', expected 'technical'"),
    ({"direction": "long", "confidence": 0.5, "round": 2}, "invalid vote: round 2, a round-1 vote has round 1"),
    ({"direction": "long", "confidence": 0.5, "guess": True}, "invalid vote: guess: Extra inputs are not permitted"),
])
def test_invalid_output_is_an_abstain_never_a_guess(out, reason):
    v = validate_vote("technical", out)
    assert v.abstain and v.direction == "none" and v.abstain_reason == reason


def test_valid_dict_vote_is_accepted():
    v = validate_vote("technical", {"direction": "short", "confidence": 1, "reason": "r", "model_id": "m"}, 3.0)
    assert (v.agent, v.direction, v.confidence, v.abstain, v.model_id, v.latency_ms) == \
        ("technical", "short", 1.0, False, "m", 3.0)


def test_latency_uses_the_injected_clock(replay_db):
    ticks = iter([10.0, 10.25])
    snap = run_once(build_graph(CFG, MemoryStore(), replay_db), ev())["snap"]
    v = safe_vote("visual", FixedVoter("visual", "long"), snap, 1.0, clock=lambda: next(ticks))
    assert v.latency_ms == 250.0


# ---- blindness and ordering

def test_voters_run_in_parallel(replay_db):
    barrier = threading.Barrier(3, timeout=5)

    class Waits:
        def __init__(self, name):
            self.name = name

        def vote(self, snapshot):
            barrier.wait()                    # only passes if all three are running at once
            return Vote(agent=self.name, direction="short", confidence=0.5)

    out = run_once(build_graph(CFG, MemoryStore(), replay_db,
                               voters={n: Waits(n) for n in ("visual", "technical", "sentiment")}), ev())
    assert [v.direction for v in out["votes"].values()] == ["short"] * 3


def test_voters_get_only_a_copy_of_the_snapshot_saved_before_they_run(replay_db):
    store = MemoryStore()
    rec = {n: RecordingVoter(n, store) for n in ("visual", "technical", "sentiment")}
    out = run_once(build_graph(CFG, store, replay_db, voters=rec), ev())
    snap = out["snap"]
    for r in rec.values():
        (call,) = r.calls
        assert call["kwargs"] == {} and len(call["args"]) == 1
        (seen,) = call["args"]
        assert isinstance(seen, Snapshot) and seen == snap and seen is not snap
        assert call["saved_snapshots"] == {snap.hash}           # saved before the agent ran


def test_a_voter_cannot_change_what_others_see(replay_db):
    store = MemoryStore()
    rec = RecordingVoter("technical")
    out = run_once(build_graph(CFG, store, replay_db,
                               voters=three(visual=MutatingVoter(), technical=rec)), ev())
    (seen,) = rec.calls[0]["args"]
    assert seen.bars["1m"] and "injected" not in seen.features
    assert store.snapshots[out["snap"].hash].content_hash() == out["snap"].hash


# ---- skips

def test_stale_run_is_skipped_with_reason_and_nothing_else(replay_db):
    store = MemoryStore()
    rec = RecordingVoter("visual")
    out = run_once(build_graph(CFG, store, replay_db, voters=three(visual=rec)),
                   ev(datetime(2025, 6, 12, 10, 0, tzinfo=UTC)), run_id="s")      # feed ended, market open
    assert out["status"] == "skipped" and out["skip_reason"].startswith("stale: newest 1m bar")
    assert rec.calls == [] and store.snapshots == {}
    r = store.runs["s"]
    assert r["run"].status == "skipped" and r["run"].snapshot_hash is None
    assert r["votes"] == () and r["decisions"] == ()


def test_gate_market_closed(replay_db):
    # 21:03 UTC in June: newest bar 20:59 is fresh (3 min) but the market is in its daily break
    store = MemoryStore()
    out = run_once(build_graph(CFG, store, replay_db), ev(datetime(2025, 6, 10, 21, 3, tzinfo=UTC)), run_id="g")
    assert out["status"] == "skipped"
    assert out["skip_reason"] == "gate: market closed at as_of 2025-06-10T21:03:00+00:00"
    r = store.runs["g"]["run"]
    assert r.snapshot_hash in store.snapshots and store.runs["g"]["votes"] == ()


def test_the_reopen_trigger_is_not_skipped(replay_db):
    # 22:00 UTC in June: the market reopens; the newest bar (20:59) is the last open minute
    store = MemoryStore()
    out = run_once(build_graph(CFG, store, replay_db), ev(datetime(2025, 6, 10, 22, 0, 15, tzinfo=UTC)), run_id="o")
    assert out["status"] == "done" and store.runs["o"]["run"].status == "done"
    assert out["snap"].newest_bar_ts < datetime(2025, 6, 10, 21, 0, tzinfo=UTC)


def test_in_the_break_the_gate_says_why(replay_db):
    out = run_once(build_graph(CFG, MemoryStore(), replay_db), ev(datetime(2025, 6, 10, 21, 30, tzinfo=UTC)))
    assert out["skip_reason"] == "gate: market closed at as_of 2025-06-10T21:30:00+00:00"


def test_gate_min_bars(replay_db):
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}, "gate": {"min_bars": {"4h": 1000}}})
    out = run_once(build_graph(cfg, MemoryStore(), replay_db), ev())
    assert out["status"] == "skipped" and out["skip_reason"].startswith("gate: 4h:")


def test_sealed_t_raises(replay_db):
    with pytest.raises(SealedError):
        run_once(build_graph(CFG, MemoryStore(), replay_db), ev(datetime(2026, 10, 1, 0, 1, tzinfo=UTC)))


# ---- misc

def test_merge_disjoint_refuses_duplicates():
    assert merge_disjoint(None, {"a": 1}) == {"a": 1}
    with pytest.raises(ValueError):
        merge_disjoint({"a": 1}, {"a": 2})


def test_pluggable_trigger(replay_db):
    class ListTrigger:
        def events(self):
            return [ev(T, "a"), ev(T + timedelta(minutes=1), "b")]

    store = MemoryStore()
    outs = drive(ListTrigger(), build_graph(CFG, store, replay_db))
    assert [o["status"] for o in outs] == ["done", "done"] and len(store.runs) == 2
    assert len({o["snap"].hash for o in outs}) == 2
