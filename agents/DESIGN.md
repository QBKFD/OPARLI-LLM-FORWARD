# Agents skeleton: design

> **History.** This records the skeleton as built on 2026-10-14. Its bull/bear
> debate (§0.5, step 5) and aggregator v0 were replaced in agents v2 (job 4) by
> the discussion and the pluggable Meta rules: see `docs/AGENTS_ARCHITECTURE.md`.

Design notes for the `feat/agents-skeleton` branch (from `feat/datafeed-v1` @
c3d25fb). Step 0 is the proposal below. Later steps append their decisions.

**Overnight run (2026-10-14).** The owner asked for steps 1–7 to run without a
"go" between them, following this proposal. Q1–Q4 (end of section 0) are
applied as the proposed defaults and are still open for the owner to confirm.
A question that blocks a step goes at the top of this file.

**Result:** all steps (1–7) finished, and no step was blocked. The report and
the open questions (Q1–Q4 plus six new ones) are in `agents/REPORT.md`.

**Owner decisions (2026-10-14)** are applied, one commit each. See "Owner
decisions" at the end of this file and section 0 of `agents/REPORT.md`.

Scope: a LangGraph skeleton under a new top-level `agents/`. Shadow decisions
only, no broker, no LLM calls, no API keys. It reads no code from `backend/` or
`legacy/`, and it doesn't modify `datafeed/`, `backend/`, `deploy/` or
`research/`. Market data comes only from `datafeed.store.query.bars_before`.

## 0. Proposal (step 0)

### 0.1 Folder layout

```
agents/
  __init__.py
  README.md               usage, config, the "as of t" rule for agents (step 7)
  config.py               AgentsConfig, DebateConfig (pydantic, defaults below)
  schemas.py              Bar, Snapshot, Vote, Claim, Turn, DebateResult, Decision, TriggerEvent
  hashing.py              canonical JSON + sha256 (snapshot hash, view/prompt hashes)
  snapshot.py             build_snapshot(): bars_before → resample → hash; Stale result
  resample.py             1m → 15m / 1h / 4h, stdlib only
  trigger.py              TriggerEvent + Trigger protocol only (no clock or event implementation)
  graph.py                RunState, build_graph(config, voters, debaters, judge, store, clock)
  nodes/
    gate.py               go / no-go after the snapshot
    aggregator.py         round-1 votes → blind Decision (code, v0)
    risk.py               pass-through stub
    store.py              writes the run in one transaction
  voters/
    base.py               VoteAgent protocol + safe_vote() wrapper (error/timeout/invalid → abstain)
    visual.py             stub → abstain
    technical.py          stub → abstain
    sentiment.py          stub → abstain
  debate/
    subgraph.py           DebateState, build_debate_graph()
    debaters.py           Debater protocol, DebaterView, safe_turn() wrapper
    evidence.py           parse and check evidence_ref against the snapshot
    judges.py             Judge protocol, CodeJudgeV0, registry (slot for an LLM judge)
  prompts/
    bull.md               future LLM template, not called
    bear.md               future LLM template, not called
  store/
    schema.py             numbered migrations, connect() ($AGENTS_DB, its own SQLite)
    writer.py             save_snapshot(), save_run()
tests/agents_tests/       (not tests/agents/: see step 4)
  conftest.py             replay store fixture; slow-exit watchdog
  agents_testkit.py       synthetic replay data through the datafeed replay path
  agents_fakes.py         FakeBull, FakeBear, raising/slow agents and debaters (tests only)
  test_*.py
```

The fake debaters live in `tests/agents_tests/agents_fakes.py`, so the package
itself contains no fake decision code.

### 0.2 Name collision with the old `agents` package

