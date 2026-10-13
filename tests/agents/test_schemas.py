"""Step 2: schema invariants."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agents.hashing import canonical_json, sha256_hex
from agents.schemas import (Bar, Claim, DebateResult, Decision, Snapshot, TriggerEvent, Turn, Vote,
                            outcome_to_direction)

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


# ---- Claim / Turn / DebateResult

def test_claim_validity_needs_reason():
    with pytest.raises(ValidationError):
        Claim(text="x", evidence_ref="feature:a", valid=False)
    with pytest.raises(ValidationError):
        Claim(text="x", evidence_ref="feature:a", valid=True, invalid_reason="no")


def test_error_turn_is_empty():
    with pytest.raises(ValidationError):
        Turn(role="bull", round=1, claims=(Claim(text="x", evidence_ref="y"),), error="timeout")
    t = Turn(role="bear", round=2, error="timeout")
    assert t.claims == () and t.n_valid() == 0


def test_debate_result_counts_and_order_are_checked():
    ok = Claim(text="a", evidence_ref="r", valid=True)
    turns = (Turn(role="bull", round=1, claims=(ok,)), Turn(role="bear", round=1))
    r = DebateResult(snapshot_hash="h", max_rounds=1, turns=turns, n_valid_bull=1, n_valid_bear=0,
                     judge_name="code_v0", outcome="hold", reason="x")
    assert r.n_valid_bull == 1
    with pytest.raises(ValidationError):           # wrong count
        DebateResult(**{**r.model_dump(), "n_valid_bull": 2})
    with pytest.raises(ValidationError):           # bear before bull
        DebateResult(**{**r.model_dump(), "turns": turns[::-1]})


def test_outcome_mapping():
    assert [outcome_to_direction(o) for o in ("long", "short", "hold")] == ["long", "short", "none"]


def test_decision_risk_fields_default_null():
    d = Decision(run_id="r", snapshot_hash="h", arm="blind", direction="none", reason="x", decided_by="aggregator_v0")
    assert (d.entry, d.sl, d.tp, d.size, d.sigma) == (None,) * 5
    with pytest.raises(ValidationError):
        Decision(run_id="r", snapshot_hash="h", arm="other", direction="none", reason="x", decided_by="a")


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
    partial = Bar(ts=datetime(2025, 6, 11, 12, tzinfo=UTC), open=1.0, high=2.0, low=0.5, close=1.5, n_1m=150,
                  complete=True)                                      # 12:00 + 4h > 14:30: partial
    with pytest.raises(ValidationError, match="complete"):
        _snap(bars={"1m": one, "4h": (partial,)})
    misaligned = Bar(ts=datetime(2025, 6, 11, 13, tzinfo=UTC), open=1.0, high=2.0, low=0.5, close=1.5,
                     complete=False)
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
