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

All dependencies are pinned, with hashes, in `requirements.lock` at the repo
root:
- every extra (feed, replay, dev, agents, research);
- every transitive dependency;
- the build backend.

```
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install --no-deps --no-build-isolation -e .
.venv/bin/pytest tests/agents_tests tests/datafeed
.venv/bin/pytest                     # everything: research + datafeed + agents
```

A bare `pytest` also runs the research suites (80 tests). They need the
untracked data from the main checkout, symlinked file by file into ignored
directories:
- `database/ohlcv_data/*`;
- `research/vol_features_r2/cache/xauusd_txt_pre2024_filetime.parquet`.

The direct pins live in `pyproject.toml`: `langgraph==1.2.12`,
`pydantic==2.13.5`, `pytest-timeout==2.4.0`, etc. There's no LLM SDK. To
regenerate the lock after changing a pin (pip-tools 7.6.1, in a separate tool
venv):

```
pip-compile --all-extras --all-build-deps --generate-hashes --allow-unsafe --strip-extras \
    --annotation-style=line --constraint requirements.cutoff.txt \
    --output-file requirements.lock pyproject.toml
```

`requirements.cutoff.txt` caps the packages whose newest release was under two
weeks old at lock time. `tests/agents_tests/test_lock.py` checks that the lock
matches the pyproject pins and the caps.

The tests build a synthetic replay store through datafeed's own replay path.
One test also replays a real parquet day when
`database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet` is present. Each test has a
120 s limit (pytest-timeout).

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

## Prompts, config and epochs (agents v2, job 2)

- **The owner writes** the prompts in `agents/prompts/` (templates ship with
  `version: TEMPLATE` and are refused until written) and `agents.toml` (from
  `agents/agents.example.toml`).
- **Code never supplies** a default prompt, model, effort, `max_tokens` or
  Meta rule. A missing or invalid one makes the agent that needs it abstain,
  with the reason.
- **Every decision carries** the epoch id, the SHA-256 of `agents.toml` and of
  every prompt file. Any change is a new epoch. See
  [docs/PROMPT_GUIDE.md](../docs/PROMPT_GUIDE.md) and
  [docs/AGENTS_ARCHITECTURE.md](../docs/AGENTS_ARCHITECTURE.md).

```
python -m agents.prompting check --config agents.toml
python -m agents.epoch --config agents.toml --db agents.sqlite
```

## The rules the code enforces

- **As of t.**
  - `as_of = floor(t, 1 min)`. Bars come only from
    `datafeed.store.query.bars_before(symbol, as_of, …)`, so every bar is
    `< as_of <= t`. The forming minute is never included.
  - Live: kinds live/history, `known_by = t`. Replay: kind replay only.
- **Seal.** `as_of` after 2026-10-01 00:00 UTC raises `SealedError`. There's
  no override.
- **Stale.** If the newest 1m bar is more than `max_staleness` (5 min) behind
  the last market-open minute before `as_of`, the run is skipped. Closed
  minutes (the daily break, the weekend) don't count, so a reopen trigger runs.
  A skipped run gets a `runs` row with the reason, and nothing else is written.
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
| `schemas.py`, `hashing.py`, `config.py` | v0; snapshot schema 3 (alert facts, slots marked unavailable until built) |
| `owner_config.py` | the owner's `agents.toml` (`$AGENTS_CONFIG`): per-agent, per-round model/effort/max_tokens, discussion, Meta, LLM, budget. Validated per section; problems are abstain reasons |
| `prompting/` | load, check and render the owner's prompts (`agents/prompts/`): [prompting/README.md](prompting/README.md) |
| `epoch.py` | the epoch id over prompts, config and input code, stamped on every run and decision; `python -m agents.epoch` |
| `llm/` | the LLM client layer: official SDK (lazy), cache, budget, kill switch, a ledger row per attempt, `LLMVoteAgent`: [llm/README.md](llm/README.md) |
| `snapshot.py`, `resample.py` | v0 |
| `graph.py`, `trigger.py` | v0 |
| `voters/visual.py`, `technical.py`, `sentiment.py` | **stubs** (abstain) |
| `nodes/gate.py`, `nodes/aggregator.py` | v0, **placeholder** rules |
| `nodes/risk.py` | **stub** (pass-through until a risk/ package) |
| `debate/` | v0; `CodeJudgeV0` is a **placeholder**; LLM judge slot not implemented |
| `prompts/bull.md`, `bear.md` | templates for future LLM debaters, **not called** |
| `store/sqlite.py`, `store/memory.py` | v0 |
