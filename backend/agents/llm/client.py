"""The LLM client interface: one attempt per call, no retries inside.

The caller (agents/llm/call.py) owns retries, logging, the cache and the
budget, so every attempt is a row in llm_calls. AnthropicClient
(anthropic_client.py) is the real implementation; FakeLLMClient (fake.py)
is for tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

ErrorKind = Literal["timeout", "api_error", "connection"]
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


@dataclass(frozen=True)
class LLMResponse:
    """What one successful attempt returned."""
    text: str | None                       # the first text block (structured output: the JSON)
    model: str                             # the model that served it
    request_id: str | None
    stop_reason: str | None
    refusal_category: str | None
    usage: dict[str, int | None]           # input_tokens, output_tokens, cache_read_input_tokens, ...
    raw_json: str                          # the whole response, for the cache
    latency_ms: float = 0.0


class LLMCallError(Exception):
    """One failed attempt. `retryable` says whether another attempt may help."""

    def __init__(self, kind: ErrorKind, message: str, *, status: int | None = None,
                 request_id: str | None = None, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind, self.status, self.request_id, self.retryable = kind, status, request_id, retryable

    @property
    def reason(self) -> str:
        code = f" {self.status}" if self.status is not None else ""
        return f"{self.kind}{code}: {self}"


class LLMClient(Protocol):
    def complete(self, payload: dict[str, Any], timeout_s: float) -> LLMResponse:
        """One attempt. Raises LLMCallError on any failure."""

    def count_tokens(self, payload: dict[str, Any]) -> int:
        """Input tokens the payload would cost (no generation)."""


__all__ = ["ErrorKind", "LLMCallError", "LLMClient", "LLMResponse", "RETRYABLE_STATUS"]
