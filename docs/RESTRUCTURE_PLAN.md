# Restructure plan: one `backend` package

**Status:** plan only (phase 0). Nothing has moved yet. Drawn from `main` @
c556533 on 2026-10-21. Decisions taken without the owner:
[docs/decisions/refactor-backend-layout.md](decisions/refactor-backend-layout.md).

## 1. What and why

The live system (the level scanner, its journal, the datafeed, scoring) is
spread over a root-level script (`backend/deterministic_scanner.py`, 1,929
lines), a helper (`backend/services/tws_connector.py`) and four root packages
(`datafeed`, `agents`, `risk`, `scoring`). The restructure makes `backend/`
the one importable package and splits the scanner into modules, in three PRs:

| PR | Branch | What |
|---|---|---|
| 1 | `refactor/backend-layout` | Mechanical moves: `datafeed`, `agents`, `risk`, `scoring` into `backend/`; tests into `tests/<package>/`; every reference updated. The scanner files don't move. |
| 2 | `refactor/scanner-package` (off PR 1) | Split the scanner into `backend/levels/` (pure) and `backend/scanner/`; remove `deterministic_scanner.py` and `services/`. |
| 3 | `docs/tidy` (off `main` after PR 1 and PR 2) | Current docs only in `docs/`; the rest to `docs/history/`; root `DECISIONS.md` split into `docs/decisions/`. |

**Rules for all three.**
- No behaviour change. A bug found on the way is listed in the branch's
  decision file and the report, and fixed later in its own PR.
- These stay exactly as they are:
  - env var names and CLI flags;
  - Telegram message texts;
  - journal and store schemas;
  - DB file paths and the S3 layout.
- Never merge, never push to `main`, never touch EC2, never commit `.env`.
- gitleaks before every push.

## 2. The golden replay (before-state)

### 2.1 What it runs

A harness (scratchpad, not in git: decision BL10) runs the scanner the way
production starts it: its own `main()`, with
`--date`/`--live`/`--dry-live`/`--selftest`. It fakes only the outside world:
- **Clock:** `pd.Timestamp.now`, `time.time` and `time.sleep` on the main
  thread, and SQLite's `datetime('now')`, all follow one simulated clock.
- **Telegram:** `urllib.request.urlopen` answers `api.telegram.org`.
  `sendMessage` is recorded, and `getUpdates` returns scripted replies.
- **IBKR:** a fake `ib_insync` module sends ticks, and `TWSConnector` builds
  the bars from them. It also answers the history request.
- **Redis:** a real server (7.4.2). The scanner's stream-feed thread is
  stepped in lock-step with the clock, so every run is deterministic.
- **Datafeed store:** a real SQLite file, filled as the recorder would fill it.

Each pass of the scanner's main loop is one checkpoint: every 15 minutes, plus
scenario times. At each checkpoint the harness moves the clock and publishes
the bars that have ended by then (stream: Redis and the store; ibkr: ticks).
The feed thread then processes them, and the due Telegram replies are
returned.

| Bar source | How it runs |
|---|---|
| replay | `--date D --db J` (journal `bar_source` 'parquet') |
| stream | `--live` with `SCANNER_BAR_SOURCE=stream`: warm-up from the datafeed store, bars from the Redis stream `bars.xauusd.1m` |
| ibkr | `--live` with `SCANNER_BAR_SOURCE=ibkr`: history and ticks from the fake gateway (added, decision BL2) |

### 2.2 What it captures

Hashed per run, then per day. Only Redis entry ids and the work directory
are normalised (BL7).
- **Journal:** every table, row and column of `alerts` (all fields, all
  statuses: `sent`, `missed_while_down`, `missed_history`), `decisions`,
  `outcomes`, `counterfactuals`, `source_switches` and `scanner_state`, plus
  the schema and `PRAGMA user_version`.
- **Telegram:** every call, in order: alert texts, confirmations, status
  messages, `getUpdates` offsets.
- **IBKR:** every call to the gateway (connect with client id, history
  request, market data subscription, cancel).
- **stdout and stderr:** the scanner's log lines and `logging` output.
- **Heartbeat** file content.
- **`journal_summary`** for the day, as text and `--json`.
- **How each process ended:** returned, `SystemExit` code, and the clock time.

### 2.3 Days and runs

28 days plus the selftest (BL3). Each day gets a replay run, a stream run and
an ibkr run (`<day>_replay`, `<day>_stream`, `<day>_ibkr`).

