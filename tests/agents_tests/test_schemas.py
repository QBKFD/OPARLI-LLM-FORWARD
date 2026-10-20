"""Step 2: schema invariants."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agents.hashing import canonical_json, sha256_hex
from agents.schemas import (Bar, Decision, DiscussionResult, Evidence, RejectedChange, Snapshot, TriggerEvent,
                            Vote)

UTC = timezone.utc
AS_OF = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)


def _bars_1m(n=5, end=AS_OF):
    return tuple(Bar(ts=end - timedelta(minutes=n - i), open=100.0 + i, high=101.0 + i,
                     low=99.0 + i, close=100.5 + i, kind="replay") for i in range(n))


def _snap(**kw):
    one = _bars_1m()
    base = dict(symbol="XAUUSD", as_of=AS_OF, kinds=("replay",), bars={"1m": one},
                newest_bar_ts=one[-1].ts)
    return Snapshot(**{**base, **kw})


# ---- Vote

def test_abstain_vote_shape():
    v = Vote.abstained("visual", "stub")
    assert (v.direction, v.confidence, v.abstain, v.abstain_reason, v.round) == ("none", 0.0, True, "stub", 1)


@pytest.mark.parametrize("bad", [
    dict(direction="long", confidence=0.5, abstain=True, abstain_reason="x"),      # abstain with a direction
    dict(direction="none", confidence=0.0, abstain=True),                          # abstain without a reason
    dict(direction="long", confidence=0.5, abstain_reason="x"),                    # reason without abstain
    dict(direction="LONG", confidence=0.5),                                        # not an allowed value
    dict(direction="long", confidence=1.5),
    dict(direction="long", confidence=float("nan")),
    dict(direction="long", confidence=0.5, extra_field=1),
])
def test_invalid_votes_are_rejected(bad):
    with pytest.raises(ValidationError):
        Vote(agent="technical", **bad)


def test_vote_is_frozen():
    v = Vote(agent="technical", direction="long", confidence=0.7, reason="r")
    with pytest.raises(ValidationError):
        v.direction = "short"


# ---- evidence, discussion votes, DiscussionResult

def test_evidence_validity_needs_reason():
    with pytest.raises(ValidationError):
        Evidence(ref="feature:a", claim="x", valid=False)
    with pytest.raises(ValidationError):
        Evidence(ref="feature:a", claim="x", valid=True, invalid_reason="no")
    with pytest.raises(ValidationError):
        Evidence(ref="feature:a", claim="x" * 201)


def test_discussion_fields_only_on_discussion_votes():
    rej = RejectedChange(direction="short", confidence=0.8)
    with pytest.raises(ValidationError):                                   # round 1 can't carry them
        Vote(agent="a", round=1, direction="long", confidence=0.5, changed=True)
    with pytest.raises(ValidationError):
        Vote(agent="a", round=2, direction="long", confidence=0.5, changed=True, change_rejected="x")
    with pytest.raises(ValidationError):                                   # a refused vote needs its reason
        Vote(agent="a", round=2, direction="long", confidence=0.5, rejected_proposal=rej)
    v = Vote(agent="a", round=2, direction="long", confidence=0.5, change_rejected="no new snapshot ref",
             rejected_proposal=rej)
    assert v.rejected_proposal.direction == "short"


def test_discussion_result_is_checked():
    v2 = Vote(agent="a", round=2, direction="long", confidence=0.5)
    v3 = Vote(agent="a", round=3, direction="long", confidence=0.5)
    ok = DiscussionResult(snapshot_hash="h", max_rounds=2, rounds_run=2, stopped_early=False, speakers=("a",),
                          votes=(v2, v3), final={"a": v3})
    assert ok.rounds_run == 2
    bad = [
        {"votes": (v3, v2)},                                      # out of order
        {"votes": (v2, v3), "rounds_run": 1},                     # a vote past rounds_run
        {"speakers": ()},                                         # a vote by a non-speaker
        {"rounds_run": 0, "votes": ()},                           # didn't run, but no skip_reason
        {"skip_reason": "x"},                                     # ran, but has a skip_reason
    ]
    for kw in bad:
        with pytest.raises(ValidationError):
            DiscussionResult(**{**ok.model_dump(), **kw})


def test_decision_risk_fields_default_null():
    d = Decision(run_id="r", snapshot_hash="h", arm="blind", direction="none", reason="x", decided_by="quorum_v0")
    assert (d.entry, d.sl, d.tp, d.size, d.sigma) == (None,) * 5
    for arm in ("discussion", "technical_v1"):
        Decision(run_id="r", snapshot_hash="h", arm=arm, direction="none", reason="x", decided_by="a")
    for arm in ("other", "debate"):                                  # the debate arm is history only
        with pytest.raises(ValidationError):
            Decision(run_id="r", snapshot_hash="h", arm=arm, direction="none", reason="x", decided_by="a")


# ---- Snapshot

def test_snapshot_hash_is_content_hash():
    s = _snap()
    assert s.hash == s.content_hash() == _snap().hash
    assert len(s.hash) == 64
    assert _snap(features={"x": 1.0}).hash != s.hash


def test_snapshot_round_trips_through_json():
    s = _snap()
    back = Snapshot.model_validate(json.loads(s.to_json()))
    assert back == s and back.hash == s.hash


def test_snapshot_tamper_is_detected():
    d = json.loads(_snap().to_json())
    d["bars"]["1m"][0]["close"] += 0.01
    with pytest.raises(ValidationError, match="does not match"):
        Snapshot.model_validate(d)


def test_snapshot_refuses_bar_at_or_after_as_of():
    one = _bars_1m() + (Bar(ts=AS_OF, open=1.0, high=1.0, low=1.0, close=1.0),)
    with pytest.raises(ValidationError, match="not before as_of"):
        _snap(bars={"1m": one}, newest_bar_ts=AS_OF)


def test_snapshot_checks_complete_flag_and_alignment():
    one = _bars_1m()
    # 4h buckets open at NY 17/21/01/05/09/13: 13:00 UTC is 09:00 EDT, a bucket open
    partial = Bar(ts=datetime(2025, 6, 11, 13, tzinfo=UTC), open=1.0, high=2.0, low=0.5, close=1.5, n_1m=90,
                  complete=True)                                      # 13:00 + 4h > 14:30: partial
    with pytest.raises(ValidationError, match="complete"):
        _snap(bars={"1m": one, "4h": (partial,)})
    misaligned = Bar(ts=datetime(2025, 6, 11, 12, tzinfo=UTC), open=1.0, high=2.0, low=0.5, close=1.5,
                     complete=False)                                  # 12:00 UTC = 08:00 EDT: not an anchor
    with pytest.raises(ValidationError, match="aligned"):
        _snap(bars={"1m": one, "4h": (misaligned,)})


def test_snapshot_requires_aware_whole_minute_as_of():
    with pytest.raises(ValidationError):
        _snap(as_of=AS_OF.replace(tzinfo=None))
    with pytest.raises(ValidationError):
        _snap(as_of=AS_OF + timedelta(seconds=30))


def test_snapshot_normalises_to_utc():
    cet = timezone(timedelta(hours=2))
    assert _snap(as_of=AS_OF.astimezone(cet)).hash == _snap().hash


# ---- TriggerEvent / hashing

def test_trigger_payload_must_be_json():
    TriggerEvent(source="clock", t=AS_OF, trigger_id="1", payload={"a": [1, "b"]})
    with pytest.raises(ValidationError):
        TriggerEvent(source="clock", t=AS_OF, trigger_id="1", payload={"a": {1, 2}})
    with pytest.raises(ValidationError):
        TriggerEvent(source="clock", t=AS_OF.replace(tzinfo=None), trigger_id="1")


def test_canonical_json_is_order_independent_and_strict():
    assert canonical_json({"b": 1, "a": (1.5, None)}) == '{"a":[1.5,null],"b":1}'
    assert sha256_hex({"a": 1, "b": 2}) == sha256_hex({"b": 2, "a": 1})
    for bad in (float("inf"), {1: "x"}, datetime(2025, 1, 1), b"x"):
        with pytest.raises((TypeError, ValueError)):
            canonical_json(bad)
