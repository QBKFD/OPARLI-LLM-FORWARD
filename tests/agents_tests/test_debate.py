"""Step 5: debate subgraph on a replay snapshot."""

from datetime import datetime, timezone

import pytest

from agents.config import AgentsConfig, DebateConfig, SnapshotConfig
from agents.debate import CodeJudgeV0, DebaterView, build_debate_graph, check_ref, make_judge, run_debate
from agents.debate.debaters import safe_turn
from agents.debate.subgraph import content_only
from agents.graph import build_graph
from agents.hashing import canonical_json
from agents.schemas import Claim, Turn, Vote
from agents.snapshot import build_snapshot
from agents_fakes import (FakeBear, FakeBull, RaisingDebater, RaisingJudge, RecordingDebater, ReturnsDebater,
                          ReturnsJudge, SlowDebater, SpyJudge)

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, 30, tzinfo=UTC)
FIXED = lambda: 0.0                                           # noqa: E731 - latency 0 in tests
VOTES = (Vote.abstained("sentiment", "stub"), Vote(agent="technical", direction="long", confidence=0.6),
         Vote(agent="visual", direction="long", confidence=0.7))


@pytest.fixture(scope="module")
def snap(replay_db):
    return build_snapshot(T, SnapshotConfig(mode="replay"), replay_db)


def debate(snap, bull, bear, *, blind="long", judge=None, max_rounds=2, timeout=5.0, clock=FIXED, votes=VOTES):
    cfg = DebateConfig(enabled=True, max_rounds=max_rounds, turn_timeout_s=timeout)
    g = build_debate_graph({"bull": bull, "bear": bear}, judge or CodeJudgeV0(), cfg, clock)
    return run_debate(g, snap, votes, blind, max_rounds)


# ---- structure

@pytest.mark.parametrize("rounds", [1, 2, 3])
def test_rounds(snap, rounds):
    r = debate(snap, FakeBull(), FakeBear(), max_rounds=rounds)
    assert [(t.round, t.role) for t in r.turns] == [(i, s) for i in range(1, rounds + 1) for s in ("bull", "bear")]
    assert r.max_rounds == rounds and r.snapshot_hash == snap.hash


def test_default_config():
    c = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}}).debate
    assert (c.enabled, c.max_rounds, c.judge) == (False, 2, "code_v0")


# ---- what a debater sees

def test_debater_sees_only_snapshot_votes_and_opponent_previous_turn(snap):
    bull, bear = RecordingDebater(FakeBull(n_valid=2)), RecordingDebater(FakeBear(n_valid=1, n_invalid=1))
    r = debate(snap, bull, bear, max_rounds=3)
    by = {(t.role, t.round): t for t in r.turns}
    for role, rec in (("bull", bull), ("bear", bear)):
        assert len(rec.calls) == 3
        for args, kwargs in rec.calls:
            assert kwargs == {} and len(args) == 1
            (view,) = args
            assert type(view) is DebaterView
            assert set(type(view).model_fields) == {"role", "round", "snapshot", "round1_votes",
                                                    "opponent_previous_turn"}
            assert view.role == role
            assert view.snapshot == snap and view.snapshot is not snap
            assert view.round1_votes == VOTES
            opp = "bear" if role == "bull" else "bull"
            if view.round == 1:
                assert view.opponent_previous_turn is None
            else:   # exactly the opponent's previous turn, evidence-checked, without timings
                assert view.opponent_previous_turn == content_only(by[(opp, view.round - 1)])
                assert view.opponent_previous_turn.role == opp


def test_opening_turns_are_blind_to_each_other(snap):
    # same fakes, opponent with a different claim count: opening turns must not change
    a = debate(snap, FakeBull(), FakeBear(n_valid=1))
    b = debate(snap, FakeBull(), FakeBear(n_valid=3))
    assert a.turns[0] == b.turns[0]                      # bull round 1
    assert a.turns[2] != b.turns[2]                      # bull round 2 saw a different bear turn


# ---- evidence check

