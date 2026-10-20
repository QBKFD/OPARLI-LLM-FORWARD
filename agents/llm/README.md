# agents/llm

The LLM client layer (agents v2, job 3): one model call per agent vote, with
a response cache, a daily budget, a kill switch and a row per attempt. The
design is [docs/AGENTS_ARCHITECTURE.md](../../docs/AGENTS_ARCHITECTURE.md) §6.
No default model or prompt exists here: both come from the owner's files
(`agents/owner_config.py`, `agents/prompting/`).

| Module | What |
|---|---|
| `request.py` | `LLMRequest`: model, effort, `max_tokens`, system, user blocks and the output schema. No temperature, top_p or top_k. `prompt_hash` covers the whole body (images by SHA-256) and is the cache key |
| `client.py` | the `LLMClient` protocol (one attempt per call), `LLMResponse`, `LLMCallError` (kind, status, request id, retryable) |
| `anthropic_client.py` | the official SDK, imported lazily (the `llm` extra, `anthropic==1.11.0`). SDK retries off; no sampling parameters, no thinking setting, no refusal fallbacks |
| `fake.py` | `FakeLLMClient`: scripted responses and errors, for tests |
| `ledger.py` | `llm_calls` (every attempt, cache hit and rerun miss) and `llm_responses` (the cache). SQLite on the agent store (migration 4), or in memory |
| `budget.py` | `budget.max_calls_per_day` (attempts per UTC day, reserved under a lock) and the kill switch (`budget.kill_switch_path` or `AGENTS_LLM_KILL=1`) |
| `call.py` | `run_call`: cache → kill switch → budget → up to 1 + `llm.max_retries` attempts with capped backoff. The response is stored before it is used |
| `parse.py` | the answer checked in code: fields, bounds, every evidence ref (`check_ref`), and a directional vote needs a ref that resolves |
| `agent.py` | `LLMVoteAgent` (prompt + config + client → `Vote` with evidence) and `make_llm_voters` |
| `pricing.py` | list prices for the cost column (an estimate, not billing) |
| `__main__.py` | `python -m agents.llm count-tokens`: the real input tokens of each prompt on a replay snapshot |

## Modes (`llm.mode` in agents.toml)

- **live:** a cache hit is reused, with no new call. On a miss: the kill switch,
  then the budget, then the call. The response is stored, and the stored copy
  is used.
- **rerun:** cache only. A miss abstains `cache_miss` and never calls, so a
  rerun is byte-identical.

## Abstain reasons

`prompt_invalid: …`, `config_invalid: …`, `kill_switch: …`, `budget: …`,
`cache_miss`, `timeout …`, `connection …`, `api_error <status>: …`,
`refusal:<category>`, `truncated: …`, `model_mismatch: …` and
`invalid: …` (bad JSON, bounds, a direction without evidence). Every one is
stored on the vote. Every attempt is a row in `llm_calls`, with its request
id, usage, latency and cost.

## Run

```
pip install --require-hashes -r requirements.lock       # includes the llm extra
export ANTHROPIC_API_KEY=...                            # or `ant auth login`
python -m agents.llm count-tokens --db replay.sqlite --t 2025-06-11T14:30:00Z --config agents.toml
```

`build_graph(..., llm_client=AnthropicClient())` makes visual, technical and
sentiment LLM voters. Without `llm_client` they stay stubs.

## Test

```
pytest tests/agents_tests/test_llm_layer.py tests/agents_tests/test_llm_agent.py tests/agents_tests/test_anthropic_client.py
```

No test calls the API. The client tests use the SDK's own types and exception
classes against a fake SDK object.
