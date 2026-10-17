# agents/discussion

The discussion between the LLM agents, after the blind round 1
([docs/AGENTS_ARCHITECTURE.md](../../docs/AGENTS_ARCHITECTURE.md) §5.2–5.3).
It replaced the bull/bear debate.

| Module | What |
|---|---|
| `rule.py` | `apply_change_rule(previous, proposal, round)`: the vote-change rule, pure. A change of direction or confidence is accepted only with a new, resolving, non-`vote:` snapshot ref. Otherwise the previous vote is carried forward with `change_rejected` and the refused vote kept as `rejected_proposal`. A failed proposal carries the previous vote too (`call failed: …`) |
| `subgraph.py` | the LangGraph subgraph: one node per discussant, simultaneous rounds, content-only views, `safe_discuss` (failure or timeout → abstain → carried forward), early stop after a round without an accepted change |
| `node.py` | `make_discussion`: runs the subgraph, or records why nobody spoke (no discussants, an invalid `[discussion]`, an invalid `discussion.md`, everyone abstained in round 1) |

**Speakers** are the voters with a `discuss(view)` method (the LLM agents)
whose round-1 vote didn't abstain. A discussion vote of round r is stored with
`round = r + 1` (round 1 is the blind vote).

## Test

```
pytest tests/agents_tests/test_discussion.py tests/agents_tests/test_llm_discussion.py tests/agents_tests/test_acceptance.py
```
