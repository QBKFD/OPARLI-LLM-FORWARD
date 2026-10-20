# Current architecture

Drawn from the code on `main` @ `c556533` (2026-10-20), not from the design
docs. The open PR #22 (instrument config) isn't drawn.
`docs/ARCHITECTURE_MAP.md` and `docs/REPO_MAP.md` are surveys from before the
phase 1 cleanup and use old paths.

**What the EC2 host runs isn't recorded in git.** PR #18 (2026-10-18) states
that the host ran branch `pipeline-fixes-and-cleanup` (last commit
2026-09-29), not `main`. That branch has no journal migrations, no TAKE
scoring, no `backup.sh` and no stream mode. Everything below describes `main`,
so those parts may not be live yet. Check on the host with
`git -C ~/oparli status -sb`.

Legend used in the diagrams:

| Colour | Meaning |
|---|---|
| green | runs in the production stack (`deploy/scanner`, default profile) |
| yellow, dashed | built and deployable, opt-in (`COMPOSE_PROFILES=stream`) |
| grey | built, not deployed anywhere |
| red | stub or placeholder |
| blue | external service |

## (a) System overview

```mermaid
flowchart TB
  subgraph EXT["External"]
    IBKR["IBKR servers"]
    TG["Telegram Bot API"]
    OWNER["Owner<br/>Telegram app, VNC for weekly 2FA"]
    S3[("S3 bucket<br/>BACKUP_S3_URI")]
    GH["GitHub Actions CI<br/>tests, lint, gitleaks"]
    ANT["Anthropic API"]
  end

  subgraph EC2["EC2 host: deploy/scanner, docker compose"]
    GW["ibgateway<br/>IB Gateway + IBC"]
    SC["scanner<br/>backend/deterministic_scanner.py"]
    J[("journal<br/>/data/scanner_decisions.sqlite")]
    CRON["host cron<br/>watchdog.sh, backup.sh"]
    HJ[("~/scanner-health.json")]
    STB["tools/status<br/>--once or the Textual board"]
    subgraph STREAM["profile stream (opt-in)"]
      FEED["feed<br/>python -m datafeed.ingest"]
      R[("redis<br/>stream bars.xauusd.1m")]
      REC["recorder<br/>python -m datafeed.recorder"]
      DS[("datafeed store<br/>/datafeed/datafeed.sqlite")]
    end
  end

  subgraph DEV["Developer machine"]
    DATA[("database/ohlcv_data<br/>XAUUSD.txt, clean parquet<br/>untracked")]
    RES["research/<br/>pre-registered studies"]
    MODEL["datafeed/features/models<br/>HAR sigma JSON, SHA-256 pinned"]
    LIB["scoring/, risk/, datafeed.features<br/>libraries"]
    AG["agents/<br/>LangGraph graph, LLM layer<br/>no runner, prompts are templates"]
  end

  IBKR <--> GW
  GW -->|"ibkr mode: ticks + 10-day history"| SC
  GW -->|"stream mode: ticks + 10-day history"| FEED
  FEED --> R --> REC --> DS
  R -->|"consumer group scanner"| SC
  DS -->|"warm-up, read-only"| SC
  SC --> J
  SC <-->|"alerts out, replies in"| TG
  TG <--> OWNER
  CRON -->|"alerts, daily heartbeat"| TG
  CRON -->|"journal, store, parquet archive"| S3
  CRON -->|"health facts"| HJ --> STB
  DATA --> RES -->|"fit_final.py"| MODEL --> LIB
  LIB -->|"scoring.score for TAKE outcomes"| SC
  DS -.->|"bars_before, nothing deployed"| AG
  AG -.->|"client built, nothing calls it"| ANT

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  classDef optin fill:#fff3cd,stroke:#b8860b,stroke-dasharray:5 5,color:#000
  classDef idle fill:#e2e3e5,stroke:#6c757d,color:#000
  classDef ext fill:#dbe9f6,stroke:#1f5f99,color:#000
  class GW,SC,J,CRON,HJ,STB prod
  class FEED,R,REC,DS optin
  class DATA,RES,MODEL,LIB,AG idle
  class IBKR,TG,OWNER,S3,GH,ANT ext
```

- The scanner is the only production service. It never trades and never
  calls an LLM.
- In `ibkr` mode (the default and the rollback), the scanner is a gateway
  client itself. In `stream` mode the feed is the gateway's only bar client,
  and the scanner reads Redis and the store.
- `scoring/` (and `risk/` through it) runs inside the scanner image. The rest
  of the developer-machine box isn't deployed.
- The status board reads only `health.json`, which the watchdog writes every
  run. `python3 -m tools.status --once` needs no packages; the Textual board
  needs the `status` extra.
- Nothing calls the Anthropic API. Outside the tests, only LangGraph Studio
  (`tools/langgraph_studio/studio.py`, a local replay-only debugging tool)
  builds the agents graph, and it passes no LLM client. The only command
  that makes a client is `python -m agents.llm count-tokens`.

## (b) Live data path, with files

