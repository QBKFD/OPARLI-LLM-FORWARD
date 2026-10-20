"""The LLM client layer (agents v2, job 3; docs/AGENTS_ARCHITECTURE.md §6).

    request.py           LLMRequest: what is sent, and its prompt_hash
    client.py            the LLMClient protocol, LLMResponse, LLMCallError
    anthropic_client.py  the official SDK (imported lazily; the `llm` extra)
    fake.py              FakeLLMClient for tests
    ledger.py            llm_calls and the llm_responses cache
    budget.py            the daily call budget and the kill switch
    call.py              one call: cache, budget, retries, ledger
    parse.py             the answer, checked in code
    agent.py             LLMVoteAgent: prompt + config + client -> Vote
    pricing.py           list prices for the cost column

Importing this package doesn't load the SDK.
"""

from agents.llm.agent import LLMVoteAgent, make_llm_voters
from agents.llm.budget import Budget
from agents.llm.call import CallContext, CallDeps, CallOutcome, run_call
from agents.llm.client import LLMCallError, LLMClient, LLMResponse
from agents.llm.fake import FakeLLMClient, fake_response
from agents.llm.ledger import CallRecord, LLMLedger, MemoryLedger, SqliteLedger
from agents.llm.request import LLMRequest, round1_request

__all__ = ["Budget", "CallContext", "CallDeps", "CallOutcome", "CallRecord", "FakeLLMClient", "LLMCallError",
           "LLMClient", "LLMLedger", "LLMRequest", "LLMResponse", "LLMVoteAgent", "MemoryLedger", "SqliteLedger",
           "fake_response", "make_llm_voters", "round1_request", "run_call"]
