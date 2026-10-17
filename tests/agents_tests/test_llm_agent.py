"""agents.llm.call and LLMVoteAgent with FakeLLMClient: live and rerun modes,
retries, budget, kill switch, every abstain path, and the graph end to end.
No test calls the API."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agents.config import AgentsConfig, SnapshotConfig
from agents.graph import build_graph, run_once
from agents.llm import (Budget, CallDeps, FakeLLMClient, LLMCallError, LLMVoteAgent, MemoryLedger, SqliteLedger,
                        fake_response, make_llm_voters)
from agents.llm.__main__ import main as llm_main
from agents.llm.call import backoff_s
from agents.owner_config import OwnerConfig
from agents.prompting.files import load_all, parse_prompt
from agents.schemas import Snapshot, TriggerEvent
from agents.snapshot import build_snapshot
from agents.store import SqliteAgentStore

HERE = Path(__file__).resolve().parent
UTC = timezone.utc
T = datetime(2025, 6, 11, 14, 30, tzinfo=UTC)
LIVE = (HERE / "owner_config_ok.toml").read_text().replace('mode = "rerun"', 'mode = "live"')
VISUAL = "---\nagent: visual\nversion: 1\n---\n## system\nYou vote.\n## user\n{{alert_json}}\n{{bars_table:15m}}\n"
ALERT = {"alert_id": "A1", "alert_ts_utc": "2025-06-11 14:17:00", "session": "NY", "level_types": "PDH",
         "level_price": 3310.0, "price_at_alert": 3310.2, "confluence_count": 1}
NONE = {"direction": "none", "confidence": 0.3, "reason": "no edge", "evidence": []}


@pytest.fixture(scope="module")
def snap(replay_db):
    s = build_snapshot(T, SnapshotConfig(mode="replay"), replay_db, payload={"alert": ALERT})
    assert isinstance(s, Snapshot)
    return s


@pytest.fixture
def owner(tmp_path):
    return OwnerConfig.from_text(LIVE.replace("/tmp/agents.kill", str(tmp_path / "KILL")))


def agent(owner, script, *, ledger=None, prompt=VISUAL, env=None, sleeps=None):
    client = FakeLLMClient(script)
    ledger = ledger if ledger is not None else MemoryLedger()
    deps = CallDeps(client=client, ledger=ledger, budget=Budget(ledger, now=lambda: T, env=env or {}),
                    sleep=(sleeps.append if sleeps is not None else lambda s: None), now=lambda: T)
    return LLMVoteAgent("visual", parse_prompt(prompt.encode(), "visual"), owner, deps, epoch_id="e1"), client, ledger


def long_answer(snap):
    bar = snap.bars["15m"][-2]
    return {"direction": "long", "confidence": 0.7, "reason": "reclaim",
            "evidence": [{"ref": f"bars:15m:{bar.ts.isoformat()}..{bar.ts.isoformat()}", "claim": "held"}]}


# ---- a good vote

def test_a_live_vote_with_evidence(owner, snap):
    a, client, ledger = agent(owner, [fake_response(long_answer(snap), input_tokens=900, output_tokens=150)])
    v = a.vote(snap)
    assert (v.abstain, v.direction, v.confidence, v.reason) == (False, "long", 0.7, "reclaim")
    assert v.evidence[0].valid and v.model_id == "claude-opus-5-5" and (v.tokens_in, v.tokens_out) == (900, 150)
    payload = client.payloads[0]
    assert payload["model"] == "claude-opus-5-5" and payload["max_tokens"] == 16000
    assert payload["output_config"]["effort"] == "medium"
    assert "temperature" not in payload and "top_p" not in payload and "thinking" not in payload
    assert '"alert_id":"A1"' in payload["messages"][0]["content"][0]["text"]
    assert client.timeouts == [30.0]
    (call,) = ledger.calls
    assert (call.call_id, call.attempt, call.cached, call.epoch_id) == (v.call_id, 1, False, "e1")
    assert call.cost_usd == pytest.approx((900 * 4 + 150 * 20) / 1e6)
    assert call.prompt_file_sha256 == a.prompt.sha256 and call.config_sha256 == owner.sha256


def test_the_response_is_cached_before_use_and_a_rerun_is_identical(owner, snap, tmp_path):
    a, _, ledger = agent(owner, [fake_response(long_answer(snap))])
    first = a.vote(snap)
    rerun_owner = OwnerConfig.from_text(owner.canonical() and LIVE.replace('mode = "live"', 'mode = "rerun"')
                                        .replace("/tmp/agents.kill", str(tmp_path / "KILL")))
    b, client, _ = agent(rerun_owner, [], ledger=ledger)
    again = b.vote(snap)
    assert client.payloads == []                                   # cache only: no call at all
    assert again.model_dump(exclude={"call_id"}) == first.model_dump(exclude={"call_id"})
    assert ledger.calls[-1].cached and ledger.calls[-1].attempt == 0


def test_live_mode_reuses_a_cached_response(owner, snap):
    a, client, ledger = agent(owner, [fake_response(NONE)])
    a.vote(snap)
    b, client2, _ = agent(owner, [], ledger=ledger)
    assert b.vote(snap).direction == "none" and client2.payloads == []


def test_a_rerun_cache_miss_never_calls(owner, snap, tmp_path):
    rerun = OwnerConfig.from_text(LIVE.replace('mode = "live"', 'mode = "rerun"'))
    a, client, ledger = agent(rerun, [])
    v = a.vote(snap)
    assert v.abstain and v.abstain_reason == "cache_miss" and client.payloads == []
    assert ledger.calls[0].error == "cache_miss" and ledger.attempts_on("2025-06-11") == 0


# ---- abstain paths

@pytest.mark.parametrize("resp, why", [
    (fake_response(NONE, model="claude-sonnet-5-5"), "model_mismatch: requested claude-opus-5-5, served claude-sonnet-5-5"),
    (fake_response(None, stop_reason="refusal", refusal_category="cyber"), "refusal:cyber"),
    (fake_response(None, stop_reason="refusal"), "refusal:uncategorised"),
    (fake_response('{"direction": "lo', stop_reason="max_tokens"), "truncated: max_tokens 16000 reached"),
    (fake_response(NONE, stop_reason="tool_use"), "invalid: stop_reason 'tool_use'"),
    (fake_response("I think long"), "invalid: not JSON"),
    (fake_response({**NONE, "direction": "long"}), "invalid: direction without evidence"),
])
def test_bad_responses_abstain(owner, snap, resp, why):
    a, _, _ = agent(owner, [resp])
    v = a.vote(snap)
    assert v.abstain and v.abstain_reason.startswith(why), v.abstain_reason
    assert v.call_id is not None and v.prompt_hash is not None          # the call is still on record


def test_prompt_and_config_problems_abstain_without_a_call(owner, snap, tmp_path):
    from agents.prompting.files import PromptProblem
    client = FakeLLMClient([])
    deps = CallDeps(client=client, ledger=MemoryLedger(), budget=Budget(MemoryLedger(), env={}))
    cases = [
        (PromptProblem("visual.md", "not found"), owner, "prompt_invalid: visual.md: not found"),
        (parse_prompt(VISUAL.encode(), "visual"), OwnerConfig.from_text(""), "config_invalid: agents.visual: missing"),
        (parse_prompt(VISUAL.encode(), "visual"), OwnerConfig.from_text(LIVE.replace("[llm]", "[llm_old]")),
         "config_invalid: llm: missing"),
        (parse_prompt(VISUAL.encode(), "visual"), OwnerConfig.from_text(LIVE.replace('"15m" = 8\n', '')),
         "config_invalid: render.bars_rows.15m: missing"),
    ]
    for prompt, cfg, why in cases:
        v = LLMVoteAgent("visual", prompt, cfg, deps).vote(snap)
        assert v.abstain and v.abstain_reason == why
    assert client.payloads == []


def test_kill_switch_stops_every_call(owner, snap, tmp_path):
    a, client, _ = agent(owner, [fake_response(NONE)], env={"AGENTS_LLM_KILL": "1"})
    assert a.vote(snap).abstain_reason == "kill_switch: $AGENTS_LLM_KILL=1" and client.payloads == []
    (tmp_path / "KILL").write_text("")
    b, client2, _ = agent(owner, [fake_response(NONE)])
    assert b.vote(snap).abstain_reason == "kill_switch: KILL exists" and client2.payloads == []


def test_the_daily_budget(owner, snap):
    capped = OwnerConfig.from_text(LIVE.replace("max_calls_per_day = 40", "max_calls_per_day = 1"))
    ledger = MemoryLedger()
    a, _, _ = agent(capped, [fake_response(NONE)], ledger=ledger)
    assert not a.vote(snap).abstain
    other = VISUAL.replace("You vote.", "You vote again.")             # a different prompt: no cache hit
    b, client, _ = agent(capped, [fake_response(NONE)], ledger=ledger, prompt=other)
    assert b.vote(snap).abstain_reason == "budget: 1 attempts on 2025-06-11, max 1" and client.payloads == []


def test_retryable_failures_are_retried_with_backoff(owner, snap):
    sleeps = []
    script = [LLMCallError("timeout", "no response after 30.0s", retryable=True),
              LLMCallError("api_error", "RateLimitError: slow down", status=429, request_id="r429", retryable=True),
              fake_response(NONE)]
    a, client, ledger = agent(owner, script, sleeps=sleeps)
    v = a.vote(snap)
    assert not v.abstain and len(client.payloads) == 3 and sleeps == [backoff_s(1), backoff_s(2)] == [0.5, 1.0]
    assert [(c.attempt, c.error is None) for c in ledger.calls] == [(1, False), (2, False), (3, True)]
    assert ledger.calls[1].request_id == "r429" and ledger.calls[1].error.startswith("api_error 429")


def test_retries_are_bounded(owner, snap):
    err = LLMCallError("api_error", "OverloadedError: busy", status=529, retryable=True)
    a, client, ledger = agent(owner, [err, err, err, err])            # max_retries = 2: three attempts
    v = a.vote(snap)
    assert v.abstain and v.abstain_reason == "api_error 529: OverloadedError: busy"
    assert len(client.payloads) == 3 and len(ledger.calls) == 3


def test_non_retryable_and_unexpected_failures_stop_at_once(owner, snap):
    a, client, _ = agent(owner, [LLMCallError("api_error", "BadRequestError: no", status=400)])
    assert a.vote(snap).abstain_reason == "api_error 400: BadRequestError: no" and len(client.payloads) == 1
    b, client2, ledger = agent(owner, [ValueError("bug in the client")])
    assert b.vote(snap).abstain_reason == "api_error: ValueError: bug in the client" and len(client2.payloads) == 1
    assert ledger.calls[0].error == "api_error: ValueError: bug in the client"


def test_backoff_is_capped():
    assert [backoff_s(i) for i in range(1, 8)] == [0.5, 1, 2, 4, 8, 8, 8]


# ---- the graph, end to end

def test_the_graph_runs_llm_voters_and_stores_their_calls(tmp_path, replay_db, owner, snap):
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    for name in ("visual", "technical"):
        (pdir / f"{name}.md").write_text(VISUAL.replace("agent: visual", f"agent: {name}"))
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    # the voters run in parallel: each scripted reply serves the model it was asked for
    client = FakeLLMClient([lambda p: fake_response(long_answer(snap), model=p["model"])] * 2)
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
    graph = build_graph(cfg, store, replay_db, owner_config=owner, prompts_dir=pdir, llm_client=client)
    out = run_once(graph, TriggerEvent(source="test", t=T, trigger_id="1", payload={"alert": ALERT}), run_id="r1")
    votes = out["votes"]
    assert not votes["visual"].abstain and not votes["technical"].abstain
    assert votes["sentiment"].abstain_reason == "prompt_invalid: sentiment.md: not found"   # checked before config
    assert out["final_decisions"]["blind"].direction == "long"     # two agreeing long votes (quorum v0)
    rec = store.load_run("r1")
    assert {v.agent: v.evidence for v in rec["votes"]} == {k: v.evidence for k, v in votes.items()}
    calls = store.con.execute("SELECT agent, model_requested, model_returned, cached FROM llm_calls"
                              " ORDER BY agent").fetchall()
    assert [tuple(r) for r in calls] == [("technical", "claude-sonnet-5-5", "claude-sonnet-5-5", 0),
                                         ("visual", "claude-opus-5-5", "claude-opus-5-5", 0)]
    assert {r[0] for r in store.con.execute("SELECT call_id FROM votes WHERE call_id IS NOT NULL")} == \
        {r[0] for r in store.con.execute("SELECT call_id FROM llm_calls")}
    store.close()


def test_without_a_client_the_stubs_stay(replay_db, owner):
    from agents.store.memory import MemoryStore
    cfg = AgentsConfig.model_validate({"snapshot": {"mode": "replay"}})
    out = run_once(build_graph(cfg, MemoryStore(), replay_db, owner_config=owner),
                   TriggerEvent(source="test", t=T, trigger_id="1"))
    assert all(v.abstain_reason.startswith("stub:") for v in out["votes"].values())


def test_make_llm_voters_builds_every_llm_agent(owner):
    deps = CallDeps(client=FakeLLMClient([]), ledger=MemoryLedger(), budget=Budget(MemoryLedger(), env={}))
    assert sorted(make_llm_voters(owner, load_all(), deps)) == ["sentiment", "technical", "visual"]


def test_sqlite_ledger_with_the_agent(tmp_path, owner, snap):
    store = SqliteAgentStore(str(tmp_path / "a.sqlite"))
    a, _, ledger = agent(owner, [fake_response(NONE)], ledger=SqliteLedger(store))
    v = a.vote(snap)
    row = store.con.execute("SELECT agent, attempt, cost_usd FROM llm_calls WHERE call_id = ?", (v.call_id,)).fetchone()
    assert tuple(row)[:2] == ("visual", 1) and row[2] > 0
    stored = store.con.execute("SELECT text FROM llm_responses").fetchone()[0]
    assert json.loads(stored) == NONE
    store.close()


# ---- count-tokens

def test_count_tokens_command(tmp_path, replay_db, capsys):
    pdir = tmp_path / "prompts"
    pdir.mkdir()
    (pdir / "visual.md").write_text(VISUAL)
    cfg = tmp_path / "agents.toml"
    cfg.write_text(LIVE)
    fake = FakeLLMClient(tokens=4321)
    assert llm_main(["count-tokens", "--db", replay_db, "--t", "2025-06-11T14:30:00Z", "--config", str(cfg),
                     "--prompts", str(pdir)], client=fake) == 0
    out = capsys.readouterr().out
    assert "visual: claude-opus-5-5 4321 input tokens, input ≈ $0.0173 at list price" in out
    assert "technical: prompt_invalid: technical.md: not found" in out
    assert "sentiment: prompt_invalid: sentiment.md: not found" in out
    assert len(fake.payloads) == 1