```mermaid
flowchart TB
  GW["IB Gateway container<br/>socat relay 4004 paper, 4003 live"]

  subgraph IBKRMODE["SCANNER_BAR_SOURCE=ibkr (default)"]
    H1["_load_recent_history()<br/>backend/deterministic_scanner.py<br/>clientId 21: 10 D of 1-min MIDPOINT bars"]
    T1["TWSConnector<br/>backend/services/tws_connector.py<br/>clientId 11: reqMktData ticks to 1-min bars"]
    MEM["hist DataFrame<br/>in memory only, lost on restart"]
  end

  subgraph STREAMMODE["SCANNER_BAR_SOURCE=stream (profile stream)"]
    F["IbkrXauusdSource + BarBuilder<br/>datafeed/ingest/xauusd_bars.py<br/>datafeed/ingest/ibkr.py, clientId 31<br/>live bars also carry bid/ask spread stats"]
    B[("Redis stream bars.xauusd.1m<br/>datafeed/bus.py, XADD MAXLEN about 100k")]
    REC["Recorder, group recorder<br/>datafeed/recorder/recorder.py"]
    ST[("datafeed.sqlite<br/>bars + spread columns, bar_conflicts, rejected_messages<br/>datafeed/store/schema.py, migration 4")]
    Q["bars_before(symbol, t, lookback)<br/>datafeed/store/query.py"]
    SF["StreamFeed, group scanner<br/>deterministic_scanner.py"]
  end

  LL["LiveLevels then Scanner<br/>deterministic_scanner.py"]
  ARC[("S3 archive/bars/date=YYYY-MM-DD/XAUUSD.parquet")]
  PQ[("database/ohlcv_data/<br/>XAUUSD_1min_UTC_clean.parquet")]

  GW --> H1 --> MEM --> LL
  GW --> T1 -->|"final bars that pass _valid_bar"| LL
  GW --> F -->|"history=True warm-up, then live bars"| B
  B --> REC -->|"one transaction per batch, then ack"| ST
  ST --> Q -->|"stream_warmup: 10 days, 80 pct coverage, read-only"| LL
  B --> SF -->|"live bars drive alerts, history bars only fill gaps"| LL
  ST -.->|"export_parquet.py, daily via backup.sh"| ARC
  PQ -.->|"ReplaySource, local runs and tests only"| B

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  classDef optin fill:#fff3cd,stroke:#b8860b,stroke-dasharray:5 5,color:#000
  classDef idle fill:#e2e3e5,stroke:#6c757d,color:#000
  class GW,H1,T1,MEM,LL prod
  class F,B,REC,ST,Q,SF,ARC optin
  class PQ idle
```

- **Bar contract.** `bar_ts` is the minute's open time in UTC. The price is
  the bid/ask midpoint, and a minute without ticks has no bar.
  `datafeed/ingest/xauusd_bars.py` lists five approved differences from the
  connector's bar builder. `tests/datafeed/test_xauusd_bars.py` pins the two
  builders together.
- **As-of rule.** `bars_before(t)` returns bars with `bar_ts + 1 min <= t`.
  With `known_by` it also requires `ingest_ts <= known_by`.
- **Spread (migration 4).** Live bars carry `spread_mean`, `spread_median`,
  `spread_max` (ask − bid, as quoted) and `n_quotes`, all four or none. They
  are NULL for history and replay bars, stay out of the duplicate/conflict
  comparison, and `bars_before` doesn't return them. They're read with
  `query.spreads()` or `tools/spread_by_hour.py` (read-only).
- **Gateway client ids:** 11 and 21 (scanner, ibkr mode), 31 (feed), 996
  (watchdog probe), 999 (entrypoint wait).
- **Replay** bars are kind `replay` in the store. Readers get them only by
  asking for `kinds=("replay",)`. A replay window can't end after
  2026-10-01, or run past the parquet's coverage.

## (c) Scanner and journal

```mermaid
flowchart LR
  BARS["1-min bars<br/>ibkr or stream path"] --> LL["LiveLevels<br/>rebuilds at UTC midnight and each session close"]
  LL --> BL["build_levels()<br/>12 level types, active from the source period's close"]
  LL --> SCN["Scanner.on_bar()<br/>first touch only, 1 USD tolerance, 15-min bucket"]
  SCN -->|"bucket flushed by the next bucket's first bar"| SINK["AlertSink"]
  SINK -->|"status sent"| A[("alerts")]
  SINK -->|"missed_while_down, missed_history"| A
  SINK -->|"format_alert()"| TGS["Telegram sendMessage"]
  SINK -->|"progress(): last_live_bar_ts"| STATE[("scanner_state")]
  TGP["Telegram getUpdates<br/>long poll 60 s"] --> HR["_handle_reply()<br/>parse_reply()"]
  HR -->|"message date = decision time"| D[("decisions")]
  HR -->|"telegram_offset"| STATE
  EP["start_epoch()<br/>new epoch on every bar-source change"] --> SW[("source_switches")]

  subgraph SWEEPS["every 5 min, main loop"]
    M["sweep_auto_missed<br/>no reply within 4 h"]
    CF["sweep_counterfactuals<br/>1h and 4h MFE/MAE from the touch"]
    OUT["sweep_outcomes<br/>scoring.outcome.score, 240 min"]
  end
  M --> D
  CF --> C[("counterfactuals")]
  OUT --> O[("outcomes")]

  A --> SUM["backend/journal_summary.py<br/>read-only, per epoch"]
  D --> SUM
  O --> SUM

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  class BARS,LL,BL,SCN,SINK,A,TGS,STATE,TGP,HR,D,EP,SW,M,CF,OUT,C,O,SUM prod
```

```mermaid
erDiagram
  alerts ||--o{ decisions : "alert_id and epoch"
  alerts ||--o| counterfactuals : "alert_id and epoch"
  decisions ||--o| outcomes : "decision_rowid, UNIQUE"
  source_switches ||--o{ alerts : "epoch"
  alerts {
    text alert_id "A, UTC day, per-day sequence"
    text alert_ts_utc "earliest touch, not send time"
    text level_types "comma-separated"
    real level_price
    real price_at_alert
    integer confluence_count
    text inserted_ts_utc "when journaled"
    integer epoch
    text bar_source "ibkr, stream, parquet"
    text status "sent, missed_while_down, missed_history"
  }
  decisions {
    text alert_id
    integer epoch
    text decision "TAKE, SKIP, MISSED"
    text decision_ts_utc "Telegram message date"
    text direction
    real entry
    real stop
    real target
    integer telegram_update_id UK
    text received_ts_utc
  }
  outcomes {
    text alert_id
    integer epoch
    integer decision_rowid UK
    text exit_type "tp, sl, timeout, unscorable"
    real r_multiple
    text entry_source "reply or market_open"
  }
  counterfactuals {
    text alert_id
    integer epoch
    real mfe_up_1h
    real mfe_dn_1h
    real mfe_up_4h
    real mfe_dn_4h
  }
  source_switches {
    text switch_ts_utc
    text from_source
    text to_source
    integer epoch
  }
  scanner_state {
    text key PK "last_live_bar_ts, telegram_offset"
    text value
  }
```

