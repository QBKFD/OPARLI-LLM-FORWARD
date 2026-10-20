# Current architecture

Drawn from the code on `main` @ `d93803a` (2026-10-20), not from the design
docs. Work that exists only on unmerged branches is named as such.
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
  end

  subgraph EC2["EC2 host: deploy/scanner, docker compose"]
    GW["ibgateway<br/>IB Gateway + IBC"]
    SC["scanner<br/>backend/deterministic_scanner.py"]
    J[("journal<br/>/data/scanner_decisions.sqlite")]
    CRON["host cron<br/>watchdog.sh, backup.sh"]
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
    AG["agents/<br/>LangGraph graph, stub voters"]
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
  DATA --> RES -->|"fit_final.py"| MODEL --> LIB
  LIB -->|"scoring.score for TAKE outcomes"| SC
  DS -.->|"bars_before, nothing deployed"| AG

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  classDef optin fill:#fff3cd,stroke:#b8860b,stroke-dasharray:5 5,color:#000
  classDef idle fill:#e2e3e5,stroke:#6c757d,color:#000
  classDef ext fill:#dbe9f6,stroke:#1f5f99,color:#000
  class GW,SC,J,CRON prod
  class FEED,R,REC,DS optin
  class DATA,RES,MODEL,LIB,AG idle
  class IBKR,TG,OWNER,S3,GH ext
```

- The scanner is the only production service. It never trades and never
  calls an LLM.
- In `ibkr` mode (the default and the rollback), the scanner is a gateway
  client itself. In `stream` mode the feed is the gateway's only bar client,
  and the scanner reads Redis and the store.
- `scoring/` (and `risk/` through it) runs inside the scanner image. The rest
  of the developer-machine box isn't deployed.

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
    F["IbkrXauusdSource + BarBuilder<br/>datafeed/ingest/xauusd_bars.py<br/>datafeed/ingest/ibkr.py, clientId 31"]
    B[("Redis stream bars.xauusd.1m<br/>datafeed/bus.py, XADD MAXLEN about 100k")]
    REC["Recorder, group recorder<br/>datafeed/recorder/recorder.py"]
    ST[("datafeed.sqlite<br/>bars, bar_conflicts, rejected_messages<br/>datafeed/store/schema.py")]
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

## (d) Agents graph: built vs stub

`agents/graph.py` on `main`. Nothing deployed runs it: no trigger
implementation and no entry point ship. Only `tests/agents_tests` drives it.
Grey with a thick border: built. Red: stub or placeholder. Dashed: off by
default.

```mermaid
flowchart TB
  S0(("start")) --> TR["trigger<br/>TriggerEvent to run_id, as_of = floor(t, 1 min)"]
  TR --> SN["snapshot<br/>agents/snapshot.py<br/>bars_before(as_of), seal and live-mode checks, staleness<br/>hashed and saved before any agent runs"]
  SN -->|"stale"| STO
  SN --> GT["gate<br/>agents/nodes/gate.py<br/>market open, minimum bars per timeframe"]
  GT -->|"no-go"| STO
  GT --> V1["visual<br/>VisualStub: abstains"]
  GT --> V2["technical<br/>TechnicalStub: abstains"]
  GT --> V3["sentiment<br/>SentimentStub: abstains"]
  V1 --> AGG
  V2 --> AGG
  V3 --> AGG
  AGG["aggregator<br/>aggregate_v0: 2 agreeing non-abstaining votes"]
  AGG -->|"debate.enabled = false, the default"| RISK
  AGG -.->|"debate.enabled = true"| DEB["debate subgraph<br/>agents/debate/: bull and bear turns, evidence check, judge<br/>no debaters ship, judge code_v0 is a placeholder"]
  DEB -.-> RISK
  RISK["risk<br/>apply_risk: pass-through"] --> STO
  STO["store<br/>SqliteAgentStore at AGENTS_DB<br/>append-only, one transaction per run"] --> E0(("end"))

  classDef built fill:#e2e3e5,stroke:#2e7d32,stroke-width:2px,color:#000
  classDef stub fill:#f8d7da,stroke:#b02a37,color:#000
  classDef off fill:#ffffff,stroke:#6c757d,stroke-dasharray:5 5,color:#000
  class TR,SN,STO built
  class GT,V1,V2,V3,AGG,RISK stub
  class DEB off