@pytest.mark.parametrize("ref, ok", [
    ("bars:1m:{last}..{last}", True),
    ("bars:15m:{q0}..{q1}", True),
    ("bars:1m:{last_z}..{last_z}", True),                         # 'Z' suffix is the same instant
    ("bars:1m:{last}..{first}", False),                           # reversed
    ("bars:1m:2025-06-11T14:30:00+00:00..2025-06-11T14:30:00+00:00", False),   # the forming minute, not in snapshot
    ("bars:1m:2025-06-11T14:29:30+00:00..2025-06-11T14:29:30+00:00", False),   # not a bar open time
    ("bars:1m:2025-06-11T14:29:00..2025-06-11T14:29:00", False),  # naive
    ("bars:1d:{last}..{last}", False),                            # timeframe not in snapshot
    ("bars:1m:{last}", False),
    ("level:pdh", False), ("feature:atr", False), ("event:nfp", False),
    ("vibes", False), ("", False), ("chart:1m", False),
])
def test_check_ref(snap, ref, ok):
    one, q = snap.bars["1m"], snap.bars["15m"]
    ref = ref.format(last=one[-1].ts.isoformat(), first=one[0].ts.isoformat(),
                     last_z=one[-1].ts.isoformat().replace("+00:00", "Z"),
                     q0=q[0].ts.isoformat(), q1=q[-1].ts.isoformat())
    assert (check_ref(ref, snap) is None) == ok, check_ref(ref, snap)


def test_level_feature_event_refs_resolve_when_present(snap):
    from agents.schemas import Event, Level, Snapshot
    filled = {"levels", "features", "events"}
    rich = Snapshot(**{**snap.model_dump(exclude={"hash", "unavailable", *filled}),
                       "levels": (Level(id="pdh", type="PDH", value=3350.0, active_from=snap.as_of),),
                       "features": {"atr_14": 1.2},
                       "events": (Event(id="nfp-2025-06", ts=datetime(2025, 6, 6, 12, 30, tzinfo=UTC)),),
                       "unavailable": {k: v for k, v in snap.unavailable.items() if k not in filled}})
    assert [check_ref(r, rich) for r in ("level:pdh", "feature:atr_14", "event:nfp-2025-06")] == [None] * 3


def test_invalid_claim_is_kept_but_ignored_by_the_judge(snap):
    spy = SpyJudge(CodeJudgeV0())
    # bull makes 5 claims per round but only 1 is supported; bear makes 2 supported ones
    r = debate(snap, FakeBull(n_valid=1, n_invalid=4), FakeBear(n_valid=2), blind="short", judge=spy)
    bull_claims = [c for t in r.turns if t.role == "bull" for c in t.claims]
    assert len(bull_claims) == 10
    bad = [c for c in bull_claims if not c.valid]
    assert len(bad) == 8 and all(c.invalid_reason for c in bad)                  # kept in the log, flagged
    (inp,) = spy.inputs
    seen = [c for t in inp.turns for c in t.claims]
    assert seen and all(c.valid is True for c in seen)                           # the judge saw only valid ones
    assert (r.n_valid_bull, r.n_valid_bear, r.outcome) == (2, 4, "short")
    assert r.reason == "bear has more valid claims (bull 2, bear 4) and agrees with the round-1 direction short"


def test_debater_cannot_self_certify(snap):
    out = {"claims": [{"text": "trust me", "evidence_ref": "level:none", "valid": True},
                      Claim(text="also me", evidence_ref="vibes", valid=True)]}
    r = debate(snap, ReturnsDebater(out), FakeBear(n_valid=0))
    assert [c.valid for c in r.turns[0].claims] == [False, False] and r.n_valid_bull == 0


# ---- judge v0

@pytest.mark.parametrize("nb, ns, blind, outcome", [
    (1, 1, "long", "hold"),          # tie
    (0, 0, "short", "hold"),         # tie at zero
    (2, 1, "long", "long"),          # bull ahead and agrees
    (2, 1, "short", "hold"),         # bull ahead, round 1 disagrees
    (2, 1, "none", "hold"),          # round 1 had no direction
    (1, 3, "short", "short"),
    (1, 3, "long", "hold"),
])
def test_code_judge_v0(snap, nb, ns, blind, outcome):
    assert debate(snap, FakeBull(n_valid=nb), FakeBear(n_valid=ns), blind=blind).outcome == outcome