- **Schema version.** `PRAGMA user_version` is 4, and the journal migrates on
  first open (`_migrate_journal`). An image from before migration 1 can't
  write to a migrated journal.
- **Epochs.** Joins use `(alert_id, epoch)`, because alert ids repeat across
  epochs on a switch day. Analyses never pool epochs.
- **First decision counts.** Only the first decision per alert and epoch is
  scored and counted. Later replies are journaled as revisions.
- **Delivery delay.** An alert is sent when the first bar of the next 15-minute
  bucket arrives, so `alert_ts_utc` is earlier than the send time. Replay of
  2025: median 11 min, 7 of 1,706 alerts over an hour (the daily break), one
  across a weekend.
- **Not shown above:** the selftest (`--selftest`) and the `--date` replay
  (`replay_day`). `replay_day` starts each day with a fresh `Scanner`, so it
  can differ from a continuous run (`tests/scanner/test_parity.py`).

## (d) Agents (v2)

**Nothing deployed runs the agents graph.** There is no runner, no trigger
implementation and no deployed entry point. Outside the tests, the only caller
of `build_graph` is LangGraph Studio (`tools/langgraph_studio/`,
`docs/LANGGRAPH_STUDIO.md`): a laptop tool, replay data only, an in-memory
agent store and no LLM client. Every prompt in `agents/prompts/` is still the
unfilled template (`version: TEMPLATE`), so even with an LLM client every LLM
agent would abstain `prompt_invalid`. The status board reports agents as OFF.

Legend for (d1) and (d4):

| Style | Meaning |
|---|---|
| grey, thick border | built: real code, tested, runs on every graph run |
| red | stub or placeholder: fixed answer, or a placeholder rule |
| white, dashed | not running: built, but nothing in the default graph reaches it |
| black, dotted | not built |

### (d1) The run graph

`agents/graph.py` `build_graph`. Each node shows what it is in the default
graph, i.e. without an `llm_client`, which is how every caller on `main`
(the tests and Studio) builds it.

```mermaid
flowchart TB
  S0(("start")) --> TR["trigger<br/>run_id, as_of = floor(t, 1 min)"]
  TR --> SN["snapshot · agents/snapshot.py<br/>bars_before(as_of), seal, live-mode rules, staleness<br/>σ features and metric table built<br/>levels, events, charts: no builder, marked unavailable<br/>hashed and saved before any agent runs"]
  SN -->|"stale"| STO
  SN --> GT["gate · agents/nodes/gate.py<br/>market open, minimum bars per timeframe<br/>v0 placeholder minimums"]
  GT -->|"no-go"| STO

  subgraph R1["round 1: blind, one superstep, each voter gets a copy of the snapshot"]
    VI["visual<br/>VisualStub: abstains"]
    TE["technical<br/>TechnicalStub: abstains"]
    SE["sentiment<br/>SentimentStub: abstains"]
    GX["gex · code agent<br/>abstains: no options data source"]
  end
  LLMV["LLMVoteAgent for visual, technical, sentiment<br/>only with build_graph(llm_client=...)<br/>agents/llm/agent.py, see d2"]

  GT --> VI & TE & SE & GX
  LLMV -.->|"replaces the three stubs"| R1
  VI & TE & SE & GX --> MB["meta_blind · agents/meta<br/>the owner's [meta] rule on the round-1 votes<br/>quorum_v0 or fixed_meta_v1; no rule: none with the reason<br/>Decision arm blind"]
  MB --> DI["discussion · agents/discussion<br/>LLM agents whose round-1 vote didn't abstain<br/>N rounds, early stop, change rule in d3<br/>default graph: skipped, no LLM discussants"]
  DI --> MD["meta_discussion<br/>the same rule on the final votes<br/>Decision arm discussion"]
  MD --> RK["risk · agents/nodes/risk.py<br/>apply_risk: pass-through"]
  RK --> STO["store · SqliteAgentStore at AGENTS_DB<br/>append-only, one transaction per run"]
  STO --> E0(("end"))

  classDef built fill:#e2e3e5,stroke:#2e7d32,stroke-width:2px,color:#000
  classDef stub fill:#f8d7da,stroke:#b02a37,color:#000
  classDef notrun fill:#ffffff,stroke:#6c757d,stroke-dasharray:5 5,color:#000
  class TR,SN,MB,MD,STO built
  class GT,VI,TE,SE,GX,RK stub
  class DI,LLMV notrun
```

| Node | State on `main` |
|---|---|
| trigger | built. The `Trigger` protocol and `drive()` exist; no trigger does |
| snapshot | built: `as_of` floor, replay refused after 2026-10-01, live mode needs an experiment PREREG and a wall clock within 120 s. HAR σ (60/240) and L, S_hd, T from `agents/features.py`, with the σ model named in `snapshot.sigma_model` (SHA-256 checked). The metric table from `agents/technical/metrics.py`. Levels, events and charts have no builder |
| gate | real checks with v0 placeholder minimums (one bar per timeframe) |
| visual, technical, sentiment | stubs that abstain. With an `llm_client` they are LLM agents (d2), but nothing passes one |
| gex | code agent that always abstains (`no options data source`) |
| meta_blind, meta_discussion | built: `quorum_v0` and `fixed_meta_v1`. The owner picks one in `agents.toml`; there's no default rule. Rule name and version are on every decision |
| discussion | built and tested with fake discussants; never reached in the default graph, which has no discussants |
| risk | pass-through stub; entry, SL, TP, size and σ stay null |
| store | built: SQLite, append-only (UPDATE and DELETE abort), one transaction per run, migration 5 |
| technical_v1 | not built; see d4 |