| Day | Why |
|---|---|
| 2023-03-12 | Sun, US DST starts (reopen 22:00 UTC) |
| 2023-03-13 | Mon, first day on EDT |
| 2023-06-15 | Thu, ordinary day (selftest's no-look-ahead day); Telegram failures |
| 2023-11-05 | Sun, US DST ends (reopen 23:00 UTC) |
| 2023-11-06 | Mon, first day on EST; stream feed crash and restart |
| 2023-11-23 | Thu, Thanksgiving (early close) |
| 2023-12-25 | Mon, Christmas (closed until the 23:00 reopen, while the scanner's market hours say open) |
| 2024-03-08 | Fri, selftest case 10's window; restart at 10:37 |
| 2024-03-10 | Sun, US DST starts |
| 2024-03-11 | Mon, first day on EDT; IBKR gateway drop and restart |
| 2024-03-29 | Fri, Good Friday (no bars at all) |
| 2024-03-31 | Sun, EU DST starts; Easter Sunday reopen |
| 2024-07-04 | Thu, Independence Day (early close) |
| 2024-11-03 | Sun, US DST ends (parity day) |
| 2024-11-04 | Mon, first day on EST (parity day); datafeed store not ready at start |
| 2024-12-24 | Tue, Christmas Eve (closes 18:43) |
| 2024-12-25 | Wed, Christmas (closed until the 23:00 reopen) |
| 2025-01-01 | Wed, New Year's Day (closed until the 23:00 reopen) |
| 2025-03-09 | Sun, US DST starts (parity day) |
| 2025-03-10 | Mon, first day on EDT (parity day); bar source switch ibkr → stream → ibkr |
| 2025-06-15 | Sun, reopen before the history-fill day |
| 2025-06-16 | Mon, the history-fill day: fills, restarts, late start, dry runs |
| 2025-07-03 | Thu, levels touched here are carried into 07-04 |
| 2025-07-04 | Fri, early close at 16:58 (the longest gap), run to the Sunday reopen |
| 2025-10-14 | Tue, ordinary day (selftest's journal day); odd stream entries |
| 2025-10-26 | Sun, EU DST ends |
| 2025-11-02 | Sun, US DST ends; no bars in the parquet |
| 2025-11-03 | Mon, first day on EST; no Sunday bars before it |

Extra runs (BL5):
- **2025-06-16** (`tests/scanner`'s cases):
  - stream and ibkr restarts: down from 07:05, back at 07:13 or at 09:00;
  - history fills at 00:25 and 00:31;
  - a start at 00:00:30;
  - dry runs for both sources.
- **2024-03-08:** restart at 10:37, both sources.
- **2023-06-15:** Telegram send and poll failures, both sources.
- **2024-11-04:** the store is not ready at start.
- **2025-10-14:** odd stream entries.
- **2023-11-06:** Redis crash, then a restart.
- **2024-03-11:** gateway drop, then a restart.
- **2025-03-10:** source switch.

In total: 104 runs.

### 2.4 Result on `main` @ c556533

**Run twice, byte-identical, no errors:**
- Two complete passes (104 runs each, 4 workers) give the same SHA-256 for
  every run and every day.
- The harness was fixed before this (BL8). Every run that had completed
  before the fix gave the same hashes then too.

**What the 104 runs exercised:**

| Output | Count |
|---|---|
| alerts, by status | 523 `sent`, 5 `missed_while_down`, 6 `missed_history` |
| decisions | 268 TAKE, 107 SKIP, 50 MISSED |
| TAKE outcomes | 39 tp, 80 sl, 14 timeout, 87 unscorable |
| counterfactuals | 425 |
| Telegram calls | 895 `sendMessage`, 11,560 `getUpdates` |
| scanner processes | 111 ended normally (stop), 2 exited 1 (the injected Redis crash and gateway drop), 1 selftest exited 0 |

**Per-day SHA-256** (first 16 hex characters; the full values are in the
scratchpad's `golden/before_a/hashes.json`):

| Day | Runs | SHA-256 |
|---|---|---|
| selftest | 1 | `b6ef6cea09de0b2a` |
| 2023-03-12 | 3 | `e17309f4bea02e23` |
| 2023-03-13 | 3 | `ce3c71aee65897b2` |
| 2023-06-15 | 5 | `4dde53642245af4e` |
| 2023-11-05 | 3 | `1b4ef603807edf54` |
| 2023-11-06 | 4 | `c3cc57fc680e4f78` |
| 2023-11-23 | 3 | `0e2a193f7c04566a` |
| 2023-12-25 | 3 | `3b901aabd3ea81bf` |
| 2024-03-08 | 5 | `d62400643a2e53fd` |
| 2024-03-10 | 3 | `53da55126d2df813` |
| 2024-03-11 | 4 | `79fd2e25985d5d27` |
| 2024-03-29 | 3 | `042e2f7621aa9a6f` |
| 2024-03-31 | 3 | `bf736db7b2b456ee` |
| 2024-07-04 | 3 | `1e248a322379fcc4` |
| 2024-11-03 | 3 | `7ccededee7b4d6be` |
| 2024-11-04 | 4 | `cd7668c496392d5a` |
| 2024-12-24 | 3 | `620ee5d6d8d51e70` |
| 2024-12-25 | 3 | `67b278577558231c` |
| 2025-01-01 | 3 | `ec28a6d3677ebcfc` |
| 2025-03-09 | 3 | `9b106162283599ed` |
| 2025-03-10 | 4 | `130f245504d4e022` |
| 2025-06-15 | 3 | `c1ca9b938b8e2528` |
| 2025-06-16 | 13 | `78e8a028e211d13e` |
| 2025-07-03 | 3 | `d92d6616460173bb` |
| 2025-07-04 | 3 | `e6707523be66711a` |
| 2025-10-14 | 4 | `d5e481be31f8bad6` |
| 2025-10-26 | 3 | `df6228622584fb5a` |
| 2025-11-02 | 3 | `4f3dcf3a11a34e28` |
| 2025-11-03 | 3 | `f3a580f8f5bc83ed` |

### 2.5 What it does not cover

- the real IB Gateway, the real Telegram API and the real Docker image;
- the entrypoint's wait loops, the watchdog and backups (`tests/deploy`
  covers those with stubs);
- `--help` output.

PR 1's and PR 2's proofs add the selftest, the full test suite, `docker
compose config` and, where Docker is available, an image build.

## 3. Target tree

After PR 2 (PR 1 is this tree without `levels/` and `scanner/`, with
`deterministic_scanner.py`, `journal_summary.py` and `services/` still in
`backend/`):

```
backend/
  __init__.py
  README.md
  agents/            from agents/     (LangGraph agents v2; not deployed)
  datafeed/          from datafeed/   (IBKR -> Redis -> SQLite store)
  risk/              from risk/
  scoring/           from scoring/
  levels/            PURE: no IO, no env, no clock, no network
    __init__.py      public API: build_levels, touches, Scanner, LevelSource
    build.py         sessions, level instances (active_from/active_to), ids
    touch.py         arming, first-touch, TOL, the 15-min collapse, alert ids; touches()
    source.py        LevelSource protocol (levels_at(t), trigger_levels(alert)) + one implementation
  scanner/
    __init__.py      the package's API (the names the old module had)
    __main__.py      CLI entry: identical flags and env vars
    config.py        paths and env parsing (SCANNER_DB, SCANNER_BAR_SOURCE)
    market.py        UTC parsing and market hours (pure helpers)
    engine.py        bars in -> alerts out: LiveLevels, AlertSink, the heartbeat
    telegram.py      send, getUpdates, reply parsing, reply handling
    journal/
      schema.py      tables, migrations 1-4, epochs
      writes.py      alerts, decisions, scanner_state, MISSED sweep
      outcomes.py    counterfactuals, TAKE scoring
      summary.py     from backend/journal_summary.py
    sources/
      ibkr.py        from services/tws_connector.py, plus the ibkr live loop
      stream.py      warm-up, StreamFeed, staleness, the stream live loop
      replay.py      the parquet replay (--date)
    selftest.py
deploy/  docs/  research/  tools/  legacy/  database/ (local data, gitignored contents)
tests/
  __init__.py
  agents/  datafeed/  risk/  scoring/  scanner/  status/  deploy/   (each with __init__.py)
```

`database/` stays where it is: local data on the Mac and on the EC2 host,
paths unchanged. PR 1 adds `database/README.md` (what goes there; git-ignored).

## 4. Module map

### 4.1 `backend/deterministic_scanner.py` (1,929 lines)

"Uses" lists the other names of this file each one needs. Lazy imports stay
lazy (§4.5).

| Lines | Name | Uses | Goes to |
|---|---|---|---|
| 1-29 | module docstring | | `scanner/__init__.py` |
| 42-46 | `PARQUET`, `DB_PATH` (`$SCANNER_DB`) | | `scanner/config.py` (§8, R1: same resolved paths) |
| 47-49 | `SESSIONS`, `MIN_BARS_FULL_DAY`, `SESSION_CARRY_H` | | `levels/build.py` |
| 50-51 | `TOL`, `WINDOW_MIN` | | `levels/touch.py` |
| 52 | `CF_HORIZONS` | | `scanner/journal/outcomes.py` |
| 53 | `MISSED_AFTER_H` | | `scanner/journal/writes.py` |
| 54-55 | `OUTCOME_MAX_HOLD_MIN`, `OUTCOME_SETTLE_MIN` | | `scanner/journal/outcomes.py` |
| 56-59 | `RECONNECT_BACKOFF_S`, `SILENCE_WARN_S` | | `scanner/config.py`, `scanner/sources/ibkr.py` |
| 63-67 | `session_of` | SESSIONS | `levels/build.py` |
| 70-125 | `build_levels` | SESSIONS, MIN_BARS_FULL_DAY, SESSION_CARRY_H | `levels/build.py` |
| 129-216 | `Scanner` | TOL, WINDOW_MIN, session_of | `levels/touch.py` |
| 219-290 | `LiveLevels` | Scanner, build_levels, SESSIONS, _utc | `scanner/engine.py` |
| 293-404 | `AlertSink` | WINDOW_MIN, _utc, insert_alert, record_progress | `scanner/engine.py` |
| 407-418 | `_alert_sender` | format_alert | `scanner/engine.py` |
| 422-432 | `counterfactual` | CF_HORIZONS | `scanner/journal/outcomes.py` |
| 445-448 | `JOURNAL_VERSION`, `ALERT_STATUSES`, `PRE_MIGRATION_EPOCH` | | `scanner/journal/schema.py` |
| 451-468 | `open_log` | DB_PATH, _migrate_journal | `scanner/journal/schema.py` |
| 471-540 | `_migrate_journal` | PRE_MIGRATION_EPOCH | `scanner/journal/schema.py` |
| 543-558 | `start_epoch` | | `scanner/journal/schema.py` |
| 561-569 | `insert_alert` | ALERT_STATUSES | `scanner/journal/writes.py` |
| 572-582 | `last_live_bar`, `record_progress` | _utc | `scanner/journal/writes.py` |
| 585-590 | `insert_counterfactual` | | `scanner/journal/writes.py` |
| 593-596 | `_utc` | | `scanner/market.py` |
| 599-648 | `_alert_epoch`, `record_decision`, `record_outcome` | _utc, PRE_MIGRATION_EPOCH | `scanner/journal/writes.py` |
| 651-672 | `undecided_alerts`, `sweep_auto_missed` | MISSED_AFTER_H, record_decision | `scanner/journal/writes.py` |
| 675-700 | `alerts_needing_counterfactual`, `sweep_counterfactuals` | CF_HORIZONS, counterfactual, insert_counterfactual | `scanner/journal/outcomes.py` |
| 710 | `OUTCOME_EXIT_TYPES` | | `scanner/journal/outcomes.py` |
| 713-825 | `take_problem`, `score_take`, `takes_needing_outcome`, `insert_outcome`, `sweep_outcomes` | OUTCOME_*, _utc; lazy `scoring.outcome` | `scanner/journal/outcomes.py` |
| 829-874 | `format_alert`, `parse_reply`, `_isnum` | | `scanner/telegram.py` |
| 877-932 | `Telegram`, `TelegramMessage`, `parse_updates` | env `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | `scanner/telegram.py` |
| 935-948 | `TELEGRAM_OFFSET_KEY`, `load_offset`, `save_offset` | | `scanner/journal/writes.py` (they write `scanner_state`) |
| 952-984 | `load_data`, `replay_day` | PARQUET, Scanner, build_levels, counterfactual, journal writes, start_epoch | `scanner/sources/replay.py` |
| 988-1007 | `_load_recent_history` | lazy `ib_insync` | `scanner/sources/ibkr.py` |
| 1010-1026 | `_market_open` | | `scanner/market.py` |
| 1029-1042 | `beat` | | `scanner/engine.py` |
| 1045-1050 | `_send_quietly` | | `scanner/telegram.py` |
| 1053-1059 | `_valid_bar` | _market_open | `scanner/market.py` |
| 1062-1196 | `run_live` | everything above; lazy `services.tws_connector`; env `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID`, `SCANNER_HEARTBEAT` | `scanner/sources/ibkr.py` |
| 1205 | `BAR_SOURCES` | | `scanner/config.py` |
| 1206-1215 | `STREAM_GROUP`, `STREAM_BATCH`, `STREAM_BLOCK_MS`, `WARMUP_*`, `STREAM_STALE_MIN`, `STORE_RETRY_S` | | `scanner/sources/stream.py` |
| 1218-1223 | `bar_source` | env `SCANNER_BAR_SOURCE` | `scanner/config.py` |
| 1226-1237 | `_open_minutes` | _utc | `scanner/market.py` |
| 1240-1263 | `warmup_start`, `_only_valid`, `_store_bars` | _valid_bar; lazy `datafeed.store.query` | `scanner/sources/stream.py` |
| 1266-1334 | `StoreNotReady`, `stream_warmup`, `wait_for_store` | _open_minutes, beat | `scanner/sources/stream.py` |
| 1337-1372 | `StreamStaleness` | _open_minutes | `scanner/sources/stream.py` |
| 1375-1548 | `StreamFeed` | _valid_bar, _open_minutes, LiveLevels API | `scanner/sources/stream.py` |
| 1551-1562 | `_journal_new_alert` | insert_alert | `scanner/journal/writes.py` |
| 1565-1601 | `_handle_reply` | parse_reply, record_decision, take_problem, _send_quietly | `scanner/telegram.py` |
| 1604-1695 | `run_live_stream` | everything above; lazy `datafeed.bus`, `datafeed.ingest.xauusd_bars`; env `SCANNER_STREAM_CONSUMER`, `SCANNER_STREAM_GROUP`, `DATAFEED_DB`, `REDIS_URL`, `SCANNER_HEARTBEAT` | `scanner/sources/stream.py` |
| 1699-1893 | `selftest` | | `scanner/selftest.py` |
| 1896-1929 | `main` | | `scanner/__main__.py` |

### 4.2 `backend/services/tws_connector.py` (694 lines)

| Name | Goes to |
|---|---|
| `TWSConnector` (all 20 methods), its module-level `ib_insync` import and logger | `scanner/sources/ibkr.py`, moved with `git mv` so history follows. Seven methods are never reached from the scanner: `_subscribe_bars_sync`, `unsubscribe_bars`, `run_event_loop`, `get_connection_status`, `fetch_historical_bars` with `_fetch_historical_async`, and `_on_bar_update` (the stock/futures branch; XAUUSD is CMDTY). They move as they are, and are listed in the decision file, not deleted. |

### 4.3 `backend/journal_summary.py` (185 lines)

It moves whole to `scanner/journal/summary.py`. It stays stdlib-only and
read-only. `DEFAULT_DB` must still resolve to `<repo>/database/scanner_decisions.sqlite`.

### 4.4 New in PR 2 (no caller changes behaviour)

- **`backend/levels`:**
  - `touches(bars, levels, ...)`: a Scanner over a bar sequence, with the
    final flush, as `replay_day`, the selftest and the tests already do by
    hand.
  - `LevelSource`, a `typing.Protocol`: `levels_at(t)` and
    `trigger_levels(alert)`.
  - One pure implementation over `build_levels`' output, with unit tests.
    Nothing in `agents/` uses it yet.
  - **The level key** (owner's answer to Q2). A level keeps its `id` (its
    position in `build_levels`' list, which the Scanner's state uses). That
    id changes on every rebuild, so `LevelSource` gives each level a derived
    key instead:
    - `key = f"{type}@{active_from:%Y-%m-%dT%H:%MZ}"` (UTC), e.g.
      `PDH@2025-06-16T00:00Z` or `ASIA_H@2025-06-16T07:00Z`;
    - `build_levels` creates at most one level per type and UTC day:
      PDH/PDL, PWH/PWL, DOPEN and WOPEN start at the day's 00:00, and a
      session's H/L at that session's close. So type plus `active_from` is
      unique, and the key needs neither the value nor the list position;
    - `trigger_levels(alert)` maps an alert to the keys of the levels it
      touched.
    - **Unit tests:**
      - keys are unique within one build;
      - **stable across rebuilds:** build over a window, then over the same
        window extended by later bars and over one that starts a day later.
        Every level present in two builds has the same key in both, and the
        same value once its source period is complete, while the `id`s
        differ;
      - `key` doesn't depend on list order.
- **`backend/scanner/__init__.py`:**
  - It re-exports the old module's names, so the tests, `research/` and later
    callers have one import.
  - `run_live` and `_load_recent_history` come from a lazy module
    `__getattr__`. Importing `backend.scanner` then never imports
    `ib_insync`, exactly as importing `deterministic_scanner.py` never did.

### 4.5 Import order after PR 2 (no cycles)

```
levels.build <- levels.touch <- levels.source <- levels
scanner.config, scanner.market                       (stdlib, pandas)
scanner.journal.schema <- journal.writes <- journal.outcomes   (outcomes: lazy backend.scoring)
scanner.telegram       (journal.writes, journal.outcomes.take_problem)
scanner.engine         (levels, market, journal.writes, telegram.format_alert)
scanner.sources.replay (levels, journal)
scanner.sources.stream (engine, journal, telegram, market; lazy backend.datafeed)
scanner.sources.ibkr   (engine, journal, telegram, market; ib_insync at import, like tws_connector today)
scanner.selftest       (levels, engine, journal, telegram, market, sources.replay)
scanner.__main__       (config, journal, sources.replay, selftest; sources.ibkr and sources.stream imported inside main())
```

These stay lazy, as now:
- `ib_insync` (only in ibkr live mode);
- `backend.datafeed` (stream mode);
- `backend.scoring` (the first TAKE outcome);
- `urllib` and `json` inside `Telegram`.

So `--date` and `--selftest` keep needing only pandas.

### 4.6 Expected sizes (limit 1,000 lines per scanner module)

| Module | Lines (approx.) |
|---|---|
| `sources/ibkr.py` | ≈ 870 (694 + 155) |
| `sources/stream.py` | ≈ 470 |
| `engine.py` | ≈ 230 |
| `selftest.py` | ≈ 200 |
| `journal/summary.py` | ≈ 185 |
| `journal/writes.py` | ≈ 170 |
| `telegram.py` | ≈ 170 |
| `journal/outcomes.py` | ≈ 150 |
| `journal/schema.py` | ≈ 130 |
| every other module | < 100 |

## 5. PR 1: `refactor/backend-layout` (mechanical moves)

1. **Moves:**
   - `git mv` of `datafeed`, `agents`, `risk` and `scoring` into `backend/`;
   - a new `backend/__init__.py`;
   - imports become `backend.datafeed…`, `backend.agents…` and so on, in code,
     strings (`monkeypatch.setattr("datafeed.x", …)`), `python -m` commands and
     argparse `prog=` texts.
2. **The scanner files stay** as they are, except their lazy imports
   (`datafeed.*`, `scoring.outcome` become `backend.…`).
   `from services.tws_connector import TWSConnector` is unchanged, since
   `services/` doesn't move in PR 1.
3. **Tests:**
   - Renamed: `tests/agents_tests` → `tests/agents`, `risk_tests` → `risk`,
     `scoring_tests` → `scoring`, `status_tests` → `status`.
   - `tests/__init__.py` and one `__init__.py` per test directory, so pytest's
     default import mode puts the repo root on `sys.path` and test modules
     are `tests.<pkg>.test_x`. Nothing shadows `backend.*`.
   - Helper imports (`scanner_helpers`, `agents_testkit`, `agents_fakes`,
     `deploy_stubs`, `status_helpers`) become `tests.<pkg>.<helper>`.
     These are import-only edits.
4. **pyproject:**
   - `name = "oparli"`;
   - packages `backend*`;
   - package-data keys `backend.agents` and `backend.datafeed`;
   - testpaths renamed.
   - `requirements.lock` and `requirements-studio.lock` are regenerated with
     the same commands and cutoffs. Only the
     `# via datafeed (pyproject.toml)` annotations may change; a diff of the
     non-comment lines must be empty.
5. **CI:** ruff paths become `backend/agents backend/risk tools
   backend/datafeed backend/scoring`. The selftest step is unchanged in PR 1.
6. **Deploy:**
   - **Image:**
     - The scanner stays at `/app/deterministic_scanner.py` and
       `/app/services/`.
     - `datafeed`, `scoring` and `risk` go under `/app/backend/`, with
       `/app/backend/__init__.py`. The script's directory `/app` is on
       `sys.path`, so `backend.*` imports there.
     - The entrypoint and `SCANNER_DB` are unchanged.
   - **Compose:** `feed`/`recorder` entrypoints become
     `python -m backend.datafeed.ingest` and `backend.datafeed.recorder`.
   - **watchdog:** `STORE_STATS_PY`'s import is renamed. The status probes are
     renamed too.
   - **datafeed stack:** `deploy/datafeed/Dockerfile` copies `backend/`.
   - **Runbook:** commands renamed.
7. **tools:**
   - `tools/status` (probes, README test paths);
   - `tools/langgraph_studio`;
   - `tools/sigma_calibration`;
   - `tools/spread_by_hour.py`.
   - `langgraph.json` keeps `./tools/langgraph_studio/studio.py:make_graph`;
     `dependencies: ["."]` installs `oparli`.
8. **research:** import lines only, in `sigma_compare` (4 files) and
   `agents_v1/expected_exclusions.py`. Proof: rerun `sigma_compare`'s
   reproduction (`run_sigma.py`); `results_sigma.json` and `coefs.csv` must be
   byte-identical to the committed files.
9. **Package-boundary tests keep their rules under the new names:**
   - `risk` imports only the stdlib, itself and `backend.datafeed.features`;
   - `datafeed` doesn't import `risk` or `scoring`;
   - `scoring`'s allowed imports;
   - `agents`: no imports of `backend.services`,
     `backend.deterministic_scanner` or `backend.journal_summary` (later
     `backend.scanner`/`backend.levels`), `services`, `legacy`, or banned
     clients. Datafeed only through query and constants.
   - `test_backend_not_on_path` stays: `backend/` on `sys.path` would make
     `datafeed` importable a second time as a top-level package.
10. **Agents epoch ids change.** `input_code_sha256` hashes (relative path,
    file bytes) of 13 files, and 12 of them have `agents.…` or `datafeed.…`
    import lines that change. No epoch is frozen yet. Logged in the decision
    file.
11. **`database/README.md`:** what goes there, and that it is git-ignored.
12. **Proof:**
    - the same test count as `main`;
    - no assertion changed: a script applies the rename map to `main`'s test
      files, diffs them against the branch, and lists every remaining
      difference (imports only, or a path inside an assertion that names a
      moved file);
    - golden replay byte-identical;
    - the selftest passes;
    - `docker compose config` validates;
    - an image build plus selftest in it, if Docker is available (it isn't on
      this Mac: §9).

## 6. PR 2: `refactor/scanner-package` (off PR 1, PR into `main`)

**One module per commit; the golden replay runs after every commit**, and work
stops if a commit breaks byte-identity. `deterministic_scanner.py` shrinks
commit by commit and imports what has moved, so the tests and the CLI work at
every commit:

1. `backend/levels/build.py`, `touch.py`
2. `scanner/market.py`
3. `scanner/journal/schema.py`
4. `scanner/journal/writes.py`
5. `scanner/journal/outcomes.py`
6. `scanner/journal/summary.py` (`git mv` of `journal_summary.py`)
7. `scanner/telegram.py`
8. `scanner/engine.py`
9. `scanner/sources/replay.py`
10. `scanner/sources/stream.py`
11. `scanner/sources/ibkr.py` (`git mv` of `services/tws_connector.py`, then
    `_load_recent_history` and `run_live` added)
12. `scanner/selftest.py`
13. `scanner/config.py`, `__main__.py`, `__init__.py`. Then remove
    `deterministic_scanner.py` and `services/`, and the tests import
    `backend.scanner` (no more loading by file path). Dockerfile, compose,
    entrypoint, watchdog and CI become `python -m backend.scanner`.
14. `levels`: `touches()`, `LevelSource`, unit tests.
15. The committed golden test:
    - a synthetic part for CI;
    - a real-data part that hashes the 28 days and skips without the parquet.
16. **Lint:**
    - ruff default rules on `backend/scanner` and `backend/levels`, in CI.
      Only behaviour-neutral fixes (split `a; b`, unused imports); anything
      else gets a `noqa` with its reason, e.g. the bare `except:` in
      `TWSConnector.connect`, where `except Exception` would stop catching
      `KeyboardInterrupt`.
    - `mypy --strict` on `backend/levels`.
    - `mypy` on `backend/scanner` with no new findings against a baseline
      taken on `deterministic_scanner.py` before commit 1.
17. **Runbook section "Deploying the restructure":**
    - rebuild the image;
    - same volumes;
    - no journal migration;
    - rollback = the previous image tag.

Proof as PR 1, plus the line count of every scanner module (limit 1,000).

## 7. PR 3: `docs/tidy` (after PR 1 and PR 2 are merged)

- **Stay in `docs/`:**
  - ARCHITECTURE_CURRENT (redrawn for the new tree);
  - DEPLOY_RUNBOOK;
  - AGENTS_ARCHITECTURE, AGENTS_TECHNICAL_DESIGN, AGENT_LLM_SCANNER_DESIGN;
  - PROMPT_GUIDE, LANGGRAPH_STUDIO and DATAFEED_NOTES, if still accurate.
- **Move to `docs/history/`, each with a one-line header** (what it was, what
  superseded it): ARCHITECTURE_MAP, REPO_MAP, CLEANUP_NOTES, DEPLOY_PLAN and
  RESTRUCTURE_PLAN.
- **SCANNER_SWITCH_NOTES** moves only after the runbook covers everything still
  operational in it.
- **Root `DECISIONS.md`** is split into `docs/decisions/<branch>.md`, content
  unchanged, plus `docs/decisions/README.md` as an index. Package and study
  `DECISIONS.md` files stay where they are.
- **Root** keeps README, pyproject, the lock files, `langgraph.json`,
  `.env.example` and `.gitignore` only.
- **Links:** every link to a moved file is fixed, checked with a link checker.

PR 1 and PR 2 fix only the links and commands that break inside the files
they move or edit (e.g. `../docs/` from `agents/README.md`, which becomes
`../../docs/`) and the runbook's commands. PR 3 runs the link checker over
everything.

## 8. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | **Default paths are computed from `__file__`:** `PARQUET`, `DB_PATH` and `journal_summary.DEFAULT_DB` are `parent.parent / "database/..."`. Moving the file one level deeper changes them. | `config.py` resolves them from the repo root (`parents[2]` of `backend/scanner/config.py`), so on the Mac they stay `<repo>/database/...`. A new test pins them. **In the image** they become `/app/database/...` instead of `/database/...`: unused, since `SCANNER_DB` is set by the Dockerfile and compose and the parquet isn't in the image. Logged. |
| R2 | **Import timing:** today `ib_insync` loads only in ibkr live mode, `datafeed` only in stream mode, `scoring` only for the first outcome. A module split can make these eager. | Lazy imports stay lazy (§4.5). A test checks that importing `backend.scanner` loads neither `ib_insync` nor `backend.datafeed`. |
| R3 | **Source-text tests:** `test_downtime`, `test_heartbeat`, `test_journal_epoch`, `test_bar_source` and `test_warmup` assert on `inspect.getsource(run_live / run_live_stream / …)`, e.g. `'SCANNER_HEARTBEAT' in src` (`test_heartbeat.py:58`) and `env.get("SCANNER_STREAM_GROUP", STREAM_GROUP)` (`test_bar_source.py:97`). | The bodies of `run_live`, `run_live_stream`, `stream_warmup` and `wait_for_store` move verbatim. This conflicts with "env parsing in one place": §9, Q1. |
| R4 | **monkeypatch targets:** `test_bar_source` patches `ds.run_live`/`ds.run_live_stream` and calls `ds.main()`, and `test_journal_epoch` patches `ds.load_data`. After the split, a patch must target the module that looks the name up. | Those 3 `monkeypatch.setattr` lines retarget to `sources.ibkr`, `sources.stream` and `sources.replay`. They are listed as non-assertion fixture edits. |
| R5 | **Deploy tests pin paths in assertions:** `test_compose` checks COPY lines, `python -m datafeed.ingest` and `/app/deterministic_scanner.py --live`. | The rule stays; the moved path is renamed inside the assertion, and every such line is listed. PR 1 keeps `/app/deterministic_scanner.py`, so that assertion changes only in PR 2. |
| R6 | **Shadowing:** with `backend/` on `sys.path` (`python backend/deterministic_scanner.py` puts it there), `datafeed` is importable twice: as `backend.datafeed` and as top-level `datafeed`. | Every import is renamed. `test_backend_not_on_path` stays. PR 2 removes the script; the entry becomes `python -m backend.scanner`. |
| R7 | **Lock files:** renaming the project changes pip-compile's `via datafeed (pyproject.toml)` comments. | Regenerated with the same commands and cutoff dates; diff of the non-comment lines must be empty. |
| R8 | **Agents epoch ids change** with the moved files' bytes. | No epoch is frozen yet (asked: log it). |
| R9 | **The image runs Python 3.12; the venv 3.13.** | No 3.13-only syntax in the moved code; ruff `target-version = "py312"` stays. |
| R10 | **The host's code is not `main`.** EC2 runs `pipeline-fixes-and-cleanup` @ c62810d (owner, 2026-10-21; containers built about 3 weeks earlier). Its journal has none of `main`'s migrations. The owner deploys current `main` on Sat 24 Oct and the restructure later, as a separate deploy. | PR 2's runbook section ("no migration, rollback = previous image tag") assumes the host already runs `main`. It says so, and that the `main` deploy, with its one-way journal migrations, must come first. |
| R11 | **The status probes and watchdog run inside the containers** (`python - < probe.py`, `python -c "$STORE_STATS_PY"`), so their imports must match the image's layout. | `tests/deploy` and `tests/status` cover the probes' text. PR 1 checks the image layout against the imports by hand (no Docker here). |
| R12 | **Research is a sealed record.** | Only import lines change in research code; markdown records keep the old paths as written. The `sigma_compare` rerun must be byte-identical. |
| R13 | **CLI usage text** (`argparse` prog) follows `sys.argv[0]`: `deterministic_scanner.py` today, `__main__.py` after PR 2. | Only `--help` and the error line show it. The flags and help texts stay identical. The golden harness doesn't cover `--help` (§2.5). Listed as an expected difference. |
| R14 | **The harness can't catch** a difference in what it fakes (IBKR, Telegram network, Docker). | Those paths keep their tests (`tests/deploy`, `test_journal_replies`). The TWSConnector code moves unchanged with `git mv`. |

## 9. Questions for the owner (answered 2026-10-21)

Q1, Q2 and Q4: the defaults below. Q3: EC2 runs `pipeline-fixes-and-cleanup`
@ c62810d; `main` is deployed first, on Sat 24 Oct (R10).

| # | Question | Default (taken) |
|---|---|---|
| Q1 | **PR 2's `config.py`** can't hold every env read without changing 2 source-text assertions (R3: `test_heartbeat.py:58`, `test_bar_source.py:97`). `run_live` and `run_live_stream` read `IB_*`, `SCANNER_HEARTBEAT`, `SCANNER_STREAM_*`, `REDIS_URL` and `DATAFEED_DB` in their own bodies, and the tests check that text. | `config.py` gets `SCANNER_DB` (the paths), `SCANNER_BAR_SOURCE` (`bar_source`) and the shared constants. The two live loops keep their env reads as they are, and no assertion changes. Alternative: move them all and rewrite those assertions. The source-text tests are logged as brittle (BL13): a follow-up PR replaces them with behaviour tests. |
| Q2 | **"Level instances with stable ids."** Today a level's `id` is its position in `build_levels`' list: deterministic for the same bars, but it changes on every rebuild, and the Scanner keys its state by it. | Ids stay as they are (no behaviour change). `LevelSource` exposes a derived key, built and tested as in §4.4. Alternative: a new id scheme in the Scanner, which is a behaviour change for a later PR. |
| Q3 | **What does EC2 run now?** | Answered: `pipeline-fixes-and-cleanup` @ c62810d, nothing from `main`. `main` is deployed first, on Sat 24 Oct; the restructure later (R10). |
| Q4 | **No Docker on this Mac**, so no image build or selftest in the image. For `docker compose config` I'd fetch the standalone `docker-compose` binary (a pinned release at least two weeks old, checksum-verified) into the scratchpad. | Do that. If it's not wanted, `tests/deploy/test_compose.py` (which parses the compose file) is the only compose check. |

## Appendix A: every file that references a moved path

Generated from `main` @ c556533. Kinds:
- **import:** an import statement.
- **dotted name:** a module name in a string, a `python -m` command or an argparse `prog=`.
- **path:** `datafeed/` and so on.
- **test dir:** a test directory that is renamed.
- **scanner:** the scanner's files or `/app/` paths.
- **project name:** the pyproject name.

222 tracked files outside `legacy/`. `legacy/` is never imported and is not edited.

#### `(root)` (6)

- `.env.example`: path ×1. PR 1: comment only.
- `DECISIONS.md`: dotted name ×12, path ×9, scanner ×3, test dir ×2. PR 3: split into docs/decisions/, content unchanged.
- `README.md`: path ×5, scanner ×3. PR 1 and PR 2: commands; PR 3: tree.
- `pyproject.toml`: dotted name ×1, path ×5, project name ×1, test dir ×4. PR 1: name, packages, package-data, testpaths, ruff comment.
- `requirements-studio.lock`: project name ×15. PR 1: regenerated; only the `via datafeed (pyproject.toml)` annotations change.
- `requirements.lock`: project name ×15. PR 1: regenerated; only the `via datafeed (pyproject.toml)` annotations change.

#### `.github` (1)

- `.github/workflows/ci.yml`: scanner ×2. PR 1: ruff paths; PR 2: selftest command, scanner and levels lint, mypy.

#### `agents` (71)

- `agents/DESIGN.md`: dotted name ×8, path ×28, scanner ×1, test dir ×20. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/README.md`: dotted name ×12, import ×4, path ×9, test dir ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/REPORT.md`: path ×11, test dir ×5. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/__init__.py`: path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/agents.example.toml`: dotted name ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/config.example.toml`: path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/config.py`: import ×3, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/discussion/README.md`: path ×1, test dir ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/discussion/__init__.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/discussion/node.py`: import ×4. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/discussion/rule.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/discussion/subgraph.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/epoch.py`: dotted name ×3, import ×6, path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/evidence.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/experiments/README.md`: path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/features.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/graph.py`: import ×17, path ×4. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/hashing.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/README.md`: dotted name ×2, path ×2, test dir ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/__init__.py`: import ×7. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/__main__.py`: dotted name ×3, import ×9, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/agent.py`: import ×7, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/anthropic_client.py`: import ×1, path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/budget.py`: import ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/call.py`: import ×6. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/client.py`: path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/fake.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/ledger.py`: import ×3, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/parse.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/pricing.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/llm/request.py`: import ×4. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/meta/README.md`: dotted name ×1, path ×1, test dir ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/meta/__init__.py`: import ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/meta/node.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/meta/rules.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/nodes/__init__.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/nodes/gate.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/nodes/risk.py`: import ×1, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/owner_config.py`: dotted name ×7, import ×2, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prereg.py`: path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/README.md`: dotted name ×5, path ×5, test dir ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/__init__.py`: import ×3, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/__main__.py`: dotted name ×3, import ×3, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/files.py`: import ×1, path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/output_schema.py`: import ×1, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/placeholders.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompting/render.py`: import ×5, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/bear.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/bull.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/discussion.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/gex.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/sentiment.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/technical.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/prompts/visual.md`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/resample.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/schemas.py`: import ×1, path ×4. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/snapshot.py`: import ×8, path ×4. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/store/__init__.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/store/base.py`: dotted name ×1, import ×1, path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/store/memory.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/store/sqlite.py`: import ×3, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/technical/README.md`: path ×2, test dir ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/technical/__init__.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/technical/metrics.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/trigger.py`: import ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/voters/__init__.py`: import ×5. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/voters/base.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/voters/gex.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/voters/sentiment.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/voters/technical.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `agents/voters/visual.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.

#### `backend` (4)

- `backend/README.md`: dotted name ×1, scanner ×9. PR 2: moves into `backend/scanner/` (README rewritten).
- `backend/deterministic_scanner.py`: import ×4, scanner ×5. PR 1: its lazy imports of datafeed/scoring renamed; PR 2: split into `backend/scanner/` and `backend/levels/`.
- `backend/journal_summary.py`: scanner ×3. PR 2: moves into `backend/scanner/` (README rewritten).
- `backend/services/tws_connector.py`: scanner ×1. PR 2: moves into `backend/scanner/` (README rewritten).

#### `datafeed` (19)

- `datafeed/README.md`: dotted name ×7, path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/__init__.py`: path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/bus.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/features/DECISIONS.md`: path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/features/__init__.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/features/models/sigma_har_parquet2021_2023_v1.json`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/features/models/sigma_har_txt2007_2023_v1.json`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/features/vol_block.py`: import ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/ingest/__init__.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/ingest/__main__.py`: dotted name ×3, import ×5, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/ingest/base.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/ingest/ibkr.py`: import ×1, path ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/ingest/xauusd_bars.py`: import ×2, scanner ×5. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/recorder/__init__.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/recorder/__main__.py`: dotted name ×2, import ×4. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/recorder/recorder.py`: import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/store/__init__.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/store/query.py`: import ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `datafeed/store/schema.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.

#### `deploy` (7)

- `deploy/datafeed/docker-compose.yml`: dotted name ×2. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.
- `deploy/scanner/Dockerfile`: scanner ×14. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.
- `deploy/scanner/backup.sh`: scanner ×2. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.
- `deploy/scanner/docker-compose.yml`: dotted name ×2, path ×1, scanner ×1. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.
- `deploy/scanner/entrypoint.sh`: scanner ×2. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.
- `deploy/scanner/restore.sh`: scanner ×1. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.
- `deploy/scanner/watchdog.sh`: import ×1. PR 1: datafeed/scoring/risk paths and module names; PR 2: scanner paths and entry.

#### `docs` (13)

- `docs/AGENTS_ARCHITECTURE.md`: dotted name ×11, path ×14. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/AGENTS_TECHNICAL_DESIGN.md`: dotted name ×1, path ×10, test dir ×1. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/AGENT_LLM_SCANNER_DESIGN.md`: dotted name ×1, path ×4. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/ARCHITECTURE_CURRENT.md`: dotted name ×11, path ×26, scanner ×8, test dir ×2. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/ARCHITECTURE_MAP.md`: path ×14, scanner ×14. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/CLEANUP_NOTES.md`: dotted name ×1, path ×5, scanner ×8. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/DATAFEED_NOTES.md`: dotted name ×3, path ×3, scanner ×8. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/DEPLOY_PLAN.md`: dotted name ×2, path ×1. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/DEPLOY_RUNBOOK.md`: scanner ×3. PR 1 and PR 2: commands; PR 2: new section 'Deploying the restructure'.
- `docs/LANGGRAPH_STUDIO.md`: dotted name ×1, path ×2, test dir ×2. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/PROMPT_GUIDE.md`: dotted name ×14, path ×7. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/REPO_MAP.md`: path ×1, scanner ×18. PR 3: links fixed (current docs) or moved to docs/history/ as written.
- `docs/SCANNER_SWITCH_NOTES.md`: dotted name ×6, path ×3, scanner ×1. PR 3: links fixed (current docs) or moved to docs/history/ as written.

#### `research` (1)

- `research/README.md`: path ×1. no change (study record, kept as written).

#### `research/agents_v1` (4)

- `research/agents_v1/DECISIONS.md`: scanner ×1. no change (study record, kept as written).
- `research/agents_v1/PREREG.md`: dotted name ×1, path ×4. no change (study record, kept as written).
- `research/agents_v1/README.md`: path ×1. no change (study record, kept as written).
- `research/agents_v1/expected_exclusions.py`: dotted name ×1, import ×1, scanner ×6. PR 1: import line; PR 2: imports `backend.scanner` instead of loading the file.

#### `research/level_target` (1)

- `research/level_target/SPEC.md`: dotted name ×3, scanner ×1. no change (study record, kept as written).

#### `research/sigma_compare` (7)

- `research/sigma_compare/DECISIONS.md`: dotted name ×1. no change (study record, kept as written).
- `research/sigma_compare/PREREG.md`: dotted name ×2, path ×2. no change (study record, kept as written).
- `research/sigma_compare/PROPOSAL.md`: path ×2. no change (study record, kept as written).
- `research/sigma_compare/har_sigma.py`: import ×2, path ×1. PR 1: import lines only.
- `research/sigma_compare/posthoc.py`: import ×1. PR 1: import lines only.
- `research/sigma_compare/run_sigma.py`: import ×1. PR 1: import lines only.
- `research/sigma_compare/test_sigma.py`: import ×2. PR 1: import lines only.

#### `risk` (5)

- `risk/README.md`: dotted name ×2, import ×3, path ×3, test dir ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `risk/__init__.py`: path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `risk/_check.py`: moves with its directory. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `risk/bracket.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `risk/sizing.py`: import ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.

#### `scoring` (4)

- `scoring/DECISIONS.md`: dotted name ×4, path ×3, test dir ×1. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `scoring/README.md`: dotted name ×2, import ×2, path ×2, test dir ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `scoring/__init__.py`: path ×2. PR 1: moves to `backend/`; imports, module names and relative links renamed.
- `scoring/outcome.py`: dotted name ×1, import ×3. PR 1: moves to `backend/`; imports, module names and relative links renamed.

#### `tests/agents_tests` (26)

- `tests/agents_tests/agents_fakes.py`: import ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/agents_testkit.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/conftest.py`: path ×1, test dir ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/owner_config_ok.toml`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/owner_config_tests.toml`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_acceptance.py`: dotted name ×3, import ×9. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_agent_store.py`: dotted name ×1, import ×8. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_agents_package.py`: dotted name ×1, import ×1, path ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_anthropic_client.py`: import ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_discussion.py`: import ×7. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_epoch.py`: dotted name ×3, import ×9. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_four_hour.py`: import ×5. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_graph.py`: import ×8. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_live_mode.py`: dotted name ×1, import ×12, path ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_llm_agent.py`: dotted name ×2, import ×12. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_llm_discussion.py`: import ×9. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_llm_layer.py`: dotted name ×1, import ×14. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_lock.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_meta.py`: import ×3, path ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_metrics.py`: dotted name ×2, import ×9. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_owner_config.py`: dotted name ×13, import ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_prompt_render.py`: dotted name ×1, import ×6. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_prompting.py`: dotted name ×2, import ×3. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_schemas.py`: import ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_snapshot.py`: dotted name ×1, import ×11. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/agents_tests/test_studio.py`: dotted name ×1, import ×4, test dir ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.

#### `tests/datafeed` (11)

- `tests/datafeed/test_bus.py`: import ×2. PR 1: imports and paths renamed.
- `tests/datafeed/test_bus_undecodable.py`: import ×5. PR 1: imports and paths renamed.
- `tests/datafeed/test_end_to_end.py`: import ×5. PR 1: imports and paths renamed.
- `tests/datafeed/test_features_sigma_har.py`: dotted name ×1, import ×2. PR 1: imports and paths renamed.
- `tests/datafeed/test_features_vol_block.py`: dotted name ×2, import ×3. PR 1: imports and paths renamed.
- `tests/datafeed/test_package.py`: path ×1. PR 1: imports and paths renamed.
- `tests/datafeed/test_recorder.py`: import ×5. PR 1: imports and paths renamed.
- `tests/datafeed/test_sources.py`: dotted name ×2, import ×3, path ×1. PR 1: imports and paths renamed.
- `tests/datafeed/test_spread_capture.py`: import ×5, scanner ×1. PR 1: imports and paths renamed.
- `tests/datafeed/test_store.py`: import ×3. PR 1: imports and paths renamed.
- `tests/datafeed/test_xauusd_bars.py`: import ×2, scanner ×3. PR 1: imports and paths renamed.

#### `tests/deploy` (5)

- `tests/deploy/deploy_stubs.py`: scanner ×2. PR 1: imports and paths renamed.
- `tests/deploy/test_backup.py`: dotted name ×4, import ×1, path ×1. PR 1: imports and paths renamed.
- `tests/deploy/test_compose.py`: dotted name ×3, import ×1, scanner ×5. PR 1: imports and paths renamed.
- `tests/deploy/test_ibkr_duration_check.py`: import ×1. PR 1: imports and paths renamed.
- `tests/deploy/test_watchdog.py`: dotted name ×1, import ×2. PR 1: imports and paths renamed.

#### `tests/risk_tests` (4)

- `tests/risk_tests/test_bracket.py`: dotted name ×1, import ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/risk_tests/test_import_rules.py`: dotted name ×3, path ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/risk_tests/test_risk_package.py`: path ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/risk_tests/test_sizing.py`: dotted name ×1, import ×1. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.

#### `tests/scanner` (14)

- `tests/scanner/conftest.py`: scanner ×6. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/scanner_helpers.py`: import ×3, scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_bar_source.py`: import ×1, scanner ×2. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_downtime.py`: import ×2, scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_heartbeat.py`: scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_history_fill.py`: import ×1, scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_journal_epoch.py`: scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_journal_outcomes.py`: scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_journal_replies.py`: scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_journal_summary.py`: scanner ×5. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_parity.py`: import ×4, scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_staleness.py`: import ×2, scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_stream_consume.py`: import ×1, scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.
- `tests/scanner/test_warmup.py`: scanner ×1. PR 1: imports renamed; PR 2: imports `backend.scanner` instead of loading the file.

#### `tests/scoring_tests` (2)

- `tests/scoring_tests/test_outcome.py`: dotted name ×1, import ×8. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/scoring_tests/test_scoring_package.py`: dotted name ×1, path ×2. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.

#### `tests/status_tests` (8)

- `tests/status_tests/conftest.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/status_helpers.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/test_status_config.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/test_status_evaluate.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/test_status_io.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/test_status_lock.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/test_status_probes.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.
- `tests/status_tests/test_status_tui.py`: moves with its directory. PR 1: directory renamed to `tests/<package>/`; imports and paths renamed.

#### `tools` (9)

- `tools/langgraph_studio/README.md`: test dir ×2. PR 1: imports, commands and paths renamed.
- `tools/langgraph_studio/studio.py`: dotted name ×1, import ×11, path ×1, test dir ×1. PR 1: imports, commands and paths renamed.
- `tools/sigma_calibration/RESULT.md`: path ×1. PR 1: imports, commands and paths renamed.
- `tools/sigma_calibration/calibrate.py`: import ×2, path ×1. PR 1: imports, commands and paths renamed.
- `tools/spread_by_hour.py`: import ×2. PR 1: imports, commands and paths renamed.
- `tools/status/README.md`: test dir ×1. PR 1: imports, commands and paths renamed.
- `tools/status/probes/journal_stats.py`: import ×1. PR 1: imports, commands and paths renamed.
- `tools/status/probes/stream_stats.py`: import ×1. PR 1: imports, commands and paths renamed.
- `tools/status/requirements-status.lock`: test dir ×1. PR 1: imports, commands and paths renamed.
