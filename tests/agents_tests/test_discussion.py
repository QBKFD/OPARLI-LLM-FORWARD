"""The discussion: the vote-change rule, the subgraph and the run-graph step,
with deterministic fake discussants. Also the evidence refs (moved from the
old debate tests)."""

import time
from datetime import datetime, timezone

import pytest

from agents.config import SnapshotConfig
from agents.discussion import (NO_NEW_REF, ONLY_VOTE_REFS, apply_change_rule, build_discussion_graph,
                               content_only, make_discussion, run_discussion, safe_discuss, speakers_of)
from agents.evidence import check_ref, check_vote_ref
from agents.owner_config import OwnerConfig
from agents.prompting.files import PromptProblem, parse_prompt
from agents.schemas import DiscussionView, Event, Evidence, Level, Snapshot, Vote
from agents.snapshot import build_snapshot
from agents_fakes import FakeDiscussant, bar_ref

UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)
OWNER = OwnerConfig.from_text('[meta]\nrule = "quorum_v0"\n[discussion]\nrounds = 3\n')
DISC = parse_prompt(b"---\nagent: discussion\nversion: 1\n---\n## user\n{{other_votes_json}}\n", "discussion")


@pytest.fixture(scope="module")
def snap(replay_db):
    s = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db)
    assert isinstance(s, Snapshot)
    return s


def vote(agent="visual", round_=1, direction="long", confidence=0.6, refs=(), **kw):
    ev = tuple(Evidence(ref=r, claim="c", valid=not r.startswith("bad"),
                        invalid_reason="no" if r.startswith("bad") else None) for r in refs)
    return Vote(agent=agent, round=round_, direction=direction, confidence=confidence, reason="x", evidence=ev, **kw)


# ---- the change rule

def test_a_restated_vote_needs_no_new_evidence():
    prev = vote(refs=("level:a",))
    out = apply_change_rule(prev, vote(round_=2, refs=(), call_id="c2"), 2)
    assert (out.round, out.direction, out.changed, out.change_rejected, out.call_id) == (2, "long", False, None, "c2")


def test_a_change_with_a_new_snapshot_ref_is_accepted():
    prev = vote(refs=("level:a",))
    out = apply_change_rule(prev, vote(round_=2, direction="short", refs=("level:a", "feature:b")), 2)
    assert (out.direction, out.changed, out.change_rejected) == ("short", True, None)


def test_a_confidence_change_counts_as_a_change():
    prev = vote(confidence=0.6, refs=("level:a",))
    out = apply_change_rule(prev, vote(round_=2, confidence=0.9, refs=("level:a",)), 2)
    assert (out.confidence, out.changed, out.change_rejected) == (0.6, False, NO_NEW_REF)


@pytest.mark.parametrize("refs, why", [
    (("level:a",), NO_NEW_REF),                         # only refs it already cited
    (("bad:x",), NO_NEW_REF),                           # a new ref that doesn't resolve
    (("vote:technical:1",), ONLY_VOTE_REFS),            # another agent's say-so is not evidence
    (("vote:technical:1", "level:a"), ONLY_VOTE_REFS),
])
def test_a_change_without_new_snapshot_evidence_is_refused_and_logged(refs, why):
    prev = vote(direction="long", refs=("level:a",))
    proposal = vote(round_=2, direction="short", confidence=0.8, refs=refs, call_id="c2", model_id="m")
    out = apply_change_rule(prev, proposal, 2)
    assert (out.round, out.direction, out.confidence, out.evidence) == (2, "long", 0.6, prev.evidence)
    assert (out.changed, out.change_rejected) == (False, why)
    assert out.rejected_proposal.direction == "short" and out.rejected_proposal.evidence == proposal.evidence
    assert (out.call_id, out.model_id) == ("c2", "m")              # provenance of this round's attempt


def test_a_failed_proposal_carries_the_previous_vote_forward():
    prev = vote(refs=("level:a",))
    out = apply_change_rule(prev, Vote.abstained("visual", "api_error 529: busy", round=2, call_id="c2"), 2)
    assert (out.direction, out.abstain, out.change_rejected, out.rejected_proposal) == \
        ("long", False, "call failed: api_error 529: busy", None)


def test_rule_misuse_raises():
    with pytest.raises(ValueError):
        apply_change_rule(Vote.abstained("visual", "x"), vote(round_=2), 2)
    with pytest.raises(ValueError):
        apply_change_rule(vote(), vote(agent="technical", round_=2), 2)


# ---- safe_discuss