Every decision is stamped with the epoch (`agents/epoch.py`): a SHA-256 over
the prompt files, the owner's `agents.toml`, the per-agent call settings, the
output schema, the input-building code, the Meta rule and the σ model. Any
change gives a new epoch automatically.

### (d2) One LLM agent's call path

`LLMVoteAgent.vote()` (`agents/llm/agent.py`) for visual, technical or
sentiment. Not running: nothing builds the graph with a client, and the
prompts are templates. Every exit on the right is an abstain with that reason;
nothing raises. The graph's `safe_vote` also bounds the whole call by
`node_timeout_s`.

```mermaid
flowchart TB
  PF[("agents/prompts/&lt;agent&gt;.md<br/>owner-written, SHA-256 over raw bytes<br/>all version TEMPLATE on main")]
  OC[("agents.toml at $AGENTS_CONFIG<br/>owner_config.py: agents.&lt;name&gt;.round1, llm, budget, render")]
  SNAP["Snapshot"]

  PF --> LP["prompting/files.py load_prompt"]
  LP -->|"missing, malformed or TEMPLATE"| X1["prompt_invalid: …"]
  OC --> SET["agent._settings<br/>call settings, llm, budget"]
  LP --> SET
  SET -->|"section invalid"| X2["config_invalid: …"]
  SET --> RD["prompting/render.py render_round1<br/>placeholders from the snapshot<br/>an unavailable slot renders as unavailable"]
  SNAP --> RD
  RD -->|"RenderProblem"| X3["the render problem"]
  RD --> RQ["llm/request.py round1_request<br/>model, effort, max_tokens, vote output schema<br/>no temperature, top_p, top_k or thinking<br/>prompt_hash = the cache key"]

  subgraph CALL["llm/call.py run_call"]
    CH{"llm_responses has<br/>prompt_hash?"}
    MODE{"llm.mode"}
    KS{"kill switch<br/>file or AGENTS_LLM_KILL=1"}
    BU{"budget.reserve<br/>max_calls_per_day, under a lock"}
    AT["AnthropicClient.complete<br/>official SDK, max_retries = 0, timeout_s"]
    PUT["ledger.put_response<br/>stored before use"]
  end
  RQ --> CH
  CH -->|"hit: reuse, no call, cached = 1"| OUT
  CH -->|"miss"| MODE
  MODE -->|"rerun"| X4["cache_miss"]
  MODE -->|"live"| KS
  KS -->|"on"| X5["kill_switch: …"]
  KS -->|"off"| BU
  KS -->|"off, but chart bytes missing"| X12["charts: …"]
  BU -->|"day's cap reached"| X6["budget: …"]
  BU --> AT
  AT -->|"timeout, connection, 408, 409, 429, 5xx<br/>backoff 0.5 to 8 s, up to 1 + max_retries attempts"| BU
  AT -->|"other error, or attempts used up"| X7["api_error, timeout …"]
  AT -->|"response"| PUT --> OUT["response"]
  AT -.->|"a row per attempt; also per cache hit and rerun miss"| LED[("ledger: llm_calls<br/>request id, tokens, latency, list-price cost")]

  OUT --> CK{"agent.py checks"}
  CK -->|"served model differs from requested"| X8["model_mismatch: …"]
  CK -->|"stop_reason refusal"| X9["refusal:category"]
  CK -->|"stop_reason max_tokens"| X10["truncated: …"]
  CK -->|"any other stop_reason"| X11
  CK -->|"end_turn"| PA["llm/parse.py parse_answer<br/>fields, confidence in 0..1, text lengths, at most 6 evidence items<br/>every ref checked against the snapshot (check_ref)<br/>long or short needs a ref that resolves"]
  PA -->|"any problem"| X11["invalid: …"]
  PA --> VOTE["Vote<br/>direction, confidence, reason, evidence<br/>model_id, tokens, prompt_hash, call_id"]

  classDef notrun fill:#ffffff,stroke:#6c757d,stroke-dasharray:5 5,color:#000
  classDef abst fill:#f8d7da,stroke:#b02a37,color:#000
  classDef store fill:#e2e3e5,stroke:#6c757d,color:#000
  class LP,SET,RD,RQ,CH,MODE,KS,BU,AT,PUT,OUT,CK,PA,VOTE,SNAP notrun
  class X1,X2,X3,X4,X5,X6,X7,X8,X9,X10,X11,X12 abst
  class PF,OC,LED store
```

- **The cache** (`llm_responses`) and **the ledger** (`llm_calls`) live in
  the agent store (migration 4). A cached response is the stored copy, so a
  rerun reproduces it byte for byte.
- **The budget** counts sent attempts per UTC day, not cache hits.
- **A discussion call** (`discuss()`) is the same path with the agent's
  `discussion` call settings: its round-1 content again, then
  `discussion.md`. Any problem there becomes the change rule's
  `call failed: …` (d3), not an abstain.

### (d3) The discussion vote-change rule

`agents/discussion/rule.py` `apply_change_rule(previous, proposal, round)`,
applied to every discussant's proposal in every discussion round. Pure code,
tested; not running on `main` (see d1).

