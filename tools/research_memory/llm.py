"""The answer step: one LLM call through backend/agents' client layer.

The request goes through `backend.agents.llm.call.run_call`: response cache,
kill switch, daily budget, retries with backoff and one ledger row per
attempt (llm_calls / llm_responses in its own SQLite file, git-ignored).
`llm.mode = "rerun"` answers from the cache only, so an eval can be repeated
at no cost. The client is AnthropicClient (key from the environment or the
.env file named in the config, never logged) or OllamaClient (below).

The system prompt is the owner's file, verbatim. The user message holds data
only: the question, then each retrieved chunk with its path, heading, heading
path, status, study and doc type, then its text. The answer is constrained by
ANSWER_SCHEMA (structured output) and parsed strictly here.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.agents.llm.budget import Budget
from backend.agents.llm.call import CallContext, CallDeps, run_call
from backend.agents.llm.client import LLMCallError, LLMClient, LLMResponse
from backend.agents.llm.ledger import LLMLedger
from backend.agents.llm.request import LLMRequest, TextBlock
from backend.agents.hashing import sha256_hex
from tools.research_memory.config import MemoryConfig, Prompt
from tools.research_memory.store import Hit

AGENT = "research_memory"
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}, "heading": {"type": "string"}},
                            "required": ["path", "heading"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["text", "citations"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims"],
    "additionalProperties": False,
}


class MemoryRequest(LLMRequest):
    """An LLMRequest whose effort may be "default": then no effort is sent."""

    def body(self) -> dict[str, Any]:
        body = super().body()
        if self.effort == "default":
            body["output_config"].pop("effort", None)
        return body


def render_user(question: str, hits: list[Hit]) -> str:
    """The user message: the question and the chunks, as data (no instructions)."""
    parts = [f"Question: {question}", "", f"Retrieved chunks ({len(hits)}):"]
    for i, h in enumerate(hits, 1):
        c = h.chunk
        part = f" (part {c.part} of {c.n_parts})" if c.n_parts > 1 else ""
        parts += ["", f"[{i}] path: {c.path}", f"heading: {c.heading}", f"heading path: {c.heading_path}{part}",
                  f"status: {c.status}", f"study: {c.study}", f"doc type: {c.doc_type}", "text:", c.text]
    return "\n".join(parts)


def build_request(cfg: MemoryConfig, prompt: Prompt, question: str, hits: list[Hit]) -> MemoryRequest:
    a = cfg.answer
    return MemoryRequest(agent=AGENT, round=1, model=a.model, effort=a.effort, max_tokens=a.max_tokens,
                         system=prompt.text, messages=((TextBlock(render_user(question, hits)),),),
                         output_schema=ANSWER_SCHEMA)


class AnswerInvalid(ValueError):
    pass


def parse_answer(text: str | None) -> list[dict[str, Any]]:
    """The claims, checked against ANSWER_SCHEMA by hand (no extra keys, all strings)."""
    if text is None:
        raise AnswerInvalid("no text in the response")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise AnswerInvalid(f"not JSON: {e}") from None
    if not isinstance(data, dict) or set(data) != {"claims"} or not isinstance(data["claims"], list):
        raise AnswerInvalid("expected exactly {\"claims\": [...]}")
    claims = []
    for i, c in enumerate(data["claims"]):
        if not isinstance(c, dict) or set(c) != {"text", "citations"}:
            raise AnswerInvalid(f"claim {i}: expected keys text, citations")
        if not isinstance(c["text"], str) or not isinstance(c["citations"], list):
            raise AnswerInvalid(f"claim {i}: text must be a string, citations a list")
        for j, ref in enumerate(c["citations"]):
            if (not isinstance(ref, dict) or set(ref) != {"path", "heading"}
                    or not all(isinstance(ref[k], str) for k in ("path", "heading"))):
                raise AnswerInvalid(f"claim {i} citation {j}: expected string path and heading")
        claims.append({"text": c["text"], "citations": [dict(r) for r in c["citations"]]})
    return claims


@dataclass(frozen=True)
class AnswerOutcome:
    claims: list[dict[str, Any]] | None     # None: abstain (see reason)
    reason: str | None
    call_id: str | None
    attempts: int
    model: str | None = None
    usage: dict[str, int | None] | None = None


def answer(cfg: MemoryConfig, prompt: Prompt, question: str, hits: list[Hit], deps: CallDeps,
           snapshot_hash: str) -> AnswerOutcome:
    req = build_request(cfg, prompt, question, hits)
    ctx = CallContext(snapshot_hash=snapshot_hash, prompt_file_sha256=prompt.sha256, config_sha256=cfg.sha256)
    out = run_call(req, cfg.llm, cfg.budget, ctx, deps)
    if out.response is None:
        return AnswerOutcome(None, out.reason, out.call_id, out.attempts)
    r = out.response
    if r.refusal_category or r.stop_reason == "refusal":
        return AnswerOutcome(None, f"refusal:{r.refusal_category}", out.call_id, out.attempts, r.model, r.usage)
    if r.stop_reason == "max_tokens":
        return AnswerOutcome(None, f"truncated: max_tokens {cfg.answer.max_tokens} reached", out.call_id,
                             out.attempts, r.model, r.usage)
    try:
        claims = parse_answer(r.text)
    except AnswerInvalid as e:
        return AnswerOutcome(None, f"invalid: {e}", out.call_id, out.attempts, r.model, r.usage)
    return AnswerOutcome(claims, None, out.call_id, out.attempts, r.model, r.usage)


def retrieval_hash(question: str, hits: list[Hit], index_meta: dict[str, object]) -> str:
    """The `snapshot_hash` of the ledger row: what the answer was asked over."""
    return sha256_hex({"question": question, "chunks": [h.chunk.chunk_id for h in hits],
                       "corpus": index_meta.get("corpus_sha256")})


# ---------------------------------------------------------------- Ollama

class OllamaClient:
    """The LLMClient protocol over Ollama's /api/chat (stdlib HTTP, no SDK).
    The Anthropic-shaped payload is translated: system -> a system message,
    the user text blocks -> one user message, the output schema -> `format`,
    max_tokens -> options.num_predict. Effort has no Ollama equivalent and is
    ignored. One attempt per call; run_call retries."""

    def __init__(self, url: str, opener: Any = None) -> None:
        self.url = url.rstrip("/")
        self._open = opener or urllib.request.urlopen

    @staticmethod
    def translate(payload: dict[str, Any]) -> dict[str, Any]:
        user = "\n\n".join(b["text"] for m in payload["messages"] for b in m["content"] if b.get("type") == "text")
        body: dict[str, Any] = {
            "model": payload["model"], "stream": False,
            "messages": [{"role": "system", "content": payload.get("system", "")}, {"role": "user", "content": user}],
            "options": {"num_predict": payload["max_tokens"]},
        }
        fmt = payload.get("output_config", {}).get("format", {})
        if fmt.get("type") == "json_schema":
            body["format"] = fmt["schema"]
        return body

    def complete(self, payload: dict[str, Any], timeout_s: float) -> LLMResponse:
        body = self.translate(payload)
        req = urllib.request.Request(f"{self.url}/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.monotonic()
        try:
            with self._open(req, timeout=timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise LLMCallError("api_error", f"HTTP {e.code}: {e.reason}", status=e.code,
                               retryable=e.code in (408, 429, 500, 502, 503, 504)) from e
        except TimeoutError as e:
            raise LLMCallError("timeout", f"no response after {timeout_s}s", retryable=True) from e
        except (urllib.error.URLError, OSError) as e:
            raise LLMCallError("connection", f"{type(e).__name__}: {e}", retryable=True) from e
        try:
            data = json.loads(raw)
            text = data["message"]["content"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise LLMCallError("api_error", f"unexpected Ollama response: {type(e).__name__}") from e
        done = data.get("done_reason")
        usage: dict[str, int | None] = {"input_tokens": data.get("prompt_eval_count"),
                                        "output_tokens": data.get("eval_count"),
                                        "cache_read_input_tokens": None, "cache_creation_input_tokens": None}
        return LLMResponse(text=text, model=str(data.get("model", payload["model"])), request_id=None,
                           stop_reason="max_tokens" if done == "length" else "end_turn", refusal_category=None,
                           usage=usage, raw_json=raw, latency_ms=(time.monotonic() - t0) * 1000.0)

    def count_tokens(self, payload: dict[str, Any]) -> int:
        raise LLMCallError("api_error", "count_tokens is not available on Ollama")


def make_client(cfg: MemoryConfig, repo: Path) -> LLMClient:
    if cfg.answer.provider == "ollama":
        assert cfg.ollama is not None
        return OllamaClient(cfg.ollama.url)
    from backend.agents.llm.anthropic_client import AnthropicClient   # noqa: PLC0415 - lazy SDK import
    from tools.research_memory.config import load_env_key             # noqa: PLC0415

    env_file = Path(cfg.paths.env_file)
    load_env_key(env_file if env_file.is_absolute() else repo / env_file)
    return AnthropicClient()


def make_deps(cfg: MemoryConfig, repo: Path, client: LLMClient | None = None,
              ledger: LLMLedger | None = None) -> CallDeps:
    if ledger is None:
        from backend.agents.llm.ledger import SqliteLedger              # noqa: PLC0415
        from backend.agents.store.sqlite import SqliteAgentStore        # noqa: PLC0415

        p = Path(cfg.paths.ledger)
        p = p if p.is_absolute() else repo / p
        p.parent.mkdir(parents=True, exist_ok=True)
        ledger = SqliteLedger(SqliteAgentStore(str(p)))
    return CallDeps(client=client or make_client(cfg, repo), ledger=ledger, budget=Budget(ledger))


__all__ = ["AGENT", "ANSWER_SCHEMA", "AnswerInvalid", "AnswerOutcome", "MemoryRequest", "OllamaClient", "answer",
           "build_request", "make_client", "make_deps", "parse_answer", "render_user", "retrieval_hash"]