def view(snap, agent="visual", r=1):
    return DiscussionView(agent=agent, round=r, max_rounds=2, snapshot=snap, own_previous=vote(agent=agent),
                          others=())


class Raises:
    def discuss(self, view):
        raise RuntimeError("down")


class Slow:
    def discuss(self, view):
        time.sleep(5)


@pytest.mark.parametrize("agent, why", [
    (Raises(), "error: RuntimeError: down"),
    (Slow(), "timeout:"),
    (type("Bad", (), {"discuss": lambda self, v: {"direction": "long"}})(), "invalid proposal: expected Vote, got dict"),
    (type("Other", (), {"discuss": lambda self, v: vote(agent="technical", round_=2)})(), "invalid proposal"),
    (type("Round", (), {"discuss": lambda self, v: vote(round_=3)})(), "invalid proposal"),
])
def test_safe_discuss_turns_failures_into_abstains(snap, agent, why):
    out = safe_discuss("visual", agent, view(snap), timeout_s=0.2)
    assert out.abstain and out.round == 2 and out.abstain_reason.startswith(why)


# ---- the subgraph

def run(snap, discussants, round1, rounds=3, early_stop=True):
    g = build_discussion_graph(discussants, rounds, early_stop, timeout_s=5)
    return run_discussion(g, snap, round1, speakers_of(round1, discussants), rounds)


def test_rounds_are_simultaneous_and_views_are_content_only(snap):
    a = FakeDiscussant("visual", "long")
    b = FakeDiscussant("technical", "short",
                       script=lambda v: {"direction": "long", "confidence": 0.5, "refs": [bar_ref(v.snapshot, 2)]})
    r1 = {"visual": a.vote(snap).model_copy(update={"model_id": "m", "tokens_in": 5}),
          "technical": b.vote(snap), "gex": Vote.abstained("gex", "no options data source")}
    res = run(snap, {"visual": a, "technical": b}, r1)
    first = {v.agent: v for v in [*a.views, *b.views] if v.round == 1}
    assert first["technical"].others[1] == content_only(r1["visual"])     # sees round-1 content, no model id
    assert first["technical"].others[1].model_id is None
    assert {o.agent for o in first["visual"].others} == {"gex", "technical"}   # abstained agents shown as they are
    assert [(v.round, v.agent) for v in res.votes][:2] == [(2, "technical"), (2, "visual")]
    changed = {v.round: v.changed for v in res.votes if v.agent == "technical"}
    assert res.final["technical"].direction == "long" and changed == {2: True, 3: False}   # then restated
    second = [v for v in a.views if v.round == 2][0]
    assert {o.agent: o.direction for o in second.others}["technical"] == "long"   # round 2 sees round-1 results
    assert res.final["gex"] == r1["gex"] and res.speakers == ("technical", "visual")


def test_early_stop_after_a_round_without_changes(snap):
    a, b = FakeDiscussant("visual"), FakeDiscussant("technical", "short")
    res = run(snap, {"visual": a, "technical": b}, {"visual": a.vote(snap), "technical": b.vote(snap)})
    assert (res.rounds_run, res.stopped_early, len(res.votes)) == (1, True, 2)


def test_without_early_stop_every_round_runs(snap):
    a = FakeDiscussant("visual")
    res = run(snap, {"visual": a}, {"visual": a.vote(snap)}, rounds=3, early_stop=False)
    assert (res.rounds_run, res.stopped_early, [v.round for v in res.votes]) == (3, False, [2, 3, 4])


def test_max_rounds_bounds_a_discussion_that_keeps_changing(snap):
    flip = {"long": "short", "short": "long"}
    a = FakeDiscussant("visual", script=lambda v: {"direction": flip[v.own_previous.direction], "confidence": 0.6,
                                                   "refs": [bar_ref(v.snapshot, v.round + 1)]})
    res = run(snap, {"visual": a}, {"visual": a.vote(snap)}, rounds=2)
    assert (res.rounds_run, res.stopped_early) == (2, False)
    assert [v.direction for v in res.votes] == ["short", "long"] and all(v.changed for v in res.votes)


def test_abstained_agents_do_not_speak(snap):
    a, b = FakeDiscussant("visual"), FakeDiscussant("technical", abstain=True)
    res = run(snap, {"visual": a, "technical": b}, {"visual": a.vote(snap), "technical": b.vote(snap)})
    assert res.speakers == ("visual",) and b.views == [] and res.final["technical"].abstain