```mermaid
flowchart TB
  P["proposal for vote round r = 2, 3, …<br/>from discuss() through safe_discuss<br/>an exception or timeout becomes an abstain"]
  P --> Q1{"proposal abstained?"}
  Q1 -->|"yes"| CF["previous vote carried forward<br/>change_rejected = call failed: reason<br/>never an abstain mid-discussion"]
  Q1 -->|"no"| Q2{"same direction<br/>and confidence?"}
  Q2 -->|"yes"| KEEP["accepted, kept<br/>new reason and evidence stored<br/>changed = False, no new evidence needed"]
  Q2 -->|"no"| Q3{"cites a ref that resolves in the snapshot,<br/>isn't vote:, and wasn't in the<br/>previous vote's evidence?"}
  Q3 -->|"yes"| ACC["accepted change<br/>changed = True"]
  Q3 -->|"no"| REJ["refused: previous vote carried forward<br/>change_rejected = only vote: refs (a vote: ref was cited)<br/>or no new snapshot ref<br/>rejected_proposal = RejectedChange, the refused vote"]
  KEEP --> NEXT{"after every speaker:<br/>rounds used up? or early_stop on<br/>and no vote with changed = True?"}
  ACC --> NEXT
  CF --> NEXT
  REJ --> NEXT
  NEXT -->|"yes"| END1["discussion ends<br/>final votes go to meta_discussion"]
  NEXT -->|"no"| P

  classDef ok fill:#d4edda,stroke:#2e7d32,color:#000
  classDef no fill:#f8d7da,stroke:#b02a37,color:#000
  class KEEP,ACC ok
  class CF,REJ no
```

- **Who speaks:** discussants (agents with a `discuss` method, the LLM agents)
  whose round-1 vote didn't abstain. GEX, the stubs and abstainers keep their
  round-1 vote, which the speakers see.
- **Simultaneous rounds, content only:** in round r every speaker sees the
  others' latest votes from before the round, without model ids, prompt
  hashes, tokens or latency.
- **Skip reasons**, the most basic first: no LLM discussants in the graph,
  an invalid `[discussion]` config, an invalid `discussion.md`, every
  discussant abstained in round 1. A skipped discussion is still a
  `discussions` row (`rounds_run = 0`), and both decisions are stored.
- Every carried or refused vote keeps that round's call provenance (model,
  prompt hash, tokens, call id).

### (d4) Where Technical v1 will sit

The deterministic Technical agent of agents v1 (`research/agents_v1/PREREG.md`,
DRAFT) is planned as its own arm, outside Meta. On `main` only its slots
exist.

```mermaid
flowchart TB
  SN["snapshot<br/>metric table built<br/>agents/technical/metrics.py"] --> GT["gate"]
  GT --> R1["round 1<br/>visual, technical, sentiment, gex"] --> MB["meta_blind<br/>arm blind"] --> DI["discussion"] --> MD["meta_discussion<br/>arm discussion"]
  GT -.->|"not built: no node or edge in agents/graph.py"| TV1["technical_v1<br/>deterministic Technical agent, agents v1<br/>NOT BUILT"]
  TV1 -.-> D3["Decision arm technical_v1<br/>reserved, never produced"]
  D3 -.-x|"never an input to Meta"| MB
  MD --> RK["risk, then store"]
  MB --> RK
  D3 -.-> RK

  classDef built fill:#e2e3e5,stroke:#2e7d32,stroke-width:2px,color:#000
  classDef notbuilt fill:#ffffff,stroke:#000,stroke-dasharray:2 2,color:#000
  class SN,GT,R1,MB,DI,MD,RK built
  class TV1,D3 notbuilt
```

| Slot for technical_v1 | Where | State |
|---|---|---|
| the agent | none | not built |
| its graph node and edges | `agents/graph.py` | not built |
| the arm `technical_v1` | `Arm` in `agents/schemas.py`, `ARM_ORDER` in `graph.py`, the `decisions.arm` CHECK (store migration 5) | reserved |
| on/off switch | `[agents.technical_v1] enabled` in `agents.example.toml`, `CODE_AGENTS` in `owner_config.py` | reserved |
| its input | `snapshot.metrics` through `metric(rows, name)`, the same table the Technical-LLM reads as `{{metrics_table}}` | built |
| its PREREG | `research/agents_v1/PREREG.md`: H = 240 primary | DRAFT |

## (e) Research data flow, sealed and spent periods

```mermaid
gantt
  title XAUUSD 1-min data and the periods it is split into (UTC)
  dateFormat YYYY-MM-DD
  axisFormat %Y
  section Data on disk
  XAUUSD.txt, usable from 2007          :d1, 2003-05-06, 2026-01-31
  Clean parquet                         :d2, 2020-01-01, 2026-01-31
  section Periods
  CONF or development 2007-2020         :p1, 2007-01-01, 2021-01-01
  DEV 2021-2023, later holdout and sanity check :p2, 2021-01-01, 2024-01-01
  Spent, read once by levels_confirm    :crit, p3, 2024-01-01, 2026-01-31
  Gap, no local data, left unused       :p4, 2026-01-31, 2026-10-01
  Sealed, live data only, open-ended    :crit, p5, 2026-10-01, 2027-01-01
```

