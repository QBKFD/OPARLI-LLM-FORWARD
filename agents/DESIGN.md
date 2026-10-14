# Agents skeleton: design

Design notes for the `feat/agents-skeleton` branch (from `feat/datafeed-v1` @
c3d25fb). Step 0 is the proposal below. Later steps append their decisions.

**Overnight run (2026-10-14).** The owner asked for steps 1–7 to run without a
"go" between them, following this proposal. Q1–Q4 (end of section 0) are
applied as the proposed defaults and are still open for the owner to confirm.
A question that blocks a step goes at the top of this file.

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
  conftest.py             drops backend/ from sys.path (as tests/datafeed does); replay store fixture
  agents_testkit.py       synthetic replay data through the datafeed replay path
  agents_fakes.py         FakeBull, FakeBear, raising/slow agents and debaters (tests only)
  test_*.py
```

The fake debaters live in `tests/agents_tests/agents_fakes.py`, so the package
itself contains no fake decision code.

### 0.2 Name collision: `backend/agents/`

`backend/agents/` is a package (docs/REPO_MAP.md), and `tests/conftest.py` puts
`backend/` at `sys.path[0]`. Under pytest, `import agents` could therefore load
the old package. `tests/agents/conftest.py` removes `backend/` from the path,
and a guard test asserts that `agents.__file__` is the repo-root package. The
collision goes away once `backend/` moves, but the guard stays. (Step 4 found a
second collision, with the test directory itself; see there.)

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
- **Tests:** `tests/agents/conftest.py` takes `backend/` off `sys.path`.
  `testpaths` gains `tests/agents`. `test_schemas.py`: 23 passed.

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