def test_a_failing_speaker_keeps_its_vote_and_the_discussion_finishes(snap):
    a = FakeDiscussant("visual")
    b = FakeDiscussant("technical", "short", script=lambda v: (_ for _ in ()).throw(RuntimeError("down")))
    res = run(snap, {"visual": a, "technical": b}, {"visual": a.vote(snap), "technical": b.vote(snap)})
    t = [v for v in res.votes if v.agent == "technical"][0]
    assert (t.direction, t.change_rejected) == ("short", "call failed: error: RuntimeError: down")


def test_a_vote_ref_resolves_only_for_votes_shown():
    shown = {("technical", 1), ("visual", 1)}
    assert check_vote_ref("vote:technical:1", shown) is None
    assert "was not shown" in check_vote_ref("vote:technical:2", shown)
    assert "vote:<agent>:<round>" in check_vote_ref("vote:technical", shown)
    assert "not a vote" in check_vote_ref("level:x", shown)


# ---- the run-graph step: run, or record why not

def test_make_discussion_skip_reasons(snap):
    a = FakeDiscussant("visual")
    r1 = {"visual": a.vote(snap)}
    bad_cfg = OwnerConfig.from_text("[discussion]\nrounds = 0\n")
    assert make_discussion({"visual": a}, bad_cfg, DISC, 5)(snap, r1).skip_reason.startswith(
        "config_invalid: discussion.rounds")
    missing = PromptProblem("discussion.md", "the unfilled template")
    assert make_discussion({"visual": a}, OWNER, missing, 5)(snap, r1).skip_reason == \
        "prompt_invalid: discussion.md: the unfilled template"
    assert make_discussion({}, OWNER, DISC, 5)(snap, r1).skip_reason == "no LLM discussants in this graph"
    abst = {"visual": Vote.abstained("visual", "x")}
    res = make_discussion({"visual": a}, OWNER, DISC, 5)(snap, abst)
    assert res.skip_reason == "every discussant abstained in round 1" and res.final == abst
    ok = make_discussion({"visual": a}, OWNER, DISC, 5)(snap, r1)
    assert ok.skip_reason is None and ok.max_rounds == 3 and ok.rounds_run == 1


# ---- evidence refs (from the old debate tests)

@pytest.mark.parametrize("ref, ok", [
    ("bars:1m:{last}..{last}", True),
    ("bars:15m:{q0}..{q1}", True),
    ("bars:1m:{last_z}..{last_z}", True),                         # 'Z' suffix is the same instant
    ("bars:1m:{last}..{first}", False),                           # reversed
    ("bars:1m:2025-06-11T14:30:00+00:00..2025-06-11T14:30:00+00:00", False),   # the forming minute
    ("bars:1m:2025-06-11T14:29:30+00:00..2025-06-11T14:29:30+00:00", False),   # not a bar open time
    ("bars:1m:2025-06-11T14:29:00..2025-06-11T14:29:00", False),  # naive
    ("bars:1d:{last}..{last}", False),                            # timeframe not in the snapshot
    ("bars:1m:{last}", False),
    ("level:pdh", False), ("feature:atr", False), ("event:nfp", False), ("metric:rsi14_1h", True),
    ("metric:volume", False), ("metric:atr14_4h_sigma", False),   # unavailable rows never resolve
    ("vibes", False), ("", False), ("chart:1m", False), ("vote:visual:1", False),     # vote: only in a discussion
])
def test_check_ref(snap, ref, ok):
    one, q = snap.bars["1m"], snap.bars["15m"]
    ref = ref.format(last=one[-1].ts.isoformat(), first=one[0].ts.isoformat(),
                     last_z=one[-1].ts.isoformat().replace("+00:00", "Z"),
                     q0=q[0].ts.isoformat(), q1=q[-1].ts.isoformat())
    assert (check_ref(ref, snap) is None) == ok, check_ref(ref, snap)


def test_level_feature_event_refs_resolve_when_present(snap):
    filled = {"levels", "features", "events"}
    rich = Snapshot(**{**snap.model_dump(exclude={"hash", "unavailable", *filled}),
                       "levels": (Level(id="pdh", type="PDH", value=3350.0, active_from=snap.as_of),),
                       "features": {"atr_14": 1.2},
                       "events": (Event(id="nfp-2025-06", ts=datetime(2025, 6, 6, 12, 30, tzinfo=UTC)),),
                       "unavailable": {k: v for k, v in snap.unavailable.items() if k not in filled}})
    assert [check_ref(r, rich) for r in ("level:pdh", "feature:atr_14", "event:nfp-2025-06")] == [None] * 3