**Current layout** (since main's cleanup, merged as 2b5d479):
- The old agent code lives in `legacy/backend_v0/agents/`.
- The old shared conftest lives in `legacy/tests_agents/conftest.py`.
- `backend/` holds only the scanner (`deterministic_scanner.py`,
  `services/`).
- `legacy/` is never collected (`norecursedirs`) and never on `sys.path`.

So `import agents` can only load the repo-root package. The guard test
(`test_agents_package.py::test_agents_is_the_repo_root_package`) asserts that
`agents.__file__` is the repo-root package, and it stays.

**History.** Before the cleanup:
- `backend/agents/` was a package too, and `tests/conftest.py` put `backend/`
  at `sys.path[0]`, so under pytest `import agents` could have loaded the old
  code.
- The agents test conftest removed `backend/` from the path. That code was
  deleted once it was dead (see "Layout after the cleanup merge").
- Step 4 found a second collision, with the test directory itself; see
  there.

### 0.3 Snapshot: what was knowable at t

**Time.** `as_of = floor(t, 1 min)`. The builder calls
`bars_before(symbol, as_of, ...)`, not `bars_before(symbol, t, ...)`. Reason:
for t = 12:00:30, `bar_ts < t` includes the 12:00 bar, which is still forming
at t. In replay that bar is already complete in the store, so it would be
lookahead. Flooring t to the minute removes that.

**Sources.**
- Live: `kinds=("live","history")` and `known_by=t`. A later rebuild then
  gives the same bars even after a backfill, because the store is append-only
  and refuses revisions.
- Replay: `kinds=("replay",)` and `known_by=None`. Replay `ingest_ts` is the
  time the replay ran, so `known_by=t` would hide every bar.

**Seal.** The builder refuses `as_of > 2026-10-01 00:00 UTC`
(`datafeed.ingest.xauusd_bars.SEAL_START`, already tested equal to the research
seal). There's no override in this PR. See question Q1.

```
Bar (frozen)
  ts: datetime              bar open time, UTC
  open, high, low, close: float
  n_1m: int                 1-min bars inside (1 for 1m bars; gaps stay visible)
  complete: bool            bucket end <= as_of (only the last bucket can be partial)
  kind: str | None          1m only: live / history / replay

Snapshot (frozen)
  schema_version: int = 1
  symbol: str = "XAUUSD"
  as_of: datetime           every bar has ts < as_of
  kinds: tuple[str, ...]
  known_by: datetime | None
  bars: {"1m": (Bar...), "15m": (...), "1h": (...), "4h": (...)}
  newest_bar_ts: datetime   newest 1m bar
  levels: tuple[Level, ...] = ()        empty until a levels/ package exists
  features: dict[str, float] = {}       empty until features exist
  events: tuple[Event, ...] = ()        empty until datafeed/ingest/calendar.py exists
  hash: str                 sha256 of everything above (not of t, run_id or wall-clock time)
```

**Resampling.**
- Stdlib only. Buckets are aligned to the UTC epoch, so 4h bars open at 00, 04,
  08, 12, 16 and 20 UTC (question Q4).
- OHLC per bucket: first open, max high, min low, last close.
- No forward fill and no empty buckets. There's no volume, because the feed
  has none.
- The last bucket per timeframe may be partial (`complete=False`). It's built
  only from bars `< as_of`.

**Default sizes** (config): fetch `timedelta(days=30)` of 1m bars, then keep the
last 240 × 1m, 192 × 15m, 120 × 1h and 120 × 4h. That's about 70 KB of JSON per
snapshot. The stored snapshot is what the agents saw, including the resampled
bars, so a change to the resampling code shows up as a different hash.

**Hash.** sha256 over canonical JSON: sorted keys, compact separators,
datetimes as `YYYY-MM-DDTHH:MM:SS+00:00` (the datafeed canonical form), and
floats via `repr` (exact round-trip). The same `(symbol, as_of, kinds, known_by,
config, store contents)` always gives the same hash.

**Stale.**
- If `as_of − (newest_bar_ts + 1 min) > max_staleness` (default 5 min), the
  builder returns `Stale(reason, newest_bar_ts)` and no snapshot is saved.
- The run is logged as skipped, with the reason. No votes and no decisions are
  written.

**Saving.** The snapshot node saves the snapshot before the graph reaches any
agent node.

### 0.4 Graph

```
trigger → snapshot ─(stale)──────────────────────────────────────────────┐
             │                                                            │
             ▼                                                            │
           gate ─(no-go)─────────────────────────────────────────────────┤
             │ fan-out                                                    │
     ┌───────┼────────┐                                                   │
  visual  technical  sentiment     (one superstep; each gets only the Snapshot)
     └───────┼────────┘ fan-in (waits for all three)                      │
             ▼                                                            │
         aggregator → blind Decision                                      │
             │                                                            │
   debate.enabled? ──yes──► debate subgraph → debate Decision             │
             │ no                 │                                       │
             ▼                    ▼                                       │
            risk (stub, pass-through for each Decision)                   │
             ▼                                                            │
           store ◄────────────────────────────────────────────────────────┘
             ▼
            END
```

**Trigger.** The graph's input is a `TriggerEvent(source, t, trigger_id,
payload)`. `source` is a free label ("clock", "scanner", ...). Whatever produces
these events (a clock or an external event) sits outside the graph and isn't
chosen in this task. The `trigger` node validates the event (tz-aware UTC t).
The payload is stored on the run row and is **not** given to the agents in v0.

**Gate (v0).** No-go if `_market_open(as_of)` is false, or if any timeframe has
fewer bars than its configured minimum. No-go ends the run as `skipped`, with
the reason. The gate is also the slot for later trigger-specific filters.

**Blindness** is enforced two ways:
- Structural: a voter's interface is `vote(snapshot: Snapshot) -> Vote | dict`.
  It never receives the graph state.
- LangGraph: all three voters run in the same superstep, so none of them can
  see another's update.

**`safe_vote`.**
- Exception, timeout (`node_timeout_s`), wrong type, or a dict that fails Vote
  validation → `Vote.abstain(agent, reason=...)`. The graph still finishes.
- Latency is measured with an injected monotonic clock, so tests can fix it.

**Aggregator v0** (the spec doesn't define the rule; question Q2). A placeholder,
not to be tuned on results:
- `direction = d` if at least 2 of the 3 agents vote `d` (abstains count as not
  `d`), otherwise `none`.
- Confidence is ignored.
- With the three stubs, the blind decision is always `none` ("3/3 abstained").

**Risk.** A pass-through. `entry`, `sl`, `tp`, `size` and `sigma` stay null.

**Store.**
- One transaction at the end: the run row, round-1 votes, debate turns and
  decisions.
- A skipped run writes only its run row.
- The snapshot was already written by the snapshot node. If the process crashes
  in between, there's an orphan snapshot with no run row, which is detectable.

```
RunState (TypedDict)
  run_id: str
  trigger: TriggerEvent
  status: "running" | "skipped" | "done"
  skip_reason: str | None
  snapshot: Snapshot | None
  votes: Annotated[dict[str, Vote], merge_disjoint]   round 1, keyed by agent; a duplicate key raises
  decisions: Annotated[dict[str, Decision], merge_disjoint]   keyed by arm: "blind", "debate"
  debate: DebateResult | None
  errors: Annotated[list[str], operator.add]
```

Config, voters, debaters, judge, store and clock are bound when the graph is
built (`build_graph(...)`). They aren't part of the state, so the state stays
plain data. No LangGraph checkpointer: the agent store is the log.

### 0.5 Debate subgraph (disabled by default)

**Input.** The parent node invokes the subgraph with only `snapshot`, the
round-1 `votes`, and `blind_direction`:
- debaters get the snapshot and the votes, as the spec says;
- the judge also gets `blind_direction`, the aggregator's output from the same
  votes, because judge v0 needs the round-1 direction.

Nothing else in the parent state is passed in.

**Rounds.** Rounds are simultaneous (question Q3):
- Round 1 (the opening): bull and bear each see the snapshot and the votes, but
  not each other.
- Round r > 1: each side also sees the opponent's round r − 1 turn, and nothing
  else. That excludes its own earlier turns, as the spec says.

`max_rounds` defaults to 2 (opening + one rebuttal).

```
DebaterView (frozen): what a debater receives, and all it receives
  role: "bull" | "bear"
  round: int
  snapshot: Snapshot
  round1_votes: tuple[Vote, ...]
  opponent_previous_turn: Turn | None     None in round 1
```

**Evidence check.** It runs right after each turn, in code. A claim gets
`valid=False` with an `invalid_reason` if its `evidence_ref` doesn't parse or
doesn't resolve inside the snapshot. The claim stays in the turn, and the judge
counts only valid claims. `evidence_ref` grammar:

| Form | Valid when |
|---|---|
| `bars:<tf>:<start>..<end>` | `<tf>` is in the snapshot, both ends are bar open times in that series, start ≤ end |
| `level:<id>` | the id is in `snapshot.levels` |
| `feature:<name>` | the name is in `snapshot.features` |
| `event:<id>` | the id is in `snapshot.events` |

Until levels, features and events exist, only `bars:` refs can be valid.

**`safe_turn`.** An exception or a timeout (`debate.turn_timeout_s`) produces an
empty turn: `claims=()`, `error="<reason>"`. The debate continues.

**Judge.**
- The `Judge` protocol takes `JudgeInput(turns, blind_direction)` and returns
  an outcome and a reason.
- The registry is keyed by `debate.judge`. An unknown name fails at build time.
- `CodeJudgeV0` is a placeholder, not to be tuned on results:
  - default: `hold`;
  - if one side has strictly more valid claims **and** its side (bull = long,
    bear = short) equals `blind_direction`, the outcome is that direction;
  - any error → `hold`, with the reason.
- With a blind `none`, the outcome is always `hold`.
- The LLM judge slot is a registry entry. It isn't implemented.

**Debate decision.** `long` / `short` / `hold`, where hold maps to Decision
direction `none`. It's stored as arm `debate` and never touches the blind rows.

**Prompts.** `agents/prompts/bull.md` and `bear.md` are templates for later and
aren't called in this task. They say:
- every claim needs an `evidence_ref` from the grammar above;
- unsupported claims are discarded;
- conceding is allowed (`conceded: true`).

### 0.6 Schemas (pydantic v2, frozen)

```
Vote
  agent: str                        "visual" | "technical" | "sentiment"
  round: int = 1                    1 = blind
  direction: "long" | "short" | "none"
  confidence: float                 0..1
  reason: str
  abstain: bool
  abstain_reason: str | None
  model_id, prompt_hash: str | None
  tokens_in, tokens_out: int | None
  latency_ms: float | None
  invariants:
    abstain ⇒ direction == "none", confidence == 0, abstain_reason non-empty
    not abstain ⇒ abstain_reason is None
  Vote.abstain(agent, reason) builds an abstain vote. An invalid or missing vote becomes one; there's no guessing.

