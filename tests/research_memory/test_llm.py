"""llm.py: the request (effort, data-only user message), strict parsing, run_call outcomes, Ollama."""

import io
import json
import urllib.error

import pytest

from backend.agents.llm.budget import Budget
from backend.agents.llm.call import CallDeps
from backend.agents.llm.client import LLMCallError
from backend.agents.llm.fake import FakeLLMClient, fake_response
from backend.agents.llm.ledger import MemoryLedger
from tests.research_memory.helpers import CONFIG, write_config
from tools.research_memory.chunk import Chunk
from tools.research_memory.config import load_config, load_prompt
from tools.research_memory.llm import (ANSWER_SCHEMA, AnswerInvalid, OllamaClient, answer, build_request,
                                       parse_answer, render_user)
from tools.research_memory.store import Hit


def chunk(path="research/a/REPORT.md", heading_path="a: report > 1. Verdict", status="result"):
    return Chunk(chunk_id=f"{path}#x-1", path=path, study="a", doc_type="REPORT", status=status,
                 heading_path=heading_path, heading=heading_path.split(" > ")[-1], part=1, n_parts=1,
                 text="PASS: QLIKE -12 %.", last_commit="c" * 40)


HITS = [Hit(chunk(), 0.9)]
CLAIMS = {"claims": [{"text": "PASS.", "citations": [{"path": "research/a/REPORT.md", "heading": "1. Verdict"}]}]}


@pytest.fixture
def setup(tmp_path):
    cfg = load_config(write_config(tmp_path))
    return cfg, load_prompt(cfg, repo=tmp_path)


def deps(script, ledger=None):
    ledger = ledger or MemoryLedger()
    return CallDeps(client=FakeLLMClient(script), ledger=ledger, budget=Budget(ledger, env={}), sleep=lambda s: None)


def test_default_effort_is_not_sent_and_the_prompt_is_verbatim(setup):
    cfg, prompt = setup
    body = build_request(cfg, prompt, "q?", HITS).body()
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": ANSWER_SCHEMA}}
    assert body["system"] == "You answer from the chunks." and body["model"] == "claude-sonnet-5-5"
    assert not {"temperature", "top_p", "top_k"} & set(body)


def test_explicit_effort_is_sent(tmp_path):
    cfg = load_config(write_config(tmp_path, CONFIG.replace('effort = "default"', 'effort = "high"')))
    body = build_request(cfg, load_prompt(cfg, repo=tmp_path), "q?", HITS).body()
    assert body["output_config"]["effort"] == "high"


def test_user_message_is_data_only():
    text = render_user("What was the verdict?", HITS)
    assert text.splitlines() == ["Question: What was the verdict?", "", "Retrieved chunks (1):", "",
                                 "[1] path: research/a/REPORT.md", "heading: 1. Verdict",
                                 "heading path: a: report > 1. Verdict", "status: result", "study: a",
                                 "doc type: REPORT", "text:", "PASS: QLIKE -12 %."]


@pytest.mark.parametrize("text,match", [
    (None, "no text"), ("not json", "not JSON"), ('{"claims": [], "x": 1}', "exactly"),
    ('{"claims": [{"text": "a"}]}', "keys"), ('{"claims": [{"text": 1, "citations": []}]}', "string"),
    ('{"claims": [{"text": "a", "citations": [{"path": "p"}]}]}', "citation 0"),
])
def test_parse_rejects(text, match):
    with pytest.raises(AnswerInvalid, match=match):
        parse_answer(text)


def test_answer_ok_and_cached(setup):
    cfg, prompt = setup
    ledger = MemoryLedger()
    out = answer(cfg, prompt, "q?", HITS, deps([fake_response(CLAIMS)], ledger), "snap")
    assert out.claims == CLAIMS["claims"] and out.reason is None and out.attempts == 1
    again = answer(cfg, prompt, "q?", HITS, deps([], ledger), "snap")      # empty script: a call would fail
    assert again.claims == CLAIMS["claims"] and again.attempts == 0
    rec = ledger.calls[0]
    assert rec.agent == "research_memory" and rec.effort == "default" and rec.prompt_file_sha256 == prompt.sha256
    assert rec.cost_usd is not None and rec.cost_usd > 0