```mermaid
flowchart TB
  TXT[("database/ohlcv_data/XAUUSD.txt<br/>2003-05 to 2026-01-30, untracked")]
  PQ[("XAUUSD_1min_UTC_clean.parquet<br/>2020-01-01 to 2026-01-30, untracked<br/>equals XAUUSD.txt close for close 2020-2023")]
  TXT --> AL["vol_features_r2/audit_long.py"] --> C1[("vol_features_r2/cache/<br/>xauusd_txt_pre2024_filetime.parquet<br/>rows before 2024")]
  TXT --> A24["levels_confirm/audit_2024.py"] --> C2[("levels_confirm/cache/<br/>xauusd_20231101_20260130.parquet")]
  SH["research/_shared/regime_features<br/>copies of the legacy regime engine"]

  C1 --> CONF["CONF or development, 2007-2020<br/>vol_features_r2, r3, trend_features, regime_v2,<br/>levels, levels_diag, sigma_compare, agents_v1"]
  PQ --> DEVS["DEV, 2021-2023<br/>vol_features r1 to r3, trend_features, regime_v2,<br/>sigma_compare, tools/sigma_calibration"]
  C2 --> SPENT["levels_confirm<br/>2024-01-01 to 2026-01-30, now spent"]
  SH --> CONF
  SH --> DEVS

  CONF --> OUTS["results/, figures/, REPORT.md<br/>per study, in git"]
  DEVS --> OUTS
  SPENT --> OUTS
  CONF --> FIT["sigma_compare/fit_final.py"]
  DEVS --> FIT
  FIT --> SM["sigma_compare/model/sigma_har_*.json"] -->|"copied, SHA-256 pinned"| DM["datafeed/features/models/"]
  DM --> VB["datafeed/features/vol_block.py<br/>load_sigma_model, sigma_over"] --> SCO["scoring.score_neutral<br/>not called in production"]
  VB --> AF["agents/features.py<br/>snapshot σ 60/240 and L, S_hd, T<br/>model from snapshot.sigma_model, no runner"]

  PQ -->|"replay, selftest, parity tests"| ENG["engineering use"]
  LIVE[("from 2026-10-01<br/>scanner journal, datafeed store, S3 archive")] -->|"planned PREREGs only"| FWD["forward tests<br/>agents_v1, LLM scanner, level_target"]

  classDef idle fill:#e2e3e5,stroke:#6c757d,color:#000
  classDef spent fill:#ffe5b4,stroke:#b8860b,color:#000
  classDef sealed fill:#f8d7da,stroke:#b02a37,color:#000
  class TXT,PQ,AL,A24,C1,SH,CONF,DEVS,OUTS,FIT,SM,DM,VB,SCO,AF,ENG idle
  class C2,SPENT spent
  class LIVE,FWD sealed
```

Where the periods are enforced in code:

| Rule | Code | Covers |
|---|---|---|
| global seal, before 2026-10-01 | `research/sealing.py` `assert_before_global_seal` | levels_confirm, levels_diag, sigma_compare |
| study seal, before 2024-01-01 | `SEAL` in `vol_features/run_study.py`, `assert_sealed` in `run_r2.py` / `run_r3.py`, `levels/levels.py`, `audit_long.py` | studies before levels_confirm |
| replay window | `ReplaySource` in `datafeed/ingest/xauusd_bars.py`: end at or before `SEAL_START`, a row at or after it stops the run, window inside `PARQUET_COVERAGE` | datafeed replay, scanner parity tests |
| σ training window | `vol_block.fit` and `VolParams`: `train_end` at or before `SEAL_START` | σ fits |
| agents | `agents/snapshot.py` `check_access`: replay after the seal raises `SealedError`; live needs a PREREG file and a wall clock within 120 s | agent snapshots |
| none | `datafeed.store.query.bars_before`, the scanner journal, the S3 archive | live (post-seal) data |

- The spent period has no shared check: each later study's loader asserts
  dates before 2024-01-01.
- `SEAL_START` (datafeed) and `GLOBAL_SEAL` (research) are separate
  constants, and a test asserts they are equal.

## (f) Ops

The host is an EC2 instance with 3.8 GB RAM and a 2 GB swap file, with the
stack in `~/oparli/deploy/scanner`. Its `.env` (host only, never in git) holds
the TWS login, the Telegram token and chat id, `BACKUP_S3_URI` and
`COMPOSE_PROFILES`.

Runtime and monitoring:

```mermaid
flowchart TB
  WD["cron every 5 min: watchdog.sh<br/>state in ~/.scanner-watchdog"]
  TH[("tools/status/thresholds.toml")]
  HJ[("~/scanner-health.json")]
  STB["python3 -m tools.status<br/>--once: exit 0 / 1 / 2<br/>or the Textual board"]
  subgraph COMPOSE["docker compose project scanner"]
    GW["ibgateway, 1536m<br/>ghcr.io/gnzsnz/ib-gateway:stable<br/>VNC on 127.0.0.1:5900"]
    SC["scanner, 512m, volume scanner_data at /data<br/>entrypoint.sh waits for the gateway or Redis<br/>healthcheck: /data/heartbeat younger than 300 s"]
    subgraph PROF["profile stream"]
      RD["redis, 384m, AOF, volume redis_data<br/>no published port"]
      FD["feed, 256m"]
      RC["recorder, 192m<br/>volume datafeed_store at /datafeed"]
    end
  end
  TG["Telegram"]
  OWNER["Owner"]

  WD -->|"probe clientId 996, then restart, at most 2 per 24 h"| GW
  WD -->|"restart counts, health status"| SC
  WD -->|"redis-cli ping"| RD
  WD -->|"restart counts"| FD
  WD -->|"restart counts, lag, rejected_messages, stream probe"| RC
  WD -->|"alerts, daily heartbeat 07:00 UTC"| TG
  SC -->|"alerts, staleness messages"| TG
  TG --> OWNER
  OWNER -->|"SSH tunnel to VNC, weekly 2FA"| GW
  TH -->|"red values = alert thresholds"| WD
  WD -->|"journal probe, docker compose exec"| SC
  WD -->|"facts, tools.status.write evaluates them"| HJ
  TH --> HJ
  HJ --> STB
  OWNER -->|"SSH"| STB

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  classDef optin fill:#fff3cd,stroke:#b8860b,stroke-dasharray:5 5,color:#000
  classDef ext fill:#dbe9f6,stroke:#1f5f99,color:#000
  class GW,SC,WD,TH,HJ,STB prod
  class RD,FD,RC optin
  class TG,OWNER ext
```

Backup and restore:

```mermaid
flowchart LR
  BK["cron 22:30 UTC daily: backup.sh<br/>writes last_ok to ~/.scanner-backup"]
  SC["scanner container"]
  J[("journal<br/>/data/scanner_decisions.sqlite")]
  DS[("datafeed store, if present<br/>/datafeed/datafeed.sqlite")]
  S3[("S3 under BACKUP_S3_URI<br/>journal/, store/, archive/bars/<br/>SSE AES256, IAM role, no deletes")]
  RS["restore.sh, by hand<br/>dry run unless --yes"]
  TG["Telegram"]

  BK -->|"docker compose exec"| SC
  SC -->|"backup_sqlite.py snapshot"| J
  SC -->|"backup_sqlite.py snapshot, export_parquet.py last 7 days"| DS
  BK -->|"gzip, aws s3 cp"| S3
  BK -->|"on failure"| TG
  S3 -->|"download, integrity check"| RS
  RS -->|"stop writer, keep .pre-restore copy, swap atomically, start"| SC

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  classDef optin fill:#fff3cd,stroke:#b8860b,stroke-dasharray:5 5,color:#000
  classDef ext fill:#dbe9f6,stroke:#1f5f99,color:#000
  class BK,SC,J,RS prod
  class DS optin
  class S3,TG ext
```

**CI** (`.github/workflows/ci.yml`) runs on every push and PR: pytest with a
Redis service, the selftest when the parquet exists, ruff, shellcheck and
gitleaks. It has no deploy step. The parquet isn't in git, so on GitHub every
test that needs it, the selftest included, skips.

**Watchdog checks.** The alert thresholds are the `red` values in
`tools/status/thresholds.toml`. `watchdog.sh` keeps the same numbers as a
fallback if the file can't be read, and `test_status_config.py` keeps the two
equal.
- Gateway down for 2 checks: alert, then a gateway restart.
- Scanner restarted 3 or more times in 5 minutes.
- Scanner unhealthy for 2 checks: alert only.
- Feed or recorder crash loop; Redis not answering PING.
- Disk 85 % or more; MemAvailable under 10 %.
- Last backup over 26 hours old.
- Recorder lag of 15 or more market-open minutes, and any growth in
  `rejected_messages`.

Market alerts are quiet from Friday 17:00 to Sunday 17:00 New York. The
watchdog finds containers by their fixed names (`scanner-scanner-1` and so
on), so the compose project must be called `scanner`.

**Status board** (`tools/status`). Every watchdog run also writes
`~/scanner-health.json`: green, yellow or red for ten components (gateway,
feed, redis, recorder, scanner, journal, backups, disk, memory, agents), each
with its reason. A fact the watchdog couldn't measure is red, never green.
Components outside the deployment show OFF: Redis and the recorder without
the stream profile, and agents always, for now. A `health.json` older than
15 minutes turns every component red.

**Deploys** are manual (`docs/DEPLOY_RUNBOOK.md`). Rollback means setting
`SCANNER_BAR_SOURCE=ibkr`, never using an image from before the journal
migration.

## (g) Files and stores