Claim
  text: str
  evidence_ref: str                 raw, as the debater produced it
  valid: bool | None                None until the evidence check runs
  invalid_reason: str | None

Turn
  role: "bull" | "bear"
  round: int >= 1
  claims: tuple[Claim, ...]
  conceded: bool = False
  error: str | None                 set → claims is empty
  model_id: str | None = None
  prompt_hash: str | None           sha256 of the exact input (rendered prompt for an LLM, canonical DebaterView for code)
  tokens_in, tokens_out: int | None
  latency_ms: float | None

DebateResult
  snapshot_hash: str
  max_rounds: int
  turns: tuple[Turn, ...]           ordered by (round, role)
  n_valid: {"bull": int, "bear": int}
  judge_name: str
  outcome: "long" | "short" | "hold"
  reason: str

Decision
  run_id: str
  snapshot_hash: str
  arm: "blind" | "debate"
  direction: "long" | "short" | "none"
  reason: str
  decided_by: str                   "aggregator_v0" | judge name
  entry, sl, tp, size, sigma: float | None = None   filled by risk/ later
  model_id, prompt_hash, tokens_in, tokens_out, latency_ms: None   for an LLM judge later

TriggerEvent
  source: str
  t: datetime                       tz-aware UTC
  trigger_id: str
  payload: dict                     JSON; stored, not shown to agents
