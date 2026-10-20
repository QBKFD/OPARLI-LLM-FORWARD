"""FakeLLMClient: a scripted client for tests. It never touches the network.

Each call to `complete` takes the next item of the script:
- an LLMResponse is returned;
- an exception is raised;
- a callable is called with the payload and its result used the same way.
An empty script raises AssertionError, so a test notices an unexpected call.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable, Iterable

from agents.llm.client import LLMCallError, LLMResponse

Item = LLMResponse | BaseException | Callable[[dict[str, Any]], Any]


def fake_response(answer: dict | str | None, *, model: str = "claude-opus-5-5", stop_reason: str = "end_turn",
                  refusal_category: str | None = None, input_tokens: int = 1000, output_tokens: int = 200,
                  request_id: str = "req_fake") -> LLMResponse:
    """A response whose text is `answer` (a dict is dumped as JSON)."""
    text = json.dumps(answer) if isinstance(answer, dict) else answer
    usage: dict[str, int | None] = {"input_tokens": input_tokens, "output_tokens": output_tokens,
                                    "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    raw = json.dumps({"model": model, "stop_reason": stop_reason, "text": text, "usage": usage})
    return LLMResponse(text=text, model=model, request_id=request_id, stop_reason=stop_reason,
                       refusal_category=refusal_category, usage=usage, raw_json=raw, latency_ms=12.0)


class FakeLLMClient:
    def __init__(self, script: Iterable[Item] = (), tokens: int = 1234) -> None:
        self.script = list(script)
        self.tokens = tokens
        self.payloads: list[dict[str, Any]] = []
        self.timeouts: list[float] = []
        self._lock = threading.Lock()

    def complete(self, payload: dict[str, Any], timeout_s: float) -> LLMResponse:
        with self._lock:
            self.payloads.append(payload)
            self.timeouts.append(timeout_s)
            if not self.script:
                raise AssertionError("FakeLLMClient: unexpected call (script exhausted)")
            item = self.script.pop(0)
        if callable(item) and not isinstance(item, (LLMResponse, BaseException)):
            item = item(payload)
        if isinstance(item, BaseException):
            raise item
        if not isinstance(item, LLMResponse):
            raise LLMCallError("api_error", f"fake script item is a {type(item).__name__}")
        return item

    def count_tokens(self, payload: dict[str, Any]) -> int:
        with self._lock:
            self.payloads.append(payload)
        return self.tokens


__all__ = ["FakeLLMClient", "fake_response"]
