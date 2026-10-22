# Scanner bar-source switch: notes for the deployment PR

> **Paths are from before the restructure** (#31, #32): `backend/deterministic_scanner.py` is now the `backend/scanner/` package (`python -m backend.scanner`), and `datafeed` is `backend/datafeed`; see [ARCHITECTURE_CURRENT.md](ARCHITECTURE_CURRENT.md) §0. It stays here because [DEPLOY_RUNBOOK.md](DEPLOY_RUNBOOK.md) cites its §4 and §5c.

Branch `feat/scanner-stream-source` (from `main` @ 9042270). **Nothing is
deployed.** The deployment itself (compose stack, entrypoint, backups,
watchdog, runbook) is on `feat/deploy-stack`: see `docs/DEPLOY_RUNBOOK.md`. This branch doesn't touch the EC2 host, `deploy/` or the running
stack. These notes are for the PR that deploys it.

`backend/deterministic_scanner.py` can now take its live bars from the
datafeed stream `bars.xauusd.1m` instead of its own IBKR connection:

| `SCANNER_BAR_SOURCE` | Live path | Bars from |
|---|---|---|
| `ibkr` (default, also when unset) | `run_live`, the rollback | its own IBKR connection (clientId 11, history 21) |
| `stream` | `run_live_stream` | `bars.xauusd.1m` via `datafeed.bus`, consumer group `scanner`. **Never connects to IBKR**: the datafeed feed is the only client of the production gateway. |

Any other value stops the scanner at startup with an error. `--date` and
`--selftest` ignore the switch.

## 1. What stream mode does

1. **Warm-up** (`stream_warmup`).
   - It reads the datafeed store with `datafeed.store.query.bars_before`: one
     bar per minute, the live bar preferred and the history bar as fallback.
   - The window is `[warmup_start(now), now)`: 10 days, moved back to Monday
     00:00 UTC of the previous ISO week when that is earlier (see 5c).
   - The store is used only if it holds ≥ 80 % of the window's market-open
     minutes **and** its newest bar ended ≤ 5 market-open minutes ago.
   - There is **no IBKR fallback** (owner decision 2026-10-14). While the
     store is unusable (missing, too thin or stale), `wait_for_store` reads it
     again every `STORE_RETRY_S` = 30 s and logs the reason each time:
     `warm-up: <reason>; scanner not started, retrying in 30s`.
   - Once the store has been unusable for `STREAM_STALE_MIN` = 10 market-open
     minutes since the wait began, Telegram gets one message. When it becomes
     usable after that, a second message says the scanner is starting.
     Weekends and the daily break don't count.
   - Then the log shows `warm-up via store: ...`.
   - While it waits, the scanner isn't running: no alerts, no Telegram reply
     polling. Replies sent meanwhile are read once it starts, and their
     decision time is when they're read. The ibkr path has the same behaviour
     during a restart.
2. **Levels**: `LiveLevels`, the same class as the ibkr path (it includes the
   start-up fix, 5b).
3. **Consumption** (`StreamFeed`).
   - Every entry is classified once:
     - `invalid_msg`
     - `wrong_source`: only `source=ibkr` is accepted, history entries
       included.
     - `history_filled`: `history=true` for a minute without a bar, inside
       the warm-up window. It is added to the levels' bars
       (`LiveLevels.fill`), and the levels are rebuilt once per batch
       (`refresh`). It never reaches the Scanner, so it never alerts (5e).
     - `history_skipped`: `history=true` for a minute that already has a bar
       (the live bar is kept).
     - `invalid_bars`
     - `before_warmup`
     - `duplicate`
     - `conflict`: same minute, different OHLC; the first bar is kept.
     - `out_of_order`: dropped.
     - `live_bars`: only these reach the level engine.
   - A live bar that skips market-open minutes is logged as a gap. That's a gap
     in live coverage, logged even when history later fills those minutes.
   - A touch made only on history bars is never sent. It is journaled as
     `missed_history` (5e), however it surfaces: flushed by a live bar
     (`history_touch_suppressed`), or derived by a rebuild over the filled bars
     (`LiveLevels.history_touches`).
   - An entry is acked only after its alerts are journaled and sent.
   - Alerts go through `AlertSink` (both bar sources, 5d):
     - journaled once, so a bar redelivered after a crash doesn't resend its
       alert;
     - an alert id is never reused within an epoch;
     - touches made while no scanner was running are journaled as
       `missed_while_down`, never sent.
4. **Staleness**: no new live bar for `STREAM_STALE_MIN` = 10 market-open
   minutes sends one Telegram message. The next live bar sends one recovery
   message. Weekends and the daily break don't count. The ibkr path keeps its
   30-min `SILENCE_WARN_S` heartbeat.
5. **Journal**: alerts get the process's epoch and `bar_source='stream'`. The
   first stream start after ibkr opens a new epoch (see §4).
6. **Telegram replies, MISSED sweep, counterfactual sweep**: same as
   `run_live`. Both paths share `_handle_reply`. A reply to an id that exists
   in several epochs means the current epoch's alert.

Expected differences from the ibkr path:
- The bar contract deviations #1–#7 in `docs/DATAFEED_NOTES.md`: no stale-close
  fallback, late ticks dropped, a timer close at minute end + 2 s, and so on.
  Live bars, and so a few alerts, can differ slightly.
- The longer warm-up window (5c).

## 2. What the compose stack needs

The current `deploy/scanner/docker-compose.yml` runs `ibgateway` and `scanner`.
Stream mode needs three more services and some image changes.

| Service | What | Notes |
|---|---|---|
| `redis` | `redis:7.4.x` | AOF on a volume, `noeviction` (see the compose plan below). Healthcheck `redis-cli ping`. |
| `feed` | `python -m datafeed.ingest`, `DATAFEED_SOURCE=ibkr` | **The only client of the production gateway** in stream mode (5f). `IB_HOST=ibgateway`, `IB_PORT=4004`, `IB_CLIENT_ID` unused elsewhere (taken: 11 and 21 by the ibkr-mode scanner, 996 by the watchdog probe, 999 by the entrypoint probe; e.g. 31). `restart: unless-stopped` (crash-only). |
| `recorder` | `python -m datafeed.recorder` | `DATAFEED_DB=/data/datafeed.sqlite` on a `datafeed_store` volume, `DATAFEED_RECORDER_CONSUMER=recorder-1` |
| `scanner` | as today, plus the env below | mount `datafeed_store` (see below) |

**Compose plan** (owner decisions 2026-10-14; for the deployment PR, not
applied to `deploy/` here):

```yaml
services:
  redis:
    image: redis:7.4.2-alpine
    command: ["redis-server", "--appendonly", "yes", "--appendfsync", "everysec",
              "--maxmemory", "256mb", "--maxmemory-policy", "noeviction"]
    volumes: [redis_data:/data]          # AOF lives here
    healthcheck: {test: ["CMD", "redis-cli", "ping"], interval: 5s, retries: 10}
    restart: unless-stopped
  feed:                                  # the only client of the production gateway
    command: ["python", "-m", "datafeed.ingest"]
    environment: {DATAFEED_SOURCE: ibkr, REDIS_URL: "redis://redis:6379/0",
                  IB_HOST: ibgateway, IB_PORT: "4004", IB_CLIENT_ID: "31"}
    depends_on:
      redis: {condition: service_healthy}
      ibgateway: {condition: service_started}   # the feed itself is crash-only on a down gateway
    healthcheck:                         # healthy once it has published (it starts with 10 D of history)
      test: ["CMD", "python", "-c", "import os,redis,sys; sys.exit(0 if redis.Redis.from_url(os.environ['REDIS_URL']).xlen('bars.xauusd.1m') else 1)"]
      interval: 15s
      retries: 20
      start_period: 60s
    restart: unless-stopped
  recorder:
    command: ["python", "-m", "datafeed.recorder"]
    environment: {REDIS_URL: "redis://redis:6379/0", DATAFEED_DB: /data/datafeed.sqlite,
                  DATAFEED_RECORDER_CONSUMER: recorder-1}
    volumes: [datafeed_store:/data]
    depends_on: {feed: {condition: service_healthy}}
    healthcheck:                         # healthy once the store has bars
      test: ["CMD", "python", "-c", "import sqlite3,sys; c=sqlite3.connect('file:/data/datafeed.sqlite?mode=ro', uri=True); sys.exit(0 if c.execute('SELECT COUNT(*) FROM bars').fetchone()[0] else 1)"]
      interval: 15s
      retries: 20
      start_period: 30s
    restart: unless-stopped
  scanner:
    environment: {SCANNER_BAR_SOURCE: stream, REDIS_URL: "redis://redis:6379/0",
                  DATAFEED_DB: /datafeed/datafeed.sqlite, SCANNER_STREAM_CONSUMER: scanner-1}
    volumes: [scanner_data:/data, datafeed_store:/datafeed]   # read-write mount, see "Store mount"
    depends_on: {recorder: {condition: service_healthy}}
volumes: {redis_data: {}, datafeed_store: {}, scanner_data: {}}
```

- **Start order**: redis → feed → recorder → scanner, each gated on the
  previous service's healthcheck. The healthchecks only gate the first start.
  - After that, every process handles its own dependencies: the feed and
    recorder are crash-only, and the scanner waits for the store.
  - So a later restart of one service doesn't need the others restarted.
- **Redis sizing**: the stream is trimmed to `DEFAULT_MAXLEN` = 100k entries
  (`XADD MAXLEN ~`).
  - Measured on Redis 7.4.2: 100,008 bar entries of 294 bytes take 33.8 MB
    (`MEMORY USAGE`; `used_memory` 31.6 MB).
  - `maxmemory 256mb` leaves room for the consumer groups' pending lists and
    the AOF rewrite. Stream trimming controls the size, not eviction.
  - With `noeviction`, Redis refuses writes when full: an `XADD` fails, so
    the feed exits (crash-only), and the scanner's staleness message fires
    after 10 market-open minutes.
- **AOF `everysec`**: at most ~1 s of entries is lost on a crash. The pending
  lists and both groups' positions survive a Redis restart.

**Scanner image** (`deploy/scanner/Dockerfile`):
- Add the `datafeed` package and `redis`: `COPY pyproject.toml` and
  `datafeed/`, then `pip install --no-deps .` and `redis==5.2.1`.
  - `ib_insync` stays: the ibkr mode (the rollback) and the entrypoint probe
    use it. Stream mode doesn't import it.
  - `pandas` and `tzdata` are already there.
  - `pyarrow` is only needed for `--date` and `--selftest`.
- Python 3.12 is fine (`requires-python >= 3.12`).

**Store mount**: the scanner opens the store read-only (`mode=ro`). The store
is in WAL mode, and a WAL reader needs to write the `-shm` file. So mount the
volume **read-write** and rely on `mode=ro`, or verify a `:ro` mount on the
host before relying on it. This hasn't been tested here, because Docker isn't
installed on this machine.

**Entrypoint** (`deploy/scanner/entrypoint.sh`): today it waits for the gateway
API with a handshake (clientId 999), then runs `--live`.
- **It must change for stream mode**. Otherwise the scanner still opens
  gateway connections, and its start still depends on the gateway.
- When `SCANNER_BAR_SOURCE=stream`, wait for Redis instead
  (`redis-cli -u $REDIS_URL ping`, or a Python `redis` ping). The store needs
  no wait: `wait_for_store` handles it.

**Watchdog** (`deploy/scanner/watchdog.sh`):
- It checks the gateway and scanner restarts.
- In stream mode, a dead feed is reported by the scanner's own staleness
  message. A dead Redis makes the scanner crash-loop, which the watchdog's
  restart counter reports.
- Nothing watches the recorder directly. Its lag only matters at a scanner
  restart, where a stale store makes the scanner wait. After 10 market-open
  minutes that wait sends a Telegram message.
- The watchdog's gateway probe (clientId 996, a handshake every 5 minutes) is
  monitoring, not a bar client. It is the one gateway connection besides the
  feed.
- Consider adding `feed` and `recorder` restart counts.

## 3. Environment

| Variable | Mode | Meaning |
|---|---|---|
| `SCANNER_BAR_SOURCE` | both | `ibkr` (default) or `stream` |
| `REDIS_URL` | stream | the bus, e.g. `redis://redis:6379/0` |
| `DATAFEED_DB` | stream | store path, e.g. `/datafeed/datafeed.sqlite`. Unset or unreadable means the scanner waits (§1, point 1), logged. |
| `SCANNER_STREAM_GROUP` | stream | consumer group, default `scanner`. Only a parallel dry run uses another one. |
| `SCANNER_STREAM_CONSUMER` | stream | consumer name in group `scanner`, default `scanner-1`. **Keep it stable across restarts**: unacked entries are redelivered to the same name. |
| `SCANNER_DB` | both | journal (unchanged) |
| `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` | ibkr | as today; stream mode doesn't read them |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | both | unchanged (not needed with `--dry-live`) |

Tunables are named constants in the scanner: `WARMUP_DAYS`,
`WARMUP_MIN_COVERAGE`, `WARMUP_MAX_STALE_MIN`, `STORE_RETRY_S`, `STREAM_STALE_MIN`,
`STREAM_BATCH` and `STREAM_BLOCK_MS`.

## 4. Journal migration and epochs (both modes)

- **Migration.** The first `open_log` of the new code migrates the journal,
  **including in ibkr mode**. It runs in numbered steps, one transaction each,
  recorded in `PRAGMA user_version`:
  1. `alerts` gets `epoch` and `bar_source`; `decisions`, `outcomes` and
     `counterfactuals` get `epoch`. Existing rows get epoch 1 and
     `bar_source='ibkr'`.
  2. `source_switches(switch_ts_utc, from_source, to_source, epoch)`. A journal
     that already has alerts gets one row for its state at migration time:
     `(now, NULL, 'ibkr', 1)` for the production journal.
  3. `alerts.status`: `'sent'` for every existing row, `'missed_while_down'`
     or `'missed_history'`. Also
     `scanner_state(key, value, updated_ts_utc)`, holding `last_live_bar_ts`:
     the newest bar any live scanner processed, written after each live bar's
     alerts.
- **Alert status.** A `missed_while_down` or `missed_history` alert was never
  shown to the human.
  - It gets counterfactuals.
  - It never gets an auto-MISSED decision.
  - A Telegram reply can't decide it ("unknown alert_id").
  - In the TAKE/SKIP analysis, count it as coverage lost, not as a decision.
- **Epochs** (owner decision 2026-10-14): **every bar-source change starts a
  new epoch, and epochs are never reused.**
  - At start, each live path calls `start_epoch(con, source)`:
    - same source as the latest `source_switches` row: that epoch continues
      (a restart);
    - otherwise: a new epoch, one above every epoch in the journal, and a row
      `(now, previous source, source, epoch)`.
  - For the production journal:
    - ibkr (today) = 1;
    - the switch to stream = 2;
    - a rollback to ibkr = 3, never 1;
    - stream again = 4.
  - `--date --db` replay into a journal is its own epoch too
    (`bar_source='parquet'`).
- **One journal, one live scanner.** Two processes on the same journal with
  different sources would flip epochs back and forth. Any parallel
  `--dry-live` comparison run must use its own `SCANNER_DB` (§6, step 3).
- **Same id in two epochs.** Alert ids are numbered per day from the bars, so
  on a switch day two epochs can use the same id.
  - Child rows carry their alert's epoch, and the sweeps join on
    `(alert_id, epoch)`.
  - A Telegram reply means the current epoch's alert, else the newest one with
    that id.

**The pre-migration code cannot write to a migrated journal.** Its INSERTs are
positional: "table alerts has 11 columns but 8 values were supplied". A test
pins this. So:
- Back up the journal before the first deploy, e.g.
  `sqlite3 /data/scanner_decisions.sqlite ".backup /data/pre-epoch.sqlite"`.
- Roll back with the switch, not with an older image.
- If an older image is ever unavoidable, either restore the backup (rows written
  since are lost) or drop the columns (SQLite ≥ 3.35):
  `ALTER TABLE alerts DROP COLUMN status;`,
  `ALTER TABLE alerts DROP COLUMN bar_source;`,
  `ALTER TABLE <t> DROP COLUMN epoch;` for all four tables,
  `DROP TABLE source_switches; DROP TABLE scanner_state;`, then
  `PRAGMA user_version = 0;`.

## 5. Open decisions (owner)

- **a. Parity reference: decided 2026-10-14.**
  - The reference is the continuous replay, the same one selftest case 10
    uses. Stream mode equals it exactly on all 8 days, on both buses.
  - `--date` replay starts each day with a fresh `Scanner`. On 2025-07-04 it
    alerts July 3's `ASIA_L` 3341.648 and `LONDON_L` 3342.865 again at 04:16
    and 04:23. Both were touched on July 3 (12:30, 12:08).
  - `test_date_replay_difference_2025_07_04` asserts exactly that
    difference, and its cause.
- **b. Start-up bug: fixed in both paths, 2026-10-14.**
  - The bug: `LiveLevels` started between 00:00 UTC and the day's first bar
    didn't rebuild on that bar. The day's DOPEN, WOPEN, PDH, PDL, PWH and PWL
    were then missing until 07:00. On 2025-06-16 that lost 3 alerts.
  - The fix: `LiveLevels` now compares against the epoch of the newest bar its
    levels were built from.
  - This changes the ibkr path too. Only a start inside that window behaves
    differently; it now matches a continuous replay.
  - Covered by selftest case 13 and `test_start_before_the_days_first_bar`.
    Both fail on the old code.
- **c. Warm-up window: decided 2026-10-14; the IBKR "10 D" meaning is still
  open.**
  - The window is 10 days, extended back to Monday 00:00 UTC of the previous
    ISO week when that is earlier (`warmup_start`). From Thursday to Sunday a
    plain 10-day window starts inside last week, and PWH/PWL would come from
    part of it.
  - **Open, not confirmable from official docs (checked 2026-10-14):** does
    IBKR's `durationStr="10 D"` count calendar days or trading days/sessions?
    - The [historical bars page](https://interactivebrokers.github.io/tws-api/historical_bars.html)
      lists the units S, D, W, M, Y. It defines the duration only as "the
      amount of time ... to go back from the request's given end date and time".
    - The [`EClient` reference](https://interactivebrokers.github.io/tws-api/classIBApi_1_1EClient.html)
      ("3 D" returns "all the available bars of the last three days") and the
      [limitations page](https://interactivebrokers.github.io/tws-api/historical_limitations.html)
      say no more.
    - The current IBKR Campus TWS API docs refuse automated fetches (HTTP 403),
      and the page copy that could be downloaded has no duration section.
  - It matters for two things:
    - the ibkr path, which builds levels from that history: if "D" means
      calendar days, PWH/PWL come from a partial week from Thursday to Sunday;
    - the feed's history, which is the store's only content before it has
      accumulated two weeks of its own bars.
  - To settle it, run once on the paper gateway, on a Thursday or Friday:
    `reqHistoricalData(XAUUSD CMDTY, "", "10 D", "1 min", "MIDPOINT", useRTH=False, formatDate=2)`
    and look at the first bar's date. Calendar days give roughly now − 10 d;
    trading days give about two weeks back.
  - Also: right after the feed first starts, the store holds only the feed's
    IBKR history. A 13-day window on Thu–Sun may then fall below 80 %
    coverage, and the scanner waits (§1, point 1). Flip on Mon–Wed, or once the
    store holds two weeks.
- **d. Touches while down: decided 2026-10-14.** They are never sent as late
  Telegram alerts. Both bar sources journal them as status
  `missed_while_down` with the process's epoch, so coverage gaps are explicit
  (`AlertSink`).
  - **At start.** The first warm-up pass's alerts are journaled as missed if
    they come from the 15-min bucket that was still open when the previous
    process stopped, or later. That means
    `alert_ts >= floor(last_live_bar_ts, 15 min)`; the previous process can't
    have sent them. On the first start on a journal (no `last_live_bar_ts`),
    nothing is recorded.
  - **Live.** An alert flushed after the restart whose earliest touch is on a
    warm-up bar (`alert_ts <= newest warm-up bar`) is journaled as missed, not
    sent. Example: a touch while down, in a bucket that closes after the
    restart.
  - An alert already journaled (same id, time and types, any epoch) is not
    repeated. This covers a crash between the journal write and the progress
    write.
  - Edge case: a bucket can combine a touch from before the restart with a
    touch at the same price after it, into one alert. It is dated by the
    earlier touch, so it is journaled as missed: the arrival at that level
    happened while down.
  - `test_restart_coverage_is_complete_and_nothing_is_sent_late` checks
    2025-06-16 with the scanner down 07:05–07:13 and 07:05–09:00. The alerts
    sent before, the alerts sent after, and the alerts journaled as missed
    together equal one continuous run exactly, and nothing touched before the
    restart is sent.
  - Not covered: a touch on a live bar the scanner processed whose alert was
    lost to a crash *and* whose bar the warm-up never re-derives. That can't
    happen with the store warm-up (it re-derives every bar), only if the
    warm-up window is shorter than the downtime (> 10–13 days).
- **e. History-flagged bars: decided 2026-10-14.** They fill gaps for levels
  and warm-up only, and never trigger an alert.
  - **Filling.** A history bar for a minute without a bar, inside the warm-up
    window, joins the levels' bars. A minute that already has a bar keeps it.
  - **Rebuild.** After a batch with fills, the levels and first-touch state are
    rebuilt once from all bars before the newest live bar. A level touched only
    on a history bar is consumed silently, like a touch during warm-up.
  - **Journaled as `missed_history`** (owner decision 2026-10-14), like
    `missed_while_down`: with epoch, never sent, never decidable,
    counterfactuals computed. There are two paths:
    - an alert dated on a filled minute that a live bar flushes;
    - a touch a rebuild over the filled bars derives: the refresh after the
      fills, and every later midnight or session-close rebuild.
    Every rebuild derives the same touch again, possibly under another id, so
    a `missed_history` row is identified by its touch (time, types, price)
    within the epoch.
    `test_three_rebuilds_give_exactly_one_missed_history_row` covers both
    paths: each touch is handed over at least 3 times and journaled once.
  - **Ids.** A rebuild over fills can renumber a day's alerts, so `AlertSink`
    never reuses an id within an epoch: a taken id gets the day's next free
    number, which is logged.
  - **Tests.** In `test_history_fill.py`, 2025-06-16's first 31 or 25 minutes
    arrive only as history. The day's first three touches are never alerted;
    DOPEN, ASIA_H and ASIA_L include the filled bars; every other alert equals
    a continuous replay.
- **f. Gateway rule: decided 2026-10-14.**
  - With stream mode, the feed is the **only** client of the production
    gateway. The scanner in stream mode never connects: it has no IBKR
    fallback and waits for the store instead (§1, point 1).
  - Development and tests still use a local paper gateway only.
  - Updated in `datafeed/README.md`, `datafeed/ingest/ibkr.py`,
    `datafeed/ingest/__main__.py`, `deploy/datafeed/docker-compose.yml` and
    `docs/DATAFEED_NOTES.md`.
  - A test checks that the stream path references no IBKR code.
- **g. Redis persistence: decided 2026-10-14.** `appendonly yes`, `appendfsync
  everysec` on a volume, `maxmemory-policy noeviction`. Size comes from stream
  trimming (see the compose plan in §2).
- **h. Epoch on rollback: decided 2026-10-14.** Every bar-source change starts a
  new epoch: a rollback is epoch 3, never 1 again. Each change is logged in
  `source_switches` (§4).
- **i. First start of the `scanner` group.** It reads the whole stream from the
  beginning: up to `DEFAULT_MAXLEN` = 100k entries, about 70 days. The scanner
  classifies them as backlog and acks them, with one summary line per batch of
  500. That's a one-off cost of probably tens of seconds; it hasn't been
  measured on the server.

## 6. How to flip the switch

1. Back up the journal (§4).
2. Deploy the new image with `SCANNER_BAR_SOURCE=ibkr` (or unset).
   - The journal migrates.
   - Behaviour is otherwise unchanged; for a day, check that alerts and replies
     look as before.
3. Bring up `redis`, `feed` and `recorder`.
   - Check `recorder` health lines: `lag_s` is small and `last_live_bar_ts`
     advances.
   - Optional: run a second scanner with `--dry-live` and
     `SCANNER_BAR_SOURCE=stream`, with **its own `SCANNER_STREAM_GROUP` (e.g.
     `scanner-dry`; consumers of one group split the entries, so another
     consumer name alone would take bars from production), its own
     `SCANNER_DB` (never the production journal, §4) and no Telegram**.
     `docs/DEPLOY_RUNBOOK.md` has the command.
     Compare its printed alerts with the live ones.
4. Set `SCANNER_BAR_SOURCE=stream` for `scanner` and add `REDIS_URL`,
   `DATAFEED_DB` and `SCANNER_STREAM_CONSUMER`. Then run
   `docker compose up -d scanner`.
5. Check the scanner log:
   - `warm-up via store: ...`. If you see `warm-up: ...; scanner not started,
     retrying in 30s` instead, the reason is on the same line (usually the
     recorder is behind or the store is still too thin);
   - `scanning XAUUSD from stream bars.xauusd.1m (group scanner, consumer ...),
     ... journal epoch 2`. `source_switches` then has the row
     `(ts, 'ibkr', 'stream', 2)`;
   - after the first-start backlog summary, no `out_of_order` or `conflict`
     lines.
6. Within 10 market-open minutes of stopping `feed` (as a test), Telegram gets
   the staleness message, and the recovery message after it restarts.

## 7. How to roll back

1. Set `SCANNER_BAR_SOURCE=ibkr`, then run `docker compose up -d scanner`.
   - That's the whole rollback. `run_live` is the IBKR path, reconnecting with
     clientId 11 and 21.
   - It writes a **new** epoch, 3 after a first stream period, and logs
     `(ts, 'stream', 'ibkr', 3)` in `source_switches`.
   - `feed` (clientId 31) can stay up; it doesn't conflict. While it runs,
     the gateway has two bar clients again.
2. The `scanner` group in Redis keeps its position and any pending entries.
   This is harmless. When stream mode is enabled again, the scanner skips that
   backlog against its warm-up.
3. Don't roll back to an image from before the epoch migration (§4).

## 8. Verification on this branch

- **Redis:** a local Redis 7.4.2 (built from source, 127.0.0.1, no persistence).
  The datafeed and scanner Redis tests run against DB 15.
- **Baseline** (`main` @ 9042270): full pytest 393 passed, 7 skipped; datafeed
  Redis tests 6 passed, 1 skipped (the manual IBKR test); selftest ALL PASSED.
- **This branch, after all owner decisions (2026-10-14):**
  - Selftest: ALL PASSED, 13 cases.
  - Full pytest with `REDIS_TEST_URL=redis://localhost:6379/15`: 537 passed,
    1 skipped (the manual IBKR test), 0 failed.
  - Without `REDIS_TEST_URL` (as the baseline): 501 passed, 37 skipped (Redis cases), 0 failed. All 393
    baseline tests pass.
  - Datafeed Redis tests: 6 passed, 1 skipped.
- **Smoke test** (`--dry-live`, stream mode, local Redis, synthetic store):
  - warm-up via the store;
  - a history entry skipped (that build ignored history entries; they now
    fill gaps, 5e) and a replay-source entry ignored;
  - a gap logged;
  - one alert journaled as epoch 2, `stream`;
  - a restart with nothing redelivered and no duplicate alert;
  - a missing store with a failing IBKR fallback: exit 1 after the backoff
    (this was the build before item 4 removed the fallback);
  - Redis down: exit 1 after the backoff;
  - a bad `SCANNER_BAR_SOURCE`: exit 1.
- **Not tested:** no IBKR gateway, no Telegram, no Docker build.
