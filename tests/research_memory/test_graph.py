"""graph.py: retrieve -> answer -> check end to end on the fixture index, with FakeLLMClient; Studio entry."""

import json
from pathlib import Path

import pytest

from backend.agents.llm.budget import Budget
from backend.agents.llm.call import CallDeps
from backend.agents.llm.fake import FakeLLMClient, fake_response
from backend.agents.llm.ledger import MemoryLedger
from tests.research_memory.helpers import make_repo, write_config
from tools.research_memory.build import build
from tools.research_memory.check import DRAFT_HEADING, NOT_IN_DOCUMENTS
from tools.research_memory.config import Problem, load_config, load_prompt
from tools.research_memory.embed import HashingEmbedder, ModelError
from tools.research_memory.graph import Retriever, build_graph

ROOT = Path(__file__).resolve().parents[2]
VERDICT = {"path": "research/alpha/REPORT.md", "heading": "1. Verdict"}


@pytest.fixture
def env(tmp_path):
    repo, rules = make_repo(tmp_path)
    index = tmp_path / "idx.sqlite"
    build(HashingEmbedder(), repo=repo, out=index, rules_path=rules)
    cfg = load_config(write_config(tmp_path))
    prompt = load_prompt(cfg, repo=tmp_path)
    return index, cfg, prompt


def run(env, script, question="What was the walrus index verdict?", k=5, setup=None):
    index, cfg, prompt = env
    ledger = MemoryLedger()
    client = FakeLLMClient(script)
    deps = CallDeps(client=client, ledger=ledger, budget=Budget(ledger, env={}), sleep=lambda s: None)
    graph = build_graph(Retriever(index, HashingEmbedder), setup or (lambda: (cfg, prompt, deps)))
    return graph.invoke({"question": question, "k": k}), client


def answer_of(*claims):
    return fake_response({"claims": list(claims)})


def test_answered(env):
    out, client = run(env, [answer_of({"text": "PASS: QLIKE falls 12 % at H = 240.", "citations": [VERDICT]})])
    assert out["verdict"] == "answered"
    assert out["answer"] == ("- PASS: QLIKE falls 12 % at H = 240. "
                             "[research/alpha/REPORT.md § alpha: report > 1. Verdict — RESULT]")
    assert len(out["hits"]) == 5 and out["call"]["attempts"] == 1
    sent = client.payloads[0]["messages"][0]["content"][0]["text"]
    assert sent.startswith("Question: What was the walrus index verdict?") and "status: result" in sent


def test_uncited_claim_gives_not_in_the_documents(env):
    out, _ = run(env, [answer_of({"text": "PASS.", "citations": [VERDICT]}, {"text": "Gold too.", "citations": []})])
    assert out["verdict"] == "not_in_documents" and out["answer"] == NOT_IN_DOCUMENTS
    assert out["reason"] == "claim 2 has no citation"


def test_citation_outside_the_retrieved_chunks(env):
    out, _ = run(env, [answer_of({"text": "x", "citations": [VERDICT]})], k=1,
                 question="Who decided the 20-day window for the walrus index?")
    if any(h["path"] == VERDICT["path"] and h["heading"] == "1. Verdict" for h in out["hits"]):
        pytest.skip("the fixture ranking retrieved the verdict chunk")
    assert out["verdict"] == "not_in_documents" and "not a retrieved chunk" in out["reason"]


def test_no_claims_gives_not_in_the_documents(env):
    out, _ = run(env, [answer_of()], question="What is the capital of France?")
    assert out["verdict"] == "not_in_documents" and out["answer"] == NOT_IN_DOCUMENTS


def test_draft_is_never_shown_as_a_result(env):
    cite = {"path": "research/alpha/PROPOSAL.md", "heading": "Proposal: a penguin index (not run)"}
    out, _ = run(env, [answer_of({"text": "The penguin index forecasts tin volatility.", "citations": [cite]})],
                 question="Does the penguin index forecast tin volatility?")
    assert out["verdict"] == "answered"
    assert out["answer"].splitlines()[0] == DRAFT_HEADING and out["answer"].endswith("— DRAFT]")


def test_config_problem_abstains_without_a_call(env):
    out, client = run(env, [], setup=lambda: Problem("prompt", "missing"))
    assert out["verdict"] == "abstain" and out["answer"] == "abstain: config_invalid: prompt: missing"
    assert client.payloads == []


def test_llm_error_abstains_not_not_in_documents(env):
    out, _ = run(env, [fake_response("{broken")])
    assert out["verdict"] == "abstain" and out["answer"].startswith("abstain: invalid")


def test_missing_index_empty_question_and_missing_model(tmp_path, env):
    _, cfg, prompt = env
    never = lambda: pytest.fail("setup must not run")                          # noqa: E731
    g = build_graph(Retriever(tmp_path / "none.sqlite", HashingEmbedder), never)
    out = g.invoke({"question": "x"})
    assert out["verdict"] == "abstain" and "index:" in out["reason"]
    assert build_graph(Retriever(env[0], HashingEmbedder), never).invoke({"question": " "})["reason"] == "no question"

    def no_model():
        raise ModelError("not fetched")

    def no_torch():
        raise ImportError("no torch", name="sentence_transformers")

    for factory, text in ((no_model, "embedder: not fetched"), (no_torch, "sentence_transformers is not installed")):
        out = build_graph(Retriever(env[0], factory), never).invoke({"question": "x"})
        assert out["verdict"] == "abstain" and text in out["reason"]


def test_studio_entry_is_registered_and_builds_without_loading_the_model(monkeypatch, tmp_path):
    cfg = json.loads((ROOT / "tools/langgraph_studio/langgraph.json").read_text())
    path, attr = cfg["graphs"]["research_memory"].split(":")
    assert (ROOT / path).resolve() == ROOT / "tools/research_memory/studio.py" and attr == "make_graph"
    monkeypatch.setenv("RESEARCH_MEMORY_INDEX", str(tmp_path / "none.sqlite"))
    monkeypatch.setenv("RESEARCH_MEMORY_CONFIG", str(tmp_path / "none.toml"))
    from tools.research_memory import studio

    out = studio.make_graph().invoke({"question": "anything"})
    assert out["verdict"] == "abstain" and "index:" in out["reason"]
    src = (ROOT / path).read_text()
    assert "anthropic" not in src and "sentence_transformers" not in src
