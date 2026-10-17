# agents

Shadow-decision agents for XAUUSD: a LangGraph graph over the datafeed store.

**Status: agents v2 (docs/AGENTS_ARCHITECTURE.md), built through job 4.
Nothing trades, and nothing is deployed.**
- **Without an LLM client** the round-1 agents are stubs, and GEX abstains
  until options data exists.
- **With one** (`build_graph(llm_client=...)`), visual, technical and
  sentiment are LLM agents driven by the owner's prompts and `agents.toml`.
- **After round 1**, a discussion runs between the LLM agents.

The skeleton's design and report (`agents/DESIGN.md`, `agents/REPORT.md`) are
history: they describe the bull/bear debate that the discussion replaced.

```
trigger → snapshot ─(stale)──────────────────────────────► store
             │
           gate ─(market closed / too few bars)──────────► store
             │
  visual | technical | sentiment | gex     in parallel, blind: each sees only the Snapshot
             │
        meta_blind  → Decision "blind"      (the owner's Meta rule)
             │
        discussion  (N rounds, early stop; LLM agents only)
             │
     meta_discussion → Decision "discussion" (the same rule, final votes)
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

**Discussion.** After round 1, the LLM agents (voters with a `discuss` method)
see each other's votes and may change theirs only by citing new snapshot
evidence (agents/discussion/README.md). The rounds and the early stop come
from the owner's `agents.toml`, and the Meta rule too (agents/meta/README.md).
Without LLM agents, the discussion is skipped with the reason, and both
decisions are still stored.

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
- **Discussion.** A discussant sees only the snapshot, its own previous vote
  and the others' latest votes, all without timings or model ids.
  - A change needs a new snapshot ref; a `vote:` ref never counts alone.
  - A refused change keeps the previous vote and is stored with its reason
    and the refused vote.
  - A failing discussant keeps its vote.
- **Two arms, never merged.** The `blind` and `discussion` decisions are
  separate rows on the same snapshot, decided by the same Meta rule, so they
  can be scored against each other.
- **Append-only store.** Triggers abort every UPDATE and DELETE, and each run is
  written in one transaction.

## Store (`$AGENTS_DB`)

| Table | Key |
|---|---|
| `snapshots` | snapshot_hash |
| `runs` | run_id |
| `votes` | (run_id, round, agent): round 1 = blind, 2.. = discussion rounds |
| `discussions` | run_id: rounds run, early stop, skip reason, speakers |
| `decisions` | (run_id, arm): arm blind / discussion (technical_v1 reserved) |
| `llm_calls`, `llm_responses` | call_id; prompt_hash (the cache) |
| `debate_turns` | history of the old debate; no longer written |

`snapshots`, `votes` and `decisions` carry `model_id`,
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
| `voters/visual.py`, `technical.py`, `sentiment.py` | **stubs** (abstain) without an LLM client |
| `voters/gex.py` | code; abstains `no options data source` |
| `meta/` | the pluggable Meta rules `quorum_v0`, `fixed_meta_v1`: [meta/README.md](meta/README.md) |
| `discussion/` | the discussion subgraph and the vote-change rule: [discussion/README.md](discussion/README.md) |
| `evidence.py` | evidence refs, checked in code |
| `features.py` | the snapshot features: HAR σ at 60/240 and L, S_hd, T from datafeed's vol_block (`snapshot.sigma_model`) |
| `technical/` | the Technical metric table shared by the Technical-LLM and Technical v1: [technical/README.md](technical/README.md) |
| `nodes/gate.py` | v0, **placeholder** rules |
| `nodes/risk.py` | **stub** (pass-through until a risk/ package) |
| `prompts/bull.md`, `bear.md` | the owner's old debate prompts, **unused**; left for the owner to delete |
| `store/sqlite.py`, `store/memory.py` | v0 |