```

| Node | State on `main` |
|---|---|
| trigger | built; the `Trigger` protocol and `drive()` exist, but no trigger does |
| snapshot | built: `as_of` floor, replay refused after 2026-10-01, live mode needs an experiment PREREG and a wall clock within 120 s |
| gate, aggregator | placeholder rules, "not to be tuned on results" |
| visual, technical, sentiment | stubs that always abstain |
| debate | built and tested, off by default; no real debaters, placeholder judge |
| risk | pass-through stub |
| store | built: SQLite, append-only (UPDATE and DELETE abort), one transaction per run |

**Unmerged branch `feat/agents-metrics` (4b78b29),** stacked on
`feat/agents-architecture`, `-prompt-config`, `-llm-client` and
`-discussion`, adds:
- LLM voters (technical, visual, sentiment, gex) built from owner-written
  prompts; a missing or `TEMPLATE` prompt means abstain;
- an Anthropic client with a response cache, a daily call budget, a kill
  switch, a ledger and cache-only rerun;
- a discussion subgraph that replaces the debate, plus Meta rules;
- the epoch hash, and σ features in the snapshot.

None of this is on `main`.

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

  PQ -->|"replay, selftest, parity tests"| ENG["engineering use"]
  LIVE[("from 2026-10-01<br/>scanner journal, datafeed store, S3 archive")] -->|"planned PREREGs only"| FWD["forward tests<br/>agents_v1, LLM scanner, level_target"]

  classDef idle fill:#e2e3e5,stroke:#6c757d,color:#000
  classDef spent fill:#ffe5b4,stroke:#b8860b,color:#000
  classDef sealed fill:#f8d7da,stroke:#b02a37,color:#000
  class TXT,PQ,AL,A24,C1,SH,CONF,DEVS,OUTS,FIT,SM,DM,VB,SCO,ENG idle
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
  WD -->|"restart counts, lag, rejected_messages"| RC
  WD -->|"alerts, daily heartbeat 07:00 UTC"| TG
  SC -->|"alerts, staleness messages"| TG
  TG --> OWNER
  OWNER -->|"SSH tunnel to VNC, weekly 2FA"| GW

  classDef prod fill:#d4edda,stroke:#2e7d32,color:#000
  classDef optin fill:#fff3cd,stroke:#b8860b,stroke-dasharray:5 5,color:#000
  classDef ext fill:#dbe9f6,stroke:#1f5f99,color:#000
  class GW,SC,WD prod
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

**Watchdog checks.** Thresholds are constants at the top of `watchdog.sh`.
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
| `datafeed/features/models/sigma_har_*_v1.json` | git, package data | copy of the research model | `vol_block.load_sigma_model` (SHA-256 pinned in `SIGMA_MODELS`), then `scoring.score_neutral` | default `parquet2021_2023`; `DATAFEED_SIGMA_MODEL` overrides it |
| scanner journal `scanner_decisions.sqlite` | EC2 volume `scanner_data` at `/data/`; locally `database/` (`SCANNER_DB`) | `backend/deterministic_scanner.py`, the only writer; it migrates on open | `backend/journal_summary.py` (read-only), `backup_sqlite.py` via `backup.sh` | gitignored; daily copy to S3 `journal/` |
| `/data/heartbeat` | volume `scanner_data` | the scanner's main loop (`beat`) | compose healthcheck, which the watchdog reads through `docker inspect` | |
| `/data/.redis-wait-alerted` | volume `scanner_data` | `deploy/scanner/entrypoint.sh` | `entrypoint.sh` (hourly alert limit) | stream mode only |
| Redis stream `bars.xauusd.1m` | volume `redis_data` (AOF) | feed (`python -m datafeed.ingest`) | consumer groups `recorder` and `scanner` (`SCANNER_STREAM_GROUP`) | stream profile; about 100k entries kept |
| datafeed store `datafeed.sqlite` | volume `datafeed_store` at `/datafeed/`; locally `DATAFEED_DB` | `datafeed.recorder`, the only writer | scanner warm-up, `export_parquet.py`, `backup_sqlite.py`, watchdog store stats, `agents.snapshot`, `vol_block.read_state`, `scoring.read_bars` | WAL; tables `bars`, `bar_conflicts`, `rejected_messages` |
| S3 `journal/`, `store/` | S3 under `BACKUP_S3_URI` | `deploy/scanner/backup.sh` | `deploy/scanner/restore.sh` (by hand) | never deleted by scripts; retention by lifecycle rule |
| S3 `archive/bars/date=YYYY-MM-DD/XAUUSD.parquet` | S3 | `backup.sh` via `export_parquet.py` | nothing | post-seal data |
| `deploy/scanner/.env` | EC2 host only | owner | docker compose, `watchdog.sh` and `backup.sh` (`val()`) | never in git; `.env.example` is the template |
| `~/.scanner-watchdog`, `~/.scanner-backup` | EC2 host | `watchdog.sh`; `backup.sh` (`last_ok`) | `watchdog.sh` | state files |
| `~/scanner-watchdog.log`, `~/scanner-backup.log` | EC2 host | cron | owner | |
| agent store (`AGENTS_DB`) | wherever it's set; nothing deployed | `agents.store.sqlite` (snapshot and store nodes) | tests | append-only |
| `agents/experiments/<id>/PREREG.md` | git | owner | `agents.prereg.require_prereg` | none exists yet; checked on disk, not in git |
| `agents/config.example.toml` | git | | `AgentsConfig.from_toml` | |
| `agents/prompts/bull.md`, `bear.md` | git, package data | | nothing | templates for future debaters |
| `requirements.lock`, `requirements.cutoff.txt` | git | `pip-compile` | CI install, `tests/agents_tests/test_lock.py` | the Dockerfiles don't use the lock |
| `database/historical_news/*` | developer machine, untracked (`DATA_NOTES_events.md` in git) | `tools/scraper_fxfac/` | `legacy/` only | the LLM scanner design calls for a point-in-time calendar store; not built |
| `XAUUSD_COMPLETE.csv` | developer machine | `tools/data/fetch_data_xauusd.py` | nothing in the repo | IBKR MIDPOINT history |
| `GC_FUTURES_1MIN.csv`, `touch_events.parquet`, `tsmom_daily.parquet` | developer machine, untracked | `legacy/` | `legacy/` only | |

## Keeping this current

Each diagram comes from the module named in its labels. Recheck these files
when they change:
- (b): `datafeed/ingest`, `datafeed/bus.py`, `datafeed/recorder`,
  `datafeed/store`;
- (c): the journal section of `deterministic_scanner.py`;
- (d): `agents/graph.py`;
- (e): `research/README.md` and `research/SEALING.md`;
- (f): `deploy/scanner/`.

The Mermaid blocks were checked with `@mermaid-js/mermaid-cli` 11.4.2.