| File or store | Lives in | Written by | Read by | Notes |
|---|---|---|---|---|
| `database/ohlcv_data/XAUUSD.txt` | developer machine, untracked | not recorded in git | `audit_long.py`, `audit_2024.py` (both cache it) | 1-min bars, 2003-05-06 to 2026-01-30; usable from 2007 (`research/vol_features/DATA_AUDIT_LONG.md`) |
| `database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet` | developer machine, untracked | built from `XAUUSD.txt` (the script isn't in git) | scanner `--date` and `--selftest`, `ReplaySource`, research DEV (2021–2023), `tools/sigma_calibration`, parity, scoring and agents tests | 2020-01-01 23:00 to 2026-01-30 21:59 UTC (`PARQUET_COVERAGE`) |
| `research/vol_features_r2/cache/xauusd_txt_pre2024_filetime.parquet` | developer machine, gitignored | `research/vol_features_r2/audit_long.py` | the r2/r3 loaders, so: trend_features, regime_v2, levels, levels_diag, sigma_compare, agents_v1 | rows before 2024 |
| `research/levels_confirm/cache/xauusd_20231101_20260130.parquet` | developer machine, gitignored | `research/levels_confirm/audit_2024.py` | `research/levels_confirm/confirm.py` | spent period |
| `research/<study>/results/`, `figures/` | git | the study's run script | its `REPORT.md` | |
| `research/_shared/regime_features/` | git | copied from `legacy/regime_classifier` | vol_features r1–r3, trend_features, regime_v2 | byte-identical copies |
| `research/sigma_compare/model/sigma_har_*.json` | git | `research/sigma_compare/fit_final.py` | copied by hand to `datafeed/features/models/` | |
| `datafeed/features/models/sigma_har_*_v1.json` | git, package data | copy of the research model | `vol_block.load_sigma_model` (SHA-256 pinned in `SIGMA_MODELS`), then `scoring.score_neutral` and `agents.features` | default `parquet2021_2023`; `DATAFEED_SIGMA_MODEL` overrides it. The agents name theirs in `snapshot.sigma_model`, with no default in code |
| scanner journal `scanner_decisions.sqlite` | EC2 volume `scanner_data` at `/data/`; locally `database/` (`SCANNER_DB`) | `backend/deterministic_scanner.py`, the only writer; it migrates on open | `backend/journal_summary.py` (read-only), `backup_sqlite.py` via `backup.sh`, `tools/status/probes/journal_stats.py` (read-only, run by the watchdog inside the container) | gitignored; daily copy to S3 `journal/` |
| `/data/heartbeat` | volume `scanner_data` | the scanner's main loop (`beat`) | compose healthcheck, which the watchdog reads through `docker inspect` | |
| `/data/.redis-wait-alerted` | volume `scanner_data` | `deploy/scanner/entrypoint.sh` | `entrypoint.sh` (hourly alert limit) | stream mode only |
| Redis stream `bars.xauusd.1m` | volume `redis_data` (AOF) | feed (`python -m datafeed.ingest`) | consumer groups `recorder` and `scanner` (`SCANNER_STREAM_GROUP`); `tools/status/probes/stream_stats.py` (newest live entry, run by the watchdog) | stream profile; about 100k entries kept |
| datafeed store `datafeed.sqlite` | volume `datafeed_store` at `/datafeed/`; locally `DATAFEED_DB` | `datafeed.recorder`, the only writer | scanner warm-up, `export_parquet.py`, `backup_sqlite.py`, watchdog store stats, `agents.snapshot`, `vol_block.read_state`, `scoring.read_bars`; spreads: `query.spreads`, `tools/spread_by_hour.py` (read-only) | WAL; tables `bars` (with the spread columns since migration 4), `bar_conflicts`, `rejected_messages` |
| S3 `journal/`, `store/` | S3 under `BACKUP_S3_URI` | `deploy/scanner/backup.sh` | `deploy/scanner/restore.sh` (by hand) | never deleted by scripts; retention by lifecycle rule |
| S3 `archive/bars/date=YYYY-MM-DD/XAUUSD.parquet` | S3 | `backup.sh` via `export_parquet.py` | nothing | post-seal data |
| `deploy/scanner/.env` | EC2 host only | owner | docker compose, `watchdog.sh` and `backup.sh` (`val()`) | never in git; `.env.example` is the template |
| `~/.scanner-watchdog`, `~/.scanner-backup` | EC2 host | `watchdog.sh`; `backup.sh` (`last_ok`) | `watchdog.sh` | state files |
| `~/scanner-watchdog.log`, `~/scanner-backup.log` | EC2 host | cron | owner | |
| `~/scanner-health.json` (`HEALTH_JSON`) | EC2 host | `watchdog.sh` through `python3 -m tools.status.write` (atomic write) | `python3 -m tools.status` (`--once` or the board) | 10 components; stale after 15 min |
| `tools/status/thresholds.toml` | git | owner | `tools.status` (evaluate), `watchdog.sh` (its alert thresholds = the `red` values) | the one place thresholds are set |
| `tools/status/requirements-status.lock` | git | copied verbatim from `requirements.lock` | `pip` on the host, for the Textual board | `test_status_lock.py` checks it is a subset |
| agent store (`AGENTS_DB`) | wherever it's set; nothing deployed | `agents.store.sqlite` (snapshot and store nodes) and the LLM ledger (`agents/llm/ledger.py`) | tests, `python -m agents.epoch --db` | append-only, migration 5: `snapshots`, `runs`, `votes`, `decisions`, `discussions`, `llm_calls`, `llm_responses` (the cache); `debate_turns` is history, no longer written |
| owner config `agents.toml` | outside the repo, at `$AGENTS_CONFIG`; template `agents/agents.example.toml` | owner; code never writes it | `agents.owner_config.OwnerConfig`: models, effort, max_tokens per agent and round, discussion, Meta rule, LLM, budget, render | not in git. Without it Meta has no rule (both decisions `none`), and an LLM agent would abstain `config_invalid`. Its SHA-256 enters the epoch |
| `agents/prompts/visual.md`, `technical.md`, `sentiment.md`, `gex.md`, `discussion.md` | git, package data | owner | `agents.prompting.files.load_all` | all `version: TEMPLATE`, so refused; `gex.md` is reserved (GEX is code and doesn't read it) |
| kill switch | `budget.kill_switch_path` in `agents.toml`, or `AGENTS_LLM_KILL=1` | owner | `agents/llm/budget.py`, before every live call | while present, LLM agents abstain `kill_switch` |
| `agents/experiments/<id>/PREREG.md` | git | owner | `agents.prereg.require_prereg` | none exists yet; checked on disk, not in git |
| `langgraph.json`, `requirements-studio.lock` | git | `uv pip compile` (the lock) | `langgraph dev` in a separate `.venv-studio` (`tools/langgraph_studio/studio.py:make_graph`) | local Studio only; replay data, in-memory agent store, empty `env`; `.langgraph_api/` is gitignored. The lock holds every project pin unchanged (tested) |
| `agents/config.example.toml` | git | | `AgentsConfig.from_toml` | snapshot (incl. `sigma_model`), gate, `node_timeout_s` |
| `agents/prompts/bull.md`, `bear.md` | git, package data | | nothing | the old debate prompts, unused since the discussion replaced the debate |
| `requirements.lock`, `requirements.cutoff.txt` | git | `pip-compile` | CI install, `tests/agents_tests/test_lock.py` | extras `llm` (anthropic) and `status` (textual) included; the Dockerfiles don't use the lock |
| `database/historical_news/*` | developer machine, untracked (`DATA_NOTES_events.md` in git) | `tools/scraper_fxfac/` | `legacy/` only | the LLM scanner design calls for a point-in-time calendar store; not built |
| `XAUUSD_COMPLETE.csv` | developer machine | `tools/data/fetch_data_xauusd.py` | nothing in the repo | IBKR MIDPOINT history |
| `GC_FUTURES_1MIN.csv`, `touch_events.parquet`, `tsmom_daily.parquet` | developer machine, untracked | `legacy/` | `legacy/` only | |

## Keeping this current

Each diagram comes from the module named in its labels. Recheck these files
when they change:
- (b): `datafeed/ingest`, `datafeed/bus.py`, `datafeed/recorder`,
  `datafeed/store`;
- (c): the journal section of `deterministic_scanner.py`;
- (d1): `agents/graph.py`, `agents/voters/`, `agents/meta/`, `agents/snapshot.py`;
- (d2): `agents/llm/` (`agent.py`, `call.py`, `budget.py`, `parse.py`),
  `agents/prompting/`, `agents/owner_config.py`;
- (d3): `agents/discussion/` (`rule.py`, `subgraph.py`, `node.py`);
- (d4): `Arm` in `agents/schemas.py`, `research/agents_v1/PREREG.md`;
- (e): `research/README.md` and `research/SEALING.md`;
- (f): `deploy/scanner/`, `tools/status/`.

The Mermaid blocks were checked with `@mermaid-js/mermaid-cli` 11.4.2.
