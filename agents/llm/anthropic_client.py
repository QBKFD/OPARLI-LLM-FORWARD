"""The real client: the official `anthropic` SDK (the `llm` extra).

- **Lazy import.** The SDK is imported when the first client is built, so
  nothing else in agents/ loads it, and the tests never do.
- **One attempt per call.** The SDK's own retries are off (`max_retries=0`);
  agents/llm/call.py retries, so each attempt is logged.
- **Credentials** come from the environment (ANTHROPIC_API_KEY, or an
  `ant auth login` profile). Nothing here reads or logs a key.
- **The request carries no sampling parameters** (temperature, top_p, top_k)
  and no thinking setting: each model's default applies, and effort is the
  only depth control. **No refusal fallbacks**: a fallback would answer with
  another model, which would break the epoch (AA11).
"""

from __future__ import annotations

import time
from typing import Any, Callable

from agents.llm.client import RETRYABLE_STATUS, LLMCallError, LLMResponse


class AnthropicClient:
    def __init__(self, sdk_client: Any = None, clock: Callable[[], float] = time.monotonic) -> None:
        """sdk_client: an `anthropic.Anthropic` instance (built lazily if None)."""
        self._sdk = sdk_client
        self._clock = clock

    def _client(self) -> Any:
        if self._sdk is None:
            import anthropic   # noqa: PLC0415 - lazy: only a live client loads the SDK

            self._sdk = anthropic.Anthropic(max_retries=0)
        return self._sdk

    def complete(self, payload: dict[str, Any], timeout_s: float) -> LLMResponse:
        import anthropic   # noqa: PLC0415

        t0 = self._clock()
        try:
            msg = self._client().messages.create(**payload, timeout=timeout_s)
        except anthropic.APITimeoutError as e:
            raise LLMCallError("timeout", f"no response after {timeout_s}s", retryable=True) from e
        except anthropic.APIConnectionError as e:
            raise LLMCallError("connection", f"{type(e).__name__}: {e}", retryable=True) from e
        except anthropic.APIStatusError as e:
            raise LLMCallError("api_error", f"{type(e).__name__}: {getattr(e, 'message', e)}",
                               status=e.status_code, request_id=e.request_id,
                               retryable=e.status_code in RETRYABLE_STATUS) from e
        except anthropic.AnthropicError as e:            # credentials, validation of the response, ...
            raise LLMCallError("api_error", f"{type(e).__name__}: {e}") from e
        return response_from_message(msg, (self._clock() - t0) * 1000.0)

    def count_tokens(self, payload: dict[str, Any]) -> int:
        import anthropic   # noqa: PLC0415

        body = {k: v for k, v in payload.items() if k in ("model", "system", "messages", "output_config")}
        try:
            return int(self._client().messages.count_tokens(**body).input_tokens)
        except anthropic.AnthropicError as e:
            raise LLMCallError("api_error", f"{type(e).__name__}: {e}",
                               status=getattr(e, "status_code", None)) from e


def response_from_message(msg: Any, latency_ms: float) -> LLMResponse:
    """An SDK Message as an LLMResponse."""
    text = next((b.text for b in msg.content if getattr(b, "type", None) == "text"), None)
    details = getattr(msg, "stop_details", None)
    u = msg.usage
    usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
             "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", None),
             "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", None)}
    return LLMResponse(text=text, model=msg.model, request_id=getattr(msg, "_request_id", None),
                       stop_reason=msg.stop_reason,
                       refusal_category=getattr(details, "category", None) if details else None,
                       usage=usage, raw_json=msg.model_dump_json(), latency_ms=latency_ms)


__all__ = ["AnthropicClient", "response_from_message"]
