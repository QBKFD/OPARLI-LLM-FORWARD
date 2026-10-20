"""AnthropicClient against a fake SDK object: real SDK types and exception
classes, no network. Checks one attempt per call, the error mapping and the
response conversion."""

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from anthropic.types import Message  # noqa: E402

from agents.llm.anthropic_client import AnthropicClient, response_from_message  # noqa: E402
from agents.llm.client import LLMCallError  # noqa: E402

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def message(**kw):
    base = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": '{"direction": "none"}'}], "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5,
                      "cache_creation_input_tokens": 0}}
    return Message.model_validate({**base, **kw})


class FakeMessages:
    def __init__(self, outcome):
        self.outcome, self.calls = outcome, []

    def create(self, **kw):
        self.calls.append(kw)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def count_tokens(self, **kw):
        self.calls.append(kw)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return type("Count", (), {"input_tokens": 777})()


class FakeSDK:
    def __init__(self, outcome):
        self.messages = FakeMessages(outcome)


def status_error(cls, code):
    return cls("boom", response=httpx2.Response(code, request=REQ, headers={"request-id": f"req_{code}"}), body=None)


def test_one_attempt_with_the_timeout_and_the_payload_as_given():
    sdk = FakeSDK(message())
    payload = {"model": "claude-opus-5-5", "max_tokens": 10, "system": "s",
               "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
               "output_config": {"effort": "low"}}
    r = AnthropicClient(sdk, clock=iter([1.0, 1.25]).__next__).complete(payload, timeout_s=12.0)
    assert sdk.messages.calls == [{**payload, "timeout": 12.0}]
    assert (r.text, r.model, r.stop_reason, r.latency_ms) == ('{"direction": "none"}', "claude-opus-5-5",
                                                              "end_turn", 250.0)
    assert r.usage == {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5,
                       "cache_creation_input_tokens": 0}
    assert '"msg_1"' in r.raw_json


def test_a_refusal_keeps_its_category():
    r = response_from_message(message(stop_reason="refusal", content=[], stop_details={"type": "refusal",
                                                                                       "category": "bio"}), 1.0)
    assert (r.text, r.stop_reason, r.refusal_category) == (None, "refusal", "bio")


@pytest.mark.parametrize("exc, kind, status, retryable", [
    (anthropic.APITimeoutError(request=REQ), "timeout", None, True),
    (anthropic.APIConnectionError(request=REQ), "connection", None, True),
    (status_error(anthropic.RateLimitError, 429), "api_error", 429, True),
    (status_error(anthropic.InternalServerError, 500), "api_error", 500, True),
    (status_error(anthropic.BadRequestError, 400), "api_error", 400, False),
    (status_error(anthropic.AuthenticationError, 401), "api_error", 401, False),
    (status_error(anthropic.NotFoundError, 404), "api_error", 404, False),
])
def test_errors_map_to_one_failed_attempt(exc, kind, status, retryable):
    with pytest.raises(LLMCallError) as e:
        AnthropicClient(FakeSDK(exc)).complete({"model": "m"}, timeout_s=1.0)
    assert (e.value.kind, e.value.status, e.value.retryable) == (kind, status, retryable)
    if status is not None:
        assert e.value.request_id == f"req_{status}"


def test_count_tokens_sends_only_the_countable_fields():
    sdk = FakeSDK(None)
    n = AnthropicClient(sdk).count_tokens({"model": "m", "max_tokens": 5, "system": "s", "messages": [],
                                          "output_config": {}})
    assert n == 777 and sdk.messages.calls == [{"model": "m", "system": "s", "messages": [], "output_config": {}}]
    with pytest.raises(LLMCallError):
        AnthropicClient(FakeSDK(status_error(anthropic.BadRequestError, 400))).count_tokens({"model": "m"})


def test_the_default_sdk_client_has_retries_off(monkeypatch):
    seen = {}

    class Recorder:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setattr(anthropic, "Anthropic", Recorder)
    AnthropicClient()._client()
    assert seen == {"max_retries": 0}
