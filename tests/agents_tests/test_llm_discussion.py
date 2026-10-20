"""LLMVoteAgent.discuss with FakeLLMClient, discussion rendering, and store
migration 5 (the decisions rebuild). No network."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.config import SnapshotConfig
from agents.llm import Budget, CallDeps, FakeLLMClient, LLMVoteAgent, MemoryLedger, fake_response
from agents.owner_config import OwnerConfig
from agents.prompting.files import parse_prompt
from agents.prompting.render import CHANGE_RULE, RenderProblem, render_discussion, vote_json
from agents.schemas import DiscussionView, Evidence, Snapshot, Vote
from agents.snapshot import build_snapshot
from agents.store import SqliteAgentStore

HERE = Path(__file__).resolve().parent
UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)
LIVE = (HERE / "owner_config_ok.toml").read_text().replace('mode = "rerun"', 'mode = "live"')
VISUAL = parse_prompt(b"---\nagent: visual\nversion: 1\n---\n## system\nYou vote.\n## user\n{{bars_table:15m}}\n",
                      "visual")
DISC = parse_prompt(b"---\nagent: discussion\nversion: 1\n---\n## user\nRound {{round}}/{{max_rounds}} as {{agent}}."
                    b"\nYou: {{own_previous_vote_json}}\nOthers: {{other_votes_json}}\n{{change_rule}}\n", "discussion")


@pytest.fixture(scope="module")
def snap(replay_db):
    s = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db)
    assert isinstance(s, Snapshot)
    return s


def ref(snap, k=1):
    b = snap.bars["15m"][-k - 1]
    return f"bars:15m:{b.ts.isoformat()}..{b.ts.isoformat()}"


def view(snap, r=1):
    own = Vote(agent="visual", round=1, direction="long", confidence=0.6, reason="r1",
               evidence=(Evidence(ref=ref(snap), claim="c", valid=True),))
    other = Vote(agent="technical", round=1, direction="short", confidence=0.7, reason="t1", model_id="hidden")
    return DiscussionView(agent="visual", round=r, max_rounds=2, snapshot=snap, own_previous=own, others=(other,))


def agent(script, owner=None, discussion=DISC):
    client, ledger = FakeLLMClient(script), MemoryLedger()
    deps = CallDeps(client=client, ledger=ledger, budget=Budget(ledger, env={}), sleep=lambda s: None,
                    now=lambda: T)
    owner = owner or OwnerConfig.from_text(LIVE)
    return LLMVoteAgent("visual", VISUAL, owner, deps, discussion_prompt=discussion), client, ledger


def test_rendering_the_discussion(snap):
    r = render_discussion(DISC, view(snap))
    text = r.user_text()
    assert "Round 1/2 as visual." in text and CHANGE_RULE in text
    others = json.loads(text.split("Others: ")[1].split("\n")[0])
    assert others == [vote_json(view(snap).others[0])] and "hidden" not in text       # content only
    assert isinstance(render_discussion(VISUAL, view(snap)), RenderProblem)


def test_a_discussion_request_reuses_the_round1_prefix(snap):
    answer = {"direction": "short", "confidence": 0.7, "reason": "agree", "evidence": [
        {"ref": ref(snap, 2), "claim": "lower high"}, {"ref": "vote:technical:1", "claim": "as technical said"}]}
    a, client, _ = agent([fake_response(answer, model="claude-sonnet-5-5")])
    v = a.discuss(view(snap))
    assert (v.round, v.direction, v.abstain) == (2, "short", False)
    assert [(e.ref.split(":")[0], e.valid) for e in v.evidence] == [("bars", True), ("vote", True)]
    p = client.payloads[0]
    assert (p["model"], p["output_config"]["effort"], p["max_tokens"]) == ("claude-sonnet-5-5", "low", 8000)
    assert p["system"] == "You vote." and len(p["messages"]) == 2              # round-1 content, then discussion
    a2, client2, _ = agent([fake_response({"direction": "none", "confidence": 0.1, "reason": "x", "evidence": []})])
    a2.vote(snap)
    assert client2.payloads[0]["messages"][0] == p["messages"][0]              # the same bytes as round 1


def test_a_vote_ref_alone_cannot_carry_a_direction(snap):
    answer = {"direction": "short", "confidence": 0.7, "reason": "x",
              "evidence": [{"ref": "vote:technical:1", "claim": "said so"}]}
    a, _, _ = agent([fake_response(answer, model="claude-sonnet-5-5")])
    v = a.discuss(view(snap))
    assert v.abstain and v.abstain_reason.startswith("invalid: direction without evidence")


def test_a_vote_ref_to_a_vote_not_shown_is_invalid(snap):
    answer = {"direction": "none", "confidence": 0.2, "reason": "x",
              "evidence": [{"ref": "vote:sentiment:1", "claim": "?"}]}
    a, _, _ = agent([fake_response(answer, model="claude-sonnet-5-5")])
    v = a.discuss(view(snap))
    assert v.evidence[0].valid is False and "not shown" in v.evidence[0].invalid_reason


@pytest.mark.parametrize("owner_text, prompt, why", [
    (LIVE.replace('[agents.visual.discussion]\nmodel = "claude-sonnet-5-5"\neffort = "low"\nmax_tokens = 8000\n', ""),
     DISC, "config_invalid: agents.visual.discussion: missing"),
    (LIVE, None, "prompt_invalid: discussion.md: not loaded"),
])
def test_discussion_problems_abstain_without_a_call(snap, owner_text, prompt, why):
    a, client, _ = agent([], owner=OwnerConfig.from_text(owner_text), discussion=prompt)
    v = a.discuss(view(snap))
    assert v.abstain and v.abstain_reason == why and v.round == 2 and client.payloads == []


def test_a_served_model_other_than_the_discussion_block_abstains(snap):
    a, _, _ = agent([fake_response({"direction": "none", "confidence": 0.1, "reason": "x", "evidence": []})])
    v = a.discuss(view(snap))                                       # discussion block asks for sonnet
    assert v.abstain_reason == "model_mismatch: requested claude-sonnet-5-5, served claude-opus-5-5"


# ---- migration 5: the decisions rebuild keeps every row

def test_a_v4_store_is_migrated_to_v5_with_its_rows(tmp_path, monkeypatch):
    from agents.store import sqlite as sq
    p = str(tmp_path / "v4.sqlite")
    monkeypatch.setattr(sq, "MIGRATIONS", sq.MIGRATIONS[:4])
    old = sq.SqliteAgentStore(p)
    c = old.con
    c.execute("INSERT INTO snapshots (snapshot_hash, schema_version, symbol, as_of, kinds, newest_bar_ts, content_json,"
              " created_ts) VALUES ('h', 3, 'XAUUSD', 't', '[]', 't', '{}', 't')")
    c.execute("INSERT INTO runs (run_id, trigger_source, trigger_id, trigger_t, trigger_payload_json, as_of, status,"
              " config_json, config_hash, code_version, created_ts) VALUES ('r', 's', 'i', '2025-06-11T14:30:00+00:00',"
              " '{}', '2025-06-11T14:30:00+00:00', 'done',"
              " '{}', 'c', 'v', 't')")
    for arm, outcome, rounds in (("blind", None, None), ("debate", "hold", 2)):
        c.execute("INSERT INTO decisions (run_id, arm, snapshot_hash, direction, reason, decided_by, debate_outcome,"
                  " debate_max_rounds, created_ts, epoch_id) VALUES ('r', ?, 'h', 'none', 'x', 'd', ?, ?, 't', 'e')",
                  (arm, outcome, rounds))
    old.close()
    monkeypatch.undo()
    s = SqliteAgentStore(p)
    got = [tuple(r) for r in s.con.execute("SELECT arm, debate_outcome, epoch_id, meta_rule FROM decisions ORDER BY arm")]
    assert got == [("blind", None, "e", None), ("debate", "hold", "e", None)]          # every row kept
    s.con.execute("INSERT INTO decisions (run_id, arm, snapshot_hash, direction, reason, decided_by, created_ts)"
                  " VALUES ('r', 'discussion', 'h', 'none', 'x', 'quorum_v0', 't')")    # the new arm is allowed
    for sql in ("UPDATE decisions SET reason = 'y'", "DELETE FROM decisions"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):                 # triggers recreated
            s.con.execute(sql)
    with pytest.raises(sqlite3.IntegrityError):
        s.con.execute("INSERT INTO decisions (run_id, arm, snapshot_hash, direction, reason, decided_by, created_ts)"
                      " VALUES ('r', 'judge', 'h', 'none', 'x', 'd', 't')")
    assert [d.arm for d in s.load_run("r")["decisions"]] == ["blind", "discussion"]      # debate rows: history only
    assert {r[1] for r in s.con.execute("PRAGMA table_info(votes)")} >= {"changed", "change_rejected",
                                                                         "rejected_proposal_json"}
    s.close()
