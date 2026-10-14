# agents

Shadow-decision agents for XAUUSD: a LangGraph graph over the datafeed store.

**Status: skeleton, branch `feat/agents-skeleton`. Nothing calls an LLM,
nothing trades, and nothing is deployed.** All three round-1 agents are stubs
that abstain. The debate (round 2) is off by default, and no real debaters
ship. Design and per-step decisions: `agents/DESIGN.md`. Final report:
`agents/REPORT.md`.

```
trigger → snapshot ─(stale)──────────────────────────────► store
             │
           gate ─(market closed / too few bars)──────────► store
             │
  visual | technical | sentiment     in parallel, each sees only the Snapshot
             │
         aggregator  → blind Decision
             │
       [debate: bull vs bear → judge] → debate Decision    (debate.enabled)
             │
            risk (stub: pass-through)
             │
           store  → agent SQLite ($AGENTS_DB)
```

## Install and test

```
python3.13 -m venv .venv
.venv/bin/pip install -e '.[feed,replay,dev,agents]'
.venv/bin/pytest tests/agents_tests tests/datafeed
```

The `agents` extra pins `langgraph==1.2.12` and `pydantic==2.13.5`. There's no
LLM SDK. The tests build a synthetic replay store through datafeed's own replay
path. One test also replays a real parquet day when
`database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet` is present.

## Use

```python
from datetime import datetime, timezone
from agents.config import AgentsConfig
from agents.graph import build_graph, run_once
from agents.schemas import TriggerEvent
from agents.store import SqliteAgentStore

cfg = AgentsConfig.from_toml("agents/config.example.toml")     # snapshot.mode = "replay"
store = SqliteAgentStore("agents.sqlite")                      # or $AGENTS_DB
graph = build_graph(cfg, store, db="datafeed.sqlite")          # or $DATAFEED_DB
out = run_once(graph, TriggerEvent(source="manual", t=datetime(2025, 6, 11, 14, 30, tzinfo=timezone.utc),
                                   trigger_id="1"))
out["status"], out["final_decisions"]["blind"].direction       # ('done', 'none')
```

**Trigger.** Anything that yields `TriggerEvent`s can drive the graph:
`agents.trigger.Trigger` plus `drive(trigger, graph)`. A clock and an external
event are both possible; neither is built here.

**Debate.** Set `debate.enabled = true` and pass
`debaters={"bull": ..., "bear": ...}` to `build_graph`. A debater implements
`argue(view: DebaterView) -> Turn | dict`. The judge is chosen by `debate.judge`
from `agents.debate.judges.JUDGES`; only `code_v0`, a placeholder, exists.

## The rules the code enforces

- **As of t.**
  - `as_of = floor(t, 1 min)`. Bars come only from
    `datafeed.store.query.bars_before(symbol, as_of, …)`, so every bar is
    `< as_of <= t`. The forming minute is never included.
  - Live: kinds live/history, `known_by = t`. Replay: kind replay only.
- **Seal.** `as_of` after 2026-10-01 00:00 UTC raises `SealedError`. There's
  no override.
- **Stale.** If the newest 1m bar completed more than `max_staleness` (5 min)
  before `as_of`, the run is skipped. It gets a `runs` row with the reason, and
  nothing else is written.
- **Snapshot first.** The snapshot is hashed (sha256 of canonical JSON) and
  saved before any agent runs. Loading it re-checks the hash.
- **Blind round 1.** Each voter gets its own copy of the Snapshot and nothing
  else. An exception, a timeout or an invalid output becomes an abstain with a
  reason, never a guess.
- **Debate.** A debater sees only the snapshot, the round-1 votes and the
  opponent's previous turn, all without timings or model ids. Every claim
  needs an `evidence_ref` that resolves inside the snapshot. Invalid claims are
  logged and hidden from the judge. A failing debater gives an empty turn; a
  failing judge gives `hold`.
- **Two arms, never merged.** The blind and debate decisions are separate rows
  on the same snapshot, so they can be scored against each other.
- **Append-only store.** Triggers abort every UPDATE and DELETE, and each run is
  written in one transaction.

## Store (`$AGENTS_DB`)

| Table | Key |
|---|---|
| `snapshots` | snapshot_hash |
| `runs` | run_id |
| `votes` | (run_id, round, agent), round 1 = blind |
| `debate_turns` | (run_id, round, role) |
| `decisions` | (run_id, arm), arm blind / debate |

`snapshots`, `votes`, `debate_turns` and `decisions` carry `model_id`,
`prompt_hash`, `tokens_in`, `tokens_out` and `latency_ms`. Details:
`agents/store/sqlite.py`.

## Modules

| Module | Status |
|---|---|
| `schemas.py`, `hashing.py`, `config.py` | v0 |
| `snapshot.py`, `resample.py` | v0 |
| `graph.py`, `trigger.py` | v0 |
| `voters/visual.py`, `technical.py`, `sentiment.py` | **stubs** (abstain) |
| `nodes/gate.py`, `nodes/aggregator.py` | v0, **placeholder** rules |
| `nodes/risk.py` | **stub** (pass-through until a risk/ package) |
| `debate/` | v0; `CodeJudgeV0` is a **placeholder**; LLM judge slot not implemented |
| `prompts/bull.md`, `bear.md` | templates for future LLM debaters, **not called** |
| `store/sqlite.py`, `store/memory.py` | v0 |
