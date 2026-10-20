# tools/langgraph_studio

`studio.py` is the LangGraph Studio entry point for the agents graph:
`make_graph` for `langgraph dev` (via `langgraph.json` at the repo root), plus
a small CLI.
- `run`: one graph run on a replay store, printed as JSON.
- `build-db`: writes a replay store, either the synthetic one or development
  bars from the research parquet, which must end by 2021-01-01.

Replay only, no LLM, no keys. Setup, what to look at and measurements:
[docs/LANGGRAPH_STUDIO.md](../../docs/LANGGRAPH_STUDIO.md).

```
python tools/langgraph_studio/studio.py run --t 2025-06-11T14:30:00Z
pytest tests/agents_tests/test_studio.py
```

The synthetic replay data of the agents tests (`tests/agents_tests/agents_testkit.py`)
comes from this module too, so there is one copy of it.