def test_tie_reason(snap):
    r = debate(snap, FakeBull(), FakeBear())
    assert (r.outcome, r.reason) == ("hold", "tie: bull 2, bear 2 valid claims")


def test_concession_is_recorded_but_not_used_by_v0(snap):
    r = debate(snap, FakeBull(n_valid=2, concede=True), FakeBear(n_valid=1))
    assert all(t.conceded for t in r.turns if t.role == "bull") and r.outcome == "long"


@pytest.mark.parametrize("judge, reason", [
    (RaisingJudge(), "judge error: RuntimeError: judge down"),
    (ReturnsJudge({"outcome": "maybe", "reason": "x"}), "judge error: invalid verdict: Input should be 'long', 'short' or 'hold'"),
    (ReturnsJudge("long"), "judge error: expected Verdict or dict, got str"),
])
def test_judge_error_is_hold(snap, judge, reason):
    r = debate(snap, FakeBull(n_valid=3), FakeBear(), judge=judge)
    assert (r.outcome, r.reason, r.judge_name) == ("hold", reason, judge.name)


def test_unknown_judge_fails_at_build(replay_db):
    with pytest.raises(ValueError, match="unknown judge"):
        make_judge("llm_v1")
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}, "debate": {"judge": "llm_v1"}})
    with pytest.raises(ValueError, match="unknown judge"):
        build_graph(cfg, None, replay_db)


def test_enabled_without_debaters_fails_at_build(replay_db):
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}, "debate": {"enabled": True}})
    with pytest.raises(ValueError, match="needs debaters"):
        build_graph(cfg, None, replay_db)


# ---- failing debaters: the debate still finishes

def test_debater_error_gives_empty_turns_and_the_debate_finishes(snap):
    r = debate(snap, RaisingDebater(), FakeBear(n_valid=1), blind="short")
    bull = [t for t in r.turns if t.role == "bull"]
    assert len(r.turns) == 4 and all(t.claims == () and t.error == "error: RuntimeError: debater down" for t in bull)
    assert (r.outcome, r.n_valid_bull, r.n_valid_bear) == ("short", 0, 2)


def test_debater_timeout_gives_an_empty_turn(snap):
    r = debate(snap, SlowDebater(5), FakeBear(), timeout=0.2, max_rounds=1)
    assert r.turns[0].error == "timeout: no result after 0.2s" and r.outcome == "hold"


@pytest.mark.parametrize("out, reason", [
    (None, "invalid turn: no turn returned"),
    ("long!", "invalid turn: expected Turn or dict, got str"),
    ({"role": "bear", "claims": []}, "invalid turn: role/round 'bear'/None, expected 'bull'/1"),
    ({"claims": "lots"}, "invalid turn: claims must be a list"),
    ({"claims": [{"text": "no ref"}]}, "invalid turn: claims.0.evidence_ref: Field required"),
    ({"claims": [], "error": "rate limited"}, "debater reported: rate limited"),
])
def test_invalid_turn_output_is_an_empty_turn(snap, out, reason):
    view = DebaterView(role="bull", round=1, snapshot=snap, round1_votes=VOTES)
    t = safe_turn(ReturnsDebater(out), view, 5.0, FIXED)
    assert (t.claims, t.error) == ((), reason)


def test_turn_records_prompt_hash_and_latency(snap):
    ticks = iter([1.0, 1.5])
    view = DebaterView(role="bear", round=1, snapshot=snap, round1_votes=VOTES)
    t = safe_turn(FakeBear(), view, 5.0, lambda: next(ticks))
    from agents.hashing import sha256_hex
    assert t.prompt_hash == sha256_hex(view) and t.latency_ms == 500.0 and t.model_id is None


# ---- determinism