```

`tokens` is split into `tokens_in` and `tokens_out`. With a fixed clock, the
same snapshot and the same fake debaters give a byte-identical `DebateResult`.

### 0.7 Agent store (step 6 preview)

- Its own SQLite (`$AGENTS_DB`), WAL, numbered migrations like
  `datafeed/store/schema.py` (the pattern is copied; datafeed's migrator isn't
  imported).
- Append-only: `BEFORE UPDATE` / `BEFORE DELETE` triggers abort on every table.

| Table | Key | Notes |
|---|---|---|
| `runs` (**addition**) | run_id | trigger source/id/t/payload, as_of, status ok/skipped, skip_reason, snapshot_hash, config_json + hash, code version. It's needed to "log why" a run was skipped |
| `snapshots` | snapshot_hash | schema_version, symbol, as_of, kinds, known_by, newest_bar_ts, content_json. Identical content is a no-op |
| `votes` | (run_id, round, agent) | Vote fields; round 1 = blind |
| `debate_turns` | (run_id, round, role) | claims_json (with validity flags), n_valid, conceded, error, model fields |
| `decisions` | (run_id, arm) | Decision fields |

Every table that a model can fill has `model_id`, `prompt_hash`, `tokens_in`,
`tokens_out` and `latency_ms` columns.

**Config** (`AgentsConfig`):
- `symbol`, `mode` (live / replay), `fetch_lookback`, `keep` (per timeframe),
  `min_bars` (per timeframe), `max_staleness`, `node_timeout_s`;
- `debate.enabled` (default **false**), `debate.max_rounds` (default 2),
  `debate.judge` (default `"code_v0"`), `debate.turn_timeout_s`.

### 0.8 Tests (step 7 preview)

- **Data.** The parquet isn't in this worktree, so the main fixture is a small
  synthetic parquet. It goes through datafeed's own `ReplaySource` →
  `InMemoryBus` → `Recorder` into a temp store, so the bars are `kind='replay'`.
- One extra test uses the real parquet when it's present (symlinked, never
  committed) and skips otherwise.
- `pytest` `testpaths` gains `tests/agents`.

### 0.9 Questions for the owner

- **Q1. Seal vs live shadow runs.** `research/SEALING.md` says nothing may read
  data from 2026-10-01 onward. Live shadow decisions would read exactly that
  data, and scoring them would too. Proposal: the snapshot builder refuses
  `as_of > SEAL_START` in this PR, with no override, so the skeleton runs on
  replay only. Allowing live runs is a separate, explicit decision.
- **Q2. Aggregator v0 rule:** ≥ 2 of 3 agents in the same direction, otherwise
  `none`; confidence ignored. OK?
- **Q3. Debate turn order:** simultaneous rounds (blind openings; round r sees
  only the opponent's r − 1 turn), not sequential (bull first, then bear sees
  bull's turn). Simultaneous rounds have no first-mover advantage. OK?
- **Q4. 4h alignment:** UTC epoch (00/04/…/20). The alternative is NY-session
  alignment (18:00 NY open, shifts with DST). I'd use UTC for v0.

## Decisions and results by step

### Step 1: packaging and venv

- **`[agents]` extra:** `langgraph==1.2.12` (released 2026-09-21) and
  `pydantic==2.13.5` (2026-08-28). These are the newest releases at least two
  weeks old on 2026-10-14; langgraph 1.2.13–1.2.15 and pydantic 2.14.x are
  newer.
- **Packaging.** `agents*` is added to package discovery, and `prompts/*.md`
  is package data. The distribution is still named `datafeed`.
- **Venv.** `.venv/` in this worktree, Python 3.13.1, installed with
  `pip install -e '.[feed,replay,dev,agents]'`.
- **Transitive versions** as resolved (not pinned by the extra):
  - langchain-core 1.6.10, langgraph-checkpoint 4.3.0, langgraph-prebuilt
    1.1.0, langgraph-sdk 0.4.7;
  - langsmith 0.14.8, pydantic_core 2.46.5, ormsgpack 1.12.2, xxhash 4.0.1.

  No LLM client (anthropic, openai, …) is installed. langsmith comes in through
  langchain-core and only sends traces if `LANGSMITH_TRACING` /
  `LANGCHAIN_TRACING_V2` is set. Neither is set here, and a test (step 7)
  checks that no LLM client module gets imported.
- **Baseline** with this venv: `pytest tests/datafeed` gives **76 passed, 7
  skipped**, the same as the datafeed step 8 report. The 7 skips are 6 Redis
  tests and 1 manual IBKR test. The historical parquet is symlinked from the
  main checkout to `database/ohlcv_data/` (gitignored, not committed). The
  research suites need scipy, matplotlib and numba, which aren't installed in
  this venv. They aren't touched by this branch and weren't run.

### Step 2: schemas

`agents/schemas.py` and `agents/hashing.py`, as in section 0.6, with these
details:

- **Every model:** frozen, `extra="forbid"`, NaN and infinity refused,
  datetimes tz-aware and stored as UTC.
- **Snapshot hash.**
  - It's computed when the model is validated. A snapshot loaded with a hash
    that doesn't match its content raises, so tampering or a schema drift
    shows up when the snapshot is loaded.
  - The validator also refuses any bar with `ts >= as_of`, a misaligned bar, a
    wrong `complete` flag, non-increasing bars and duplicate level/event ids.
- **Vote invariants:**
  - abstain ⇒ direction `none`, confidence 0 and a non-empty
    `abstain_reason`;
  - not abstain ⇒ no `abstain_reason`.
- **Turn:** an `error` means an empty turn (no claims, no concession).
- **Claim:** `invalid_reason` is set exactly when `valid` is False.
- **DebateResult:** turns must be unique and ordered by (round, bull before
  bear). The `n_valid_*` counts must match the turns.
- **Placeholders.** `Level` and `Event` are minimal models (id, price/ts,
  label/title) that hold the slots in the snapshot until levels/ and the
  calendar ingest exist.
- **Canonical JSON** (`hashing.py`):
  - sorted keys, compact separators;
  - floats via repr, refusing NaN/inf;
  - datetimes as UTC `+00:00`;
  - tuples become lists;
  - anything else (sets, bytes, naive datetimes, non-str keys) raises.
- **Tests:** `tests/agents/conftest.py` takes `backend/` off `sys.path`
  (later removed: after the cleanup merge nothing puts `backend/` there; see
  0.2). `testpaths` gains `tests/agents`. `test_schemas.py`: 23 passed.

### Step 3: snapshot builder

`agents/snapshot.py`, `agents/resample.py`, `agents/config.py`
(`SnapshotConfig`).

- **`build_snapshot(t, cfg, db)`** returns a `Snapshot` or a
  `Stale(as_of, newest_bar_ts, reason)`.
  - `as_of = floor(t)`. Bars come from `bars_before(symbol, as_of, …)` only.
  - Live mode uses kinds live/history and `known_by=t`. Replay mode uses kind
    replay and `known_by=None`.
  - `as_of > SEAL_START` raises `SealedError`, with no override. `SEAL_START`
    is imported from `datafeed.ingest.xauusd_bars`, the same object datafeed
    tests against the research seal.
- **Fetch window alignment.** The window start is
  `bucket_start(as_of − fetch_lookback, largest kept timeframe)`. Without it,
  the oldest 4h bucket would be cut at the window edge but still labelled
  complete. A test compares a 1-day and a 2-day fetch.
- **`mode` has no default:** `live` or `replay` must be chosen explicitly.
- **Staleness.** The age is `as_of − (newest 1m bar_ts + 1 min)`, i.e. measured
  from when that bar completed. Stale means `age > max_staleness`; exactly 5
  min is still fresh. No bars at all is also `Stale`, with its own reason.
- **`prepare_snapshot(t, cfg, db, sink)`.**
  - Fresh: it saves the snapshot to the sink and returns it. The graph's
    snapshot node calls it before any agent node (step 4).
  - Stale: it logs a warning (`run skipped at as_of …: <reason>`) and saves
    nothing. The skip also gets a persistent row in `runs` (step 4/6).
- **`known_by` is part of the hash.** In live mode it's t, so two triggers in
  the same minute can give different hashes. That's on purpose: `known_by`
  decides which bars are visible. In replay, the hash depends only on as_of
  (and the config and the stored bars).
- **Size.** The default snapshot (240 × 1m, 192 × 15m, ≤ 120 × 1h, ≤ 120 × 4h)
  is about 69 KB of canonical JSON on the synthetic store. It builds in about
  0.14 s.
- **Test data.** `tests/agents/conftest.py` builds a synthetic replay store
  through the datafeed's own path: parquet → `ReplaySource` → `InMemoryBus` →
  `Recorder` → SQLite. That's Mon–Wed 2025-06-09..11, with the daily break and
  ~3 % missing minutes.
- **Tests:** `test_snapshot.py`: 20 passed (43 in tests/agents).

### Step 4: graph

`agents/graph.py`, `agents/trigger.py`, `agents/voters/`, `agents/nodes/`,
`agents/store/` (interface + `MemoryStore`), `AgentsConfig` / `GateConfig` in
`agents/config.py`.

- **Wiring** as in section 0.4. Node names are `trigger`, `snapshot`, `gate`,
  `visual`, `technical`, `sentiment`, `aggregator`, (`debate`), `risk` and
  `store`.
  - LangGraph doesn't allow a node and a state key with the same name, so the
    state keys are `event`, `snap` and `debate_result`, not trigger, snapshot
    and debate.
- **Fan-out and fan-in.**
  - The gate routes either to `store` (no-go) or to the list of voter nodes,
    which run in one superstep. A test makes three voters wait on one
    `threading.Barrier(3)`; it passes, so they do run at the same time.
  - `add_edge([voters], "aggregator")` waits for all of them.
- **Blindness.**
  - Each voter is called with exactly one argument, its own deep copy of the
    Snapshot. A test checks the call signature. Another test shows that a voter
    which mutates its copy doesn't change what the others see, or the stored
    snapshot.
  - Votes go into a reducer (`merge_disjoint`) that refuses duplicate keys.
- **`safe_vote`.** Each of these becomes an abstain with the reason, and the
  graph still finishes:
  - an exception (`error: RuntimeError: boom`);
  - a timeout (`timeout: no result after 0.2s`);
  - None, a wrong type, a vote naming another agent, round ≠ 1;
  - anything failing **strict** Vote validation. Strict mode means
    `"0.9"`-as-string or `"LONG"` is refused, not coerced.
- **Timeouts** run the agent in a daemon thread. Python can't kill a thread, so
  a timed-out agent keeps running in the background, but its result is
  discarded and it can't block exit. Default `node_timeout_s` is 120.
- **Gate v0:** no-go if `_market_open(as_of)` is false (datafeed's copy of the
  scanner rule), or if a timeframe has fewer than `min_bars` bars (default 1
  each, a placeholder).
- **Aggregator v0** (Q2 default): a direction wins with a strict majority of
  **all** voters (abstains and "none" count against); confidence is ignored.
  With the three stubs, the blind decision is `none`, with the reason
  `long 0, short 0, none 0, abstain 3 of 3: no strict majority`.
- **Risk:** a pass-through. Post-risk decisions go in `final_decisions`.
- **Store node.**
  - Writes a `RunRecord`: trigger, as_of, status done/skipped, skip_reason,
    snapshot_hash, canonical config JSON + hash, code version.
  - Also writes the votes (sorted by agent), the decisions (blind, debate) and
    the DebateResult, through `AgentStore.save_run`.
  - A stale run writes only its run row; there's no snapshot. A gated-out run
    has its snapshot (saved before the gate) and no votes.
- **Crash-only outside the voters.** A sealed t raises `SealedError` out of
  `run_once`, as do store and snapshot errors.
- **Trigger.** A `Trigger` protocol (`events()`) and `drive(trigger, graph)`.
  No clock or event producer is implemented.
- **Deviation: test directory renamed `tests/agents/` → `tests/agents_tests/`.**
  - What happened: `tests/conftest.py` makes pytest put `tests/` on `sys.path`,
    so `tests/agents/` (no `__init__.py`) was imported as a *namespace package*
    `agents`. Its submodules still loaded through the editable-install finder,
    so step 2 and 3 tests passed by luck. `from agents import __version__`
    failed.
  - Fix: the directory name no longer collides. Helper modules have unique
    names (`agents_testkit`, `agents_fakes`), because all test directories
    share one module namespace: `from conftest import …` was fragile too.
  - `test_agents_package.py` asserts that `agents.__file__` is the repo-root
    package.
  - Current layout: since the cleanup merge (2b5d479), `tests/conftest.py`
    is `legacy/tests_agents/conftest.py`, so pytest no longer puts `tests/`
    on `sys.path`. The rename stays as a safeguard, so the shadow can't
    return if a `tests/conftest.py` is added again.
- **Boundary tests** (`test_agents_package.py`):
  - `agents/` imports nothing from backend, legacy, services, ib_insync, redis,
    psycopg2 or any LLM SDK;
  - from datafeed, it imports only `datafeed.store.query` and
    `datafeed.ingest.xauusd_bars` (`SEAL_START`, `_market_open`);
  - after importing the graph, no LLM client module is loaded.
- **Tests:** 77 passed in tests/agents_tests (graph 30, package 4, schemas 23,
  snapshot 20).
- **Not reproduced.** Once, a run printed its pytest summary (77 passed in
  12 s), but the process took over 3 minutes to exit. Two reruns of the full
  suite exited in ~12 s, and the cause wasn't found. If it comes back, the
  first suspect is the daemon thread a timed-out voter leaves behind.

### Step 5: debate subgraph

`agents/debate/` (`debaters.py`, `evidence.py`, `judges.py`, `subgraph.py`),
`agents/prompts/bull.md` and `bear.md`, and `DebateConfig` (`enabled=False`,
`max_rounds=2`, `judge="code_v0"`, `turn_timeout_s=60`).

- **Subgraph.** `START → bull | bear → next_round → (bull | bear again, or
  judge) → END`.
  - Rounds are simultaneous (Q3 default). The bull/bear join waits for both
    sides every round; tested for 1, 2 and 3 rounds.
  - The recursion limit is `2 × max_rounds + 5`, and `max_rounds` is capped at
    10.
- **Inputs.** The run graph's `debate` node passes the snapshot, the round-1
  votes, and the blind decision's direction. Only the judge gets the
  direction. Nothing else from the run state goes in.
- **DebaterView** has exactly `role`, `round`, `snapshot` (a deep copy),
  `round1_votes` and `opponent_previous_turn` (None in round 1). A test checks
  the field set, that each debater is called with exactly one argument, and
  that the opponent turn is the opponent's r−1 turn.
- **Found and fixed:** the view now carries content only. A test with a real
  clock showed that round-2 views carried the opponent's `latency_ms`. The view
  hash (`prompt_hash`) and a future rendered prompt therefore changed with
  timing. `content_only()` now blanks `model_id`, `prompt_hash`, tokens and
  latency on the votes and on the opponent turn the debater sees. The stored
  turns keep their own measurements.
- **Evidence check.**
  - Implemented in code, right after each turn. It uses the grammar in
    section 0.5 and accepts `Z` or `+00:00`; naive timestamps are invalid.
  - For `bars:` refs, both ends must be bar open times present in that
    timeframe's series.
  - Flags a debater sets itself are dropped, so a debater can't self-certify.
  - Invalid claims stay in the `DebateResult` with an `invalid_reason`. The
    judge gets a `JudgeInput` whose turns hold **only valid claims**. That's
    enforced for any judge, not left to the judge's own code.
- **`safe_turn`.** These all become an empty turn with `error`, and the debate
  continues:
  - an exception or a timeout;
  - None, a wrong type, a wrong role/round, non-list claims, a claim without
    `evidence_ref`;
  - a debater-reported `error`.
- **`prompt_hash`** is the view hash unless the debater sets its own (an LLM
  debater would set the hash of the rendered prompt).
- **Judges.**
  - `JUDGES = {"code_v0": CodeJudgeV0}`. `make_judge(name)` runs in
    `build_graph` even when the debate is disabled, so an unknown name fails at
    build time.
  - `safe_judge`: an exception or an invalid verdict → `hold`, with the
    reason.
  - The LLM judge slot is a registry entry, not implemented.
- **CodeJudgeV0** works as specified:
  - a tie → `hold` (`tie: bull 2, bear 2 valid claims`);
  - more valid claims and agreeing with the round-1 direction → that
    direction;
  - otherwise → `hold`.
  - Concessions are recorded on the turn but not used by v0.
- **Graph.**
  - `debate.enabled=False`: no `debate` node is built, and the debaters are
    never called, even if passed.
  - `enabled=True` with no debaters: `build_graph` raises (no real debaters
    ship).
  - Otherwise the `debate` Decision is added under arm `debate`, with
    `decided_by` = the judge name and hold mapped to `none`.
- **Fakes** (tests only, `tests/agents_tests/agents_fakes.py`).
  `FakeBull` / `FakeBear` are deterministic: they make n claims citing the
  newest 1m bars, plus m claims with refs that don't resolve. Their text
  depends on the view. Also raising, slow, returns-anything and recording
  debaters, and spy / raising / returns judges.
- **Prompts.** `agents/prompts/bull.md` and `bear.md` aren't called anywhere.
  They require an `evidence_ref` from the grammar on every claim, say that
  invalid claims are discarded, allow conceding, forbid using remembered
  post-`as_of` knowledge, and define the JSON output. Placeholders come from
  the DebaterView only.
- **Tests:** test_debate.py: 53 passed. tests/agents_tests: 130 passed.

### Step 6: agent store and config

`agents/store/sqlite.py` (`SqliteAgentStore`) and `agents/config.example.toml`.

- **Its own SQLite.** The path comes from `$AGENTS_DB` (or `path=`). WAL,
  `synchronous=NORMAL`, `foreign_keys=ON`, numbered migrations in
  `MIGRATIONS` (one so far), and a `schema_version` table. It has no `bars`
  table. A test checks that the datafeed store file is byte-identical after a
  run.
- **Tables:**

  | Table | Key | Content |
  |---|---|---|
  | `snapshots` | snapshot_hash | symbol, as_of, kinds, known_by, newest_bar_ts, canonical `content_json`, model columns |
  | `runs` | run_id | trigger source/id/t/payload, as_of, status, skip_reason (set exactly when skipped), snapshot_hash (FK, null when stale), config JSON + hash, code_version |
  | `votes` | (run_id, round, agent) | Vote fields, `round` (1 = blind), model columns |
  | `debate_turns` | (run_id, round, role) | `claims_json` (each claim with `valid` / `invalid_reason`), n_claims, n_valid, conceded, error, model columns |
  | `decisions` | (run_id, arm) | arm blind/debate, snapshot_hash (FK), direction, reason, decided_by, entry/sl/tp/size/sigma (null), model columns, plus `debate_outcome` (long/short/hold) and `debate_max_rounds` on the debate row only |

  `model_id`, `prompt_hash`, `tokens_in`, `tokens_out` and `latency_ms` are on
  snapshots, votes, debate_turns and decisions ("all", as specified). On
  snapshots they stay null, because code builds snapshots.
- **Append-only.** `BEFORE UPDATE` / `BEFORE DELETE` triggers on all five
  tables abort with "<table> is append-only".
- **Atomic.** A run is written once, in one `BEGIN IMMEDIATE` transaction. A
  repeated run_id, a key violation or an FK violation rolls the whole run back,
  and a test shows that no partial rows remain.
- **Snapshots.** A snapshot is written in its own transaction, before any agent
  runs. The same hash with the same content is a no-op. The same hash with
  different content raises `StoreConflict`.
- **Thread safety.** Writes are serialised with a lock, because graph nodes can
  run on worker threads.
- **Reads.**
  - `load_snapshot(hash)` re-validates, so a tampered row fails on load.
  - `load_run(run_id)` rebuilds RunRecord, Votes, Decisions and the full
    DebateResult. A round-trip test compares them with the objects the graph
    produced.
- **Config.** `AgentsConfig.from_toml(path)`. `agents/config.example.toml`
  equals the defaults (tested): `debate.enabled = false`,
  `debate.max_rounds = 2`, `debate.judge = "code_v0"`. Unknown keys are
  refused, so a typo like `enabeld` raises instead of being ignored.
- **Tests:** test_agent_store.py: 19 passed. tests/agents_tests: 149 passed.

### Step 7: acceptance tests on replay data

`tests/agents_tests/test_acceptance.py` runs each item through the full graph
and the SQLite agent store. Assertions read the stored rows and reload
snapshots from the store; they don't use the in-memory objects.

| # | Requirement | Test | Result |
|---|---|---|---|
| A1 | a snapshot never contains a bar ≥ t | triggers every 37 min 13 s, off the minute, across the synthetic data and 2 h past its end; every stored snapshot reloaded and checked | pass |
| A2 | same inputs → same snapshot hash | two agent stores, two datafeed stores rebuilt from the same parquet, t 28 s apart in the same minute → equal hash and byte-equal `content_json`; next minute → different hash | pass |
| A3 | a node that raises → abstain, graph finishes | technical voter raises → stored vote `abstain=1, direction none, confidence 0, "error: RuntimeError: boom"`; run `done`; blind decision stored | pass |
| A4 | debate disabled → only blind | decisions rows = [blind], no debate_turns, debaters never called | pass |
| A5 | debate enabled → two decisions on one snapshot; round-1 rows unchanged | blind + debate rows with the run's snapshot_hash; vote rows and blind decision row identical to a debate-disabled run on the same snapshot | pass |
| A6 | nonexistent evidence_ref → invalid and ignored | bull 6 claims/round (5 unsupported) vs bear 2 supported: 10 bull claims stored with `valid:false` + reason; the judge saw only valid claims; bear wins 4–2 | pass |
| A7 | tie → hold; debater error → debate finishes | 4–4 → `hold`, direction none, reason `tie: bull 4, bear 4 valid claims`; raising bull → 2 empty bull turns with the error, 4 turns stored, run done | pass |
| A8 | a debater sees nothing outside snapshot + round-1 votes + opponent's previous turn | recorded calls: one positional `DebaterView`, exactly 5 fields, snapshot == stored snapshot, votes == stored votes (content only), opponent turn == stored r−1 turn (content only), None in round 1 | pass |
| A9 | same snapshot + same fakes → identical DebateResult | two stores, two rebuilt datafeed stores; the DebateResults loaded back from SQLite are equal | pass |
| — | real parquet day | 2025-06-04..11 replayed through datafeed into a temp store; 48 triggers on 2025-06-11 (every 30 min, +17 s), debate on with fakes; 45 done, 3 skipped. 21:00 is the gate (market closed); 21:30 and 22:00 are stale; 22:00 is right after the reopen, when the newest bar is still 20:59. Every stored snapshot < t, and both arms stored | pass |

- **Full run.** `pytest tests/datafeed tests/agents_tests`: **236 passed, 7
  skipped**. That's datafeed 76 passed + 7 skipped (6 Redis, 1 manual IBKR),
  exactly the datafeed step 8 baseline, plus agents 160 passed.
- **Research suites** weren't run: this venv has no scipy (bare `pytest`
  collection errors on 9 research modules, all "No module named 'scipy'"). The
  branch doesn't change `research/` (`git diff c3d25fb..HEAD -- research` is
  empty).
- **Protected paths.** `git diff c3d25fb..HEAD -- datafeed backend deploy
  research legacy` is empty.
- **Packaging.** A wheel built from the branch contains `agents/` with the
  debate, nodes, prompts, store and voters subpackages,
  `prompts/bull.md`, `prompts/bear.md` and `config.example.toml`.
- **README.** `agents/README.md`; its usage example was run as written.

## Owner decisions (2026-10-14, after the overnight run)

One commit per decision. Q3 (simultaneous debate openings) is kept as is, with
no code change.

### D1: seal and live mode

Owner: replay keeps the seal, with no override. Live mode (t within 120 s of
the wall clock) is allowed only with an `experiment_id` that points to an
existing PREREG file under `agents/experiments/`. `experiment_id` is stored on
every snapshot and decision.

- **`agents/prereg.py`.** The PREREG path is
  `agents/experiments/<experiment_id>/PREREG.md`, one folder per experiment,
  as in the research studies.
  - The file must exist and be non-empty. An empty file is refused, a small
    addition so a placeholder can't pass.
  - Ids: `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, so no path separators and no
    leading dot.
  - Only the file on disk is checked, not whether it was committed before the
    run.
  - `agents/experiments/README.md` documents the convention. No experiment
    exists yet.
- **`check_access`** (`snapshot.py`) runs before any bar is read:
  - **replay:** `as_of > SEAL_START` raises `SealedError`, with or without an
    experiment.
  - **live:** no `experiment_id` raises `ExperimentError`; `|t − wall clock| >
    120 s` raises `LiveClockError`. The limit is inclusive and applies in both
    directions. With a valid experiment, live mode can read data after the
    seal, but only at t ≈ now, never at an arbitrary sealed t.
  - Any `experiment_id`, in either mode, must have its PREREG.
  - `build_graph` runs the same experiment checks, so a bad config fails at
    build time. The wall clock can be injected (`wall_clock=`) for tests.
- **Stored.**
  - `Snapshot.experiment_id` is part of the content and the hash. Two
    experiments therefore never share a snapshot row.
  - `Snapshot.schema_version` is 2. Version-1 snapshot JSON no longer loads,
    but none exist outside test temp dirs.
  - `Decision.experiment_id` equals the snapshot's. Both stores refuse a
    mismatch, and in SQLite the whole run rolls back.
  - Agent store **migration 2:** `ALTER TABLE snapshots/decisions ADD COLUMN
    experiment_id`, plus an index on `decisions.experiment_id`. The
    append-only triggers still apply.
- **Prompts.** The templates now say never to render `experiment_id` into a
  prompt. Agents receive the Snapshot object, and an experiment name could hint
  at a hypothesis. The experiments README asks for opaque ids (`exp-0001`).
- **Tests** (`test_live_mode.py`, 27):
  - replay past the seal fails with or without an experiment;
  - replay before the seal with an experiment stores the id on both tables;
    without one, it stores NULL;
  - live without an experiment, with a missing or empty PREREG, or with a bad
    id fails at build and at snapshot time;
  - skew of 0, ±120 s passes; ±121 s and 1 day fail;
  - a full live run after the seal (synthetic live bars, 2026-10-14) stores
    `exp-0001` on the snapshot and on both decision arms, and a history bar
    backfilled after t is invisible (`known_by = t`);
  - the decision/snapshot experiment mismatch is refused;
  - the real `agents/experiments/` folder is the default.
  - Also: `test_agent_store.py` checks that a version-1 database migrates to
    version 2.
- **Result:** tests/agents_tests 187 passed.

### D2: aggregator v0

Owner: keep it. A direction needs at least 2 agreeing NON-abstaining votes,
else `none`. Confidence is ignored. Test the abstain cases.

- **The rule is now literal:** a fixed quorum of 2 non-abstaining votes, no
  longer "a strict majority of all voters".
  - With 3 voters, the outcomes are the same in every case.
  - They differ only with more voters: abstains no longer count against.
    For example, 2 long + 3 abstain is now `long`.
  - If both long and short reach 2 (possible only with 4 or more voters), the
    result is `none`.
- **Reasons** read like `long 2, short 0, none 0, abstain 1 of 3: 2
  non-abstaining votes agree on long`, or `…: no direction has 2 agreeing
  non-abstaining votes`.
- **Tests.** 14 three-voter cases, 8 of them with abstains:
  - 2 + abstain → that direction;
  - 1 + 2 abstain → none;
  - 3 abstain → none;
  - long/short/abstain → none;
  - none + abstain cases → none.
  - Also: abstains don't count against with 5 voters, both directions at 2 →
    none, and confidence is ignored (one vote at confidence 1.0 is still
    `none`).
- **Result:** tests/agents_tests 197 passed.

### D4: 4h candles on the NY trading day

Owner: change it. 4h candles are anchored to the NY 17:00 trading-day open
(17, 21, 01, 05, 09, 13 America/New_York), DST-safe. Test across a DST change.

- **`schemas.bucket_start(ts, tf)` / `bucket_end(start, tf)`** replace the
  epoch-only arithmetic, everywhere: resampling, the Snapshot validator
  (alignment and the `complete` flag), the fetch-window start, and evidence
  refs.
  - 15m and 1h stay on the UTC epoch, which is the same as NY alignment because
    NY offsets are whole hours.
  - 4h is computed in NY wall-clock time: start = local hour −
    `(hour − 17) mod 4`. End = start + 4 h, wall clock.
- **UTC opens move with DST.** They're 21/01/05/09/13/17 UTC in summer and
  22/02/06/10/14/18 UTC in winter.
- **A bucket spanning a DST change** lasts 3 h (spring, 01:00 EST → 05:00 EDT)
  or 5 h (autumn, 01:00 EDT → 05:00 EST). Both fall early on Sunday, NY time,
  while the market is closed. The ambiguous autumn 01:00 is the first one
  (EDT), so both 01:xx hours fall in one bucket.
- **The week's first 4h bucket** is Sunday 17:00 NY. The market opens at 18:00,
  so that bucket holds at most 3 h of bars.
- **Tests** (`test_four_hour.py`, 11):
  - continuous 1m data across 2025-03-09 and 2025-11-02:
    - every open is on a NY anchor;
    - buckets tile time with no gap or overlap;
    - the only bucket that isn't 4 h is the DST one, 3 h or 5 h, with n_1m
      equal to its minutes;
    - the UTC opens switch between the summer and winter sets;
  - `bucket_start` spot cases, including the repeated 01:xx hour;
  - 15m/1h unchanged;
  - a replay snapshot Thu–Tue across 2025-11-02:
    - Friday's 13:00 EDT bucket is complete;
    - Sunday's 17:00 EST bucket (22:00 UTC) holds ≤ 180 bars;
    - no bucket over the weekend;
    - opens are summer before and winter after;
  - evidence refs: a NY-anchored open resolves, including one written as a
    `-05:00` timestamp; an old epoch-anchored open doesn't.
  - Four earlier tests had UTC 4h opens (12:00 UTC); they now expect the NY
    anchors (13:00 UTC = 09:00 EDT).
- **Result:** tests/agents_tests 208 passed.

### Staleness against the last market-open minute

Owner: measure staleness against the last market-open minute, not the wall
clock. The reopen trigger must not be skipped.

- **Rule.** `age = last_open_minute(as_of) − newest 1m bar_ts`, stale if
  `age > max_staleness` (5 min).
  - `last_open_minute` is the latest minute before `as_of` in which datafeed's
    `_market_open` is true. It steps back an hour at a time, because the closed
    spans are whole NY hours, and so whole UTC hours. The search is limited to
    9 days.
  - While the market is open this is `as_of − 1 min`, the same as before.
- **What changes.**
  - In the daily break and over the weekend, the newest bar (the one before the
    close) is fresh. The gate now gives the skip reason (`market closed`)
    instead of "stale".
  - At the 22:00 UTC (summer) reopen, and at the Sunday 18:00 NY open, the run
    goes through on the last bar before the close.
  - A few minutes after the reopen, the new bars are needed again: the last
    open minute moves forward.
  - Holidays aren't modelled (as in `_market_open`). On a holiday the feed is
    silent while the market counts as open, so runs are skipped as stale after
    5 min, which is the safe side.
- **Tests:**
  - `last_open_minute` cases: open, summer and winter break, the reopen minute,
    Saturday, the Sunday reopen;
  - the break and the reopen are not stale;
  - the feed stopping while the market is open is stale, with the 5-minute
    limit inclusive;
  - graph: the reopen trigger is `done`, and a break trigger is skipped by the
    gate;
  - weekend store: the Sunday 22:00 UTC trigger is `done` on Friday's last bar;
    Saturday is skipped by the gate.
  - Earlier tests that used the break as their "stale" example now use the
    feed ending on a weekday.
  - Real parquet day: the 22:00 reopen now runs, so 46 done and 2 skipped
    (21:00 and 21:30, both by the gate).
- **Result:** tests/agents_tests 225 passed.

### Pytest timeout and slow-exit watchdog

Owner: add a pytest timeout, and note the slow-exit case in REPORT.md if it
recurs.

- **`pytest-timeout==2.4.0`** goes in the `dev` extra, with `timeout = 120` (s
  per test) in `[tool.pytest.ini_options]`. The slowest test, a replayed
  parquet day through the graph, takes ~10 s.
  - 2.5.0 (2026-08-16) was tried first. pip warned it's **yanked** ("accidental
    breaking change"), so 2.4.0 (2025-05-05) is pinned.
  - Checked with a probe test that sleeps 10 s under `timeout=2`: it fails
    with `Timeout (>2.0s) from pytest-timeout`.
- **Slow-exit watchdog** (`tests/agents_tests/conftest.py`). A per-test
  timeout can't catch a slow exit, because that happens after pytest is done.
  `pytest_unconfigure` therefore arms
  `faulthandler.dump_traceback_later(30 s)`: if the process is still alive
  30 s after pytest finishes, every thread's stack goes to stderr. Checked
  with a probe that leaves a non-daemon thread sleeping 40 s: after 30 s the
  dump showed the main thread waiting in `threading._shutdown` and the
  sleeping thread, and the process exited at ~41 s. A normal run exits before
  the timer.
- **Result:** tests/agents_tests 225 passed in 21 s; the process exited at
  21.6 s.

### Lock file

Owner: pin all dependencies with a lock file.

- **`requirements.lock`** (repo root) is generated by pip-tools 7.6.1 with
  `pip-compile --all-extras --all-build-deps --generate-hashes --allow-unsafe
  --strip-extras --constraint requirements.cutoff.txt pyproject.toml`.
  - 56 packages, each `==` with sha256 hashes for every published file of that
    version, so other platforms are covered too.
  - It includes the build backend (setuptools 84.0.0, wheel 0.48.0), so
    `pip install --no-build-isolation -e .` fetches nothing unpinned.
- **Two-week rule extended to transitive dependencies.** The first resolve
  pulled 12 releases under two weeks old. langsmith 0.14.8 and langchain-core
  1.6.10 had come out 1–2 days earlier.
  - `requirements.cutoff.txt` caps each of the 12 at its newest release
    uploaded before 2026-09-30. For example: langchain-core 1.6.6, langsmith
    0.14.1, langgraph-checkpoint 4.2.0, langgraph-sdk 0.4.5, orjson 3.12.0.
  - After re-locking, no pin is newer than 2026-09-29, and none is yanked
    (checked against PyPI).
- **Install:** `pip install --require-hashes -r requirements.lock`, then
  `pip install --no-deps --no-build-isolation -e .`.
- **Fresh venv from the lock only:**
  - `pip check` is clean;
  - **313 passed, 7 skipped**: agents 229, plus datafeed 84 as currently in
    the working tree (see the REPORT on the uncommitted datafeed sync);
  - the process exited ~3 s after pytest finished.
- **This worktree's `.venv` is synced to the lock:** `pip freeze` equals the
  lock exactly. That moved the 12 capped packages back.
- **`test_lock.py`** checks four things:
  - every lock entry is `==` and hashed;
  - every pyproject requirement (core + all extras) is `==` and locked at that
    version;
  - setuptools and wheel are locked;
  - the cutoff caps hold.
- **Not covered:** the lock was resolved on macOS / Python 3.13.1. The scanner
  image is Linux / Python 3.12. The hashes cover Linux wheels, but no
  dependency differs by platform marker today, and that hasn't been run on
  3.12. `deploy/datafeed/Dockerfile` still installs from `pyproject.toml`;
  `deploy/` is out of scope.

### Research extra

Owner: add a `[research]` extra with the research dependencies, pinned to the
old backend venv; include them in the lock; run the full root pytest, which
must show research 80 passed with the baseline per-suite counts.

- **What research imports.** From an AST scan of the tracked `research/**/*.py`
  files: the third-party imports are numpy, pandas, scipy, matplotlib, numba
  and pytest. The rest are research's own modules. pyarrow is also needed,
  because pandas uses it for the `read_parquet` / `to_parquet` calls in
  levels, levels_confirm, vol_features and vol_features_r2/r3.
- **Pins** (from `algo_project/backend/venv`, Python 3.13.1, read with
  `pip freeze`; not modified): `numpy==2.2.6`, `pandas==2.3.3`,
  `pyarrow==25.0.0`, `scipy==1.17.1`, `matplotlib==3.10.8`, `numba==0.61.2`,
  `pytest==9.1.1`.
  - numpy, pandas, pyarrow and pytest were already pinned to these versions
    in `replay` / `dev`.
  - All are on PyPI, not yanked, at least 7 months old, and have cp313 macOS
    and cp312 manylinux wheels.
- **Lock.** Re-locked with the same command: 66 packages (+10), and no
  existing pin moved.
  - New: scipy, matplotlib, numba, llvmlite 0.44.0 (= backend venv; numba
    0.61.2 requires 0.44.x), contourpy 1.4.0, cycler 0.12.1, fonttools 4.66.1,
    kiwisolver 1.5.1, pillow 12.3.0, pyparsing 3.3.3.
  - All are at least two weeks old (no new cap needed), and none is yanked.
- **Differences from the backend venv.** Every package research imports is at
  the backend venv's version. These indirect dependencies resolved to newer
  versions:
  - matplotlib's plotting libraries: contourpy 1.4.0 vs 1.3.3, fonttools
    4.66.1 vs 4.61.1, kiwisolver 1.5.1 vs 1.4.9, pillow 12.3.0 vs 12.1.0,
    pyparsing 3.3.3 vs 3.3.1;
  - packages shared with agents/datafeed: packaging 26.3 vs 25.0, pytz 2026.4
    vs 2025.2, and others.

  Research imports none of these directly. The numerical stack (numpy,
  pandas, scipy, numba, llvmlite) is identical.
- **Data.** Research tests read untracked data. As in the baseline worktree,
  it's symlinked from the main checkout: file by file into
  `database/ohlcv_data/` and `research/vol_features_r2/cache/`. Both are
  gitignored, and `git status` stays clean.
- **`.venv`** was installed from the lock; `pip freeze` equals the lock (66
  packages) and `pip check` is clean.
- **Full root `pytest`: 393 passed, 7 skipped,** in 71 s; the process exited
  ~1 s later.
  - research: **80 passed** = levels 11, levels_confirm 8, levels_diag 4,
    regime_v2 9, trend_features 7, vol_block 5, vol_features 14,
    vol_features_r2 9, vol_features_r3 11, test_sealing 2. Identical to the
    baseline per suite, with 0 skipped and 0 failed.
  - datafeed: 84 passed, 7 skipped (the merged `feat/datafeed-v1` @
    5f285d3).
  - agents: 229 passed.

### Layout after the cleanup merge

Owner: tidy the comments to the current layout (the backend/ agent code now
lives in legacy/). Keep the guard test. Remove the backend/ path-stripping
code only if the guard test still passes without it.

- **Current layout.** `main`'s cleanup (01244b9, merged as 2b5d479) moved:
  - `backend/agents/` → `legacy/backend_v0/agents/`;
  - `tests/conftest.py` → `legacy/tests_agents/conftest.py`.

  `backend/` now holds only the scanner. `legacy/` is in `norecursedirs`.
- **Checked before removing anything.** A probe test in a full run (research +
  tests/datafeed + tests/agents_tests) printed:
  - `sys.path` (repo entries): only the venv, `research/` and its study
    directories, `tests/agents_tests` and `tests/datafeed`. No `backend/`, no
    `tests/`, no `legacy/`.
  - `agents.__file__` = `<repo>/agents/__init__.py`.
  - No module loaded from `legacy/` or `backend/`.
- **Removed:** the `backend/` path-stripping lines in
  `tests/agents_tests/conftest.py`. They had nothing left to strip.
- **Kept:**
  - the guard test, which passes on its own and in the full run;
  - the `tests/agents_tests` name and the unique helper module names.
- **Comments updated:** the docstring of `tests/agents_tests/conftest.py`; the
  layout listing and section 0.2 here (rewritten for the current layout, with
  the history kept); the step 2 and step 4 notes (annotated, not rewritten).
- **Not touched:** `tests/datafeed/conftest.py` has the same now-dead
  `backend/` stripping, and `tests/datafeed/test_package.py` asserts that no
  `backend` path is on `sys.path`. Both belong to datafeed and are outside
  this change.