@pytest.mark.parametrize("resp,reason", [
    (fake_response(CLAIMS, stop_reason="refusal", refusal_category="cyber"), "refusal:cyber"),
    (fake_response(CLAIMS, stop_reason="max_tokens"), "truncated"),
    (fake_response("{oops"), "invalid: not JSON"),
    (LLMCallError("api_error", "bad request", status=400), "api_error 400"),
])
def test_answer_abstains(setup, resp, reason):
    cfg, prompt = setup
    out = answer(cfg, prompt, "q?", HITS, deps([resp]), "snap")
    assert out.claims is None and reason in out.reason


def test_rerun_mode_never_calls(tmp_path):
    cfg = load_config(write_config(tmp_path, CONFIG.replace('mode = "live"', 'mode = "rerun"')))
    out = answer(cfg, load_prompt(cfg, repo=tmp_path), "q?", HITS, deps([]), "snap")
    assert out.claims is None and out.reason == "cache_miss"


def test_kill_switch(setup, tmp_path):
    cfg, prompt = setup
    (tmp_path / "KILL").write_text("")
    out = answer(cfg, prompt, "q?", HITS, deps([]), "snap")
    assert out.claims is None and out.reason.startswith("kill_switch")


# --- Ollama -------------------------------------------------------------------------------------------

class FakeHTTP:
    def __init__(self, payload=None, error=None):
        self.payload, self.error, self.requests = payload, error, []

    def __call__(self, req, timeout):
        self.requests.append((req, timeout))
        if self.error:
            raise self.error
        return io.BytesIO(json.dumps(self.payload).encode())


def test_ollama_translates_and_parses(setup):
    cfg, prompt = setup
    payload = build_request(cfg, prompt, "q?", HITS).payload()
    http = FakeHTTP({"model": "llama3.1:8b", "message": {"content": json.dumps(CLAIMS)}, "done_reason": "stop",
                     "prompt_eval_count": 321, "eval_count": 45})
    resp = OllamaClient("http://127.0.0.1:11434/", opener=http).complete(payload, 12.0)
    req, timeout = http.requests[0]
    body = json.loads(req.data)
    assert req.full_url == "http://127.0.0.1:11434/api/chat" and timeout == 12.0
    assert body["messages"][0] == {"role": "system", "content": "You answer from the chunks."}
    assert body["messages"][1]["content"].startswith("Question: q?") and body["format"] == ANSWER_SCHEMA
    assert body["options"] == {"num_predict": 2000} and body["stream"] is False
    assert parse_answer(resp.text) == CLAIMS["claims"] and resp.usage["input_tokens"] == 321
    assert resp.stop_reason == "end_turn"


def test_ollama_length_and_errors(setup):
    cfg, prompt = setup
    payload = build_request(cfg, prompt, "q?", HITS).payload()
    resp = OllamaClient("http://x", opener=FakeHTTP({"message": {"content": "{}"}, "done_reason": "length"})
                        ).complete(payload, 1)
    assert resp.stop_reason == "max_tokens"
    err = urllib.error.HTTPError("http://x", 503, "busy", {}, None)
    with pytest.raises(LLMCallError) as e:
        OllamaClient("http://x", opener=FakeHTTP(error=err)).complete(payload, 1)
    assert e.value.status == 503 and e.value.retryable
    with pytest.raises(LLMCallError) as e:
        OllamaClient("http://x", opener=FakeHTTP(error=urllib.error.URLError("refused"))).complete(payload, 1)
    assert e.value.kind == "connection" and e.value.retryable
    with pytest.raises(LLMCallError, match="unexpected"):
        OllamaClient("http://x", opener=FakeHTTP({"nope": 1})).complete(payload, 1)