def test_same_snapshot_and_fakes_give_an_identical_result(snap, replay_db):
    a = debate(snap, FakeBull(n_valid=2, n_invalid=1), FakeBear(n_valid=1, n_invalid=2), max_rounds=3)
    b = debate(snap, FakeBull(n_valid=2, n_invalid=1), FakeBear(n_valid=1, n_invalid=2), max_rounds=3)
    rebuilt = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db)      # same inputs, new object
    c = debate(rebuilt, FakeBull(n_valid=2, n_invalid=1), FakeBear(n_valid=1, n_invalid=2), max_rounds=3)
    assert a == b == c
    assert canonical_json(a) == canonical_json(b) == canonical_json(c)


def test_with_a_real_clock_only_latency_differs(snap):
    # Views carry no timings, so prompt hashes match even with a real clock.
    import time
    timed = (Vote(agent="technical", direction="long", confidence=0.6, latency_ms=812.0, model_id="m1", tokens_in=9),)
    a = debate(snap, FakeBull(), FakeBear(), clock=time.monotonic, votes=timed)
    b = debate(snap, FakeBull(), FakeBear(), clock=time.monotonic,
               votes=(timed[0].model_copy(update={"latency_ms": 3.0, "tokens_in": 7}),))
    strip = lambda r: [t.model_dump(exclude={"latency_ms"}) for t in r.turns]   # noqa: E731
    assert strip(a) == strip(b) and a.outcome == b.outcome and a.reason == b.reason


def test_view_hides_timings_and_provenance(snap):
    rec = RecordingDebater(FakeBear())
    votes = (Vote(agent="visual", direction="long", confidence=0.7, latency_ms=1234.0, model_id="m", tokens_out=5),)
    debate(snap, FakeBull(), rec, votes=votes, clock=iter(range(100)).__next__)
    for (view,), _ in rec.calls:
        for x in (*view.round1_votes, view.opponent_previous_turn):
            if x is not None:
                assert (x.model_id, x.prompt_hash, x.tokens_in, x.tokens_out, x.latency_ms) == (None,) * 5
    assert rec.calls[0][0][0].round1_votes[0].direction == "long"


def test_error_turn_cannot_hold_claims():
    with pytest.raises(Exception):
        Turn(role="bull", round=1, claims=(Claim(text="x", evidence_ref="y"),), error="e")


# ---- in the run graph

def _voters():
    from agents_fakes import FixedVoter
    from agents.voters import default_voters
    v = default_voters()
    v.update(visual=FixedVoter("visual", "long"), technical=FixedVoter("technical", "long"))
    return v


def test_graph_debate_disabled_never_calls_debaters(replay_db):
    from agents.graph import run_once
    from agents.schemas import TriggerEvent
    from agents.store import MemoryStore
    bull, bear = RecordingDebater(FakeBull()), RecordingDebater(FakeBear())
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
    out = run_once(build_graph(cfg, MemoryStore(), replay_db, voters=_voters(),
                               debaters={"bull": bull, "bear": bear}),
                   TriggerEvent(source="t", t=T, trigger_id="1"))
    assert list(out["final_decisions"]) == ["blind"] and "debate_result" not in out
    assert bull.calls == [] and bear.calls == []


def test_graph_debate_enabled_adds_a_debate_decision(replay_db):
    from agents.graph import run_once
    from agents.schemas import TriggerEvent
    from agents.store import MemoryStore
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}, "debate": {"enabled": True}})
    store = MemoryStore()
    out = run_once(build_graph(cfg, store, replay_db, voters=_voters(),
                               debaters={"bull": FakeBull(n_valid=2), "bear": FakeBear()}, clock=FIXED),
                   TriggerEvent(source="t", t=T, trigger_id="1"), run_id="d")
    blind, deb = out["final_decisions"]["blind"], out["final_decisions"]["debate"]
    assert (blind.direction, deb.direction, deb.decided_by) == ("long", "long", "code_v0")
    assert blind.snapshot_hash == deb.snapshot_hash == out["snap"].hash
    rec = store.runs["d"]
    assert [d.arm for d in rec["decisions"]] == ["blind", "debate"] and rec["debate"] == out["debate_result"]
