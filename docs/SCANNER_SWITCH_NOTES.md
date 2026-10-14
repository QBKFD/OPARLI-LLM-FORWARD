# Scanner bar-source switch: notes for the deployment PR

Branch `feat/scanner-stream-source` (from `main` @ 9042270). **Nothing is
deployed.** This branch doesn't touch the EC2 host, `deploy/` or the running
stack. These notes are for the PR that deploys it.

`backend/deterministic_scanner.py` can now take its live bars from the
datafeed stream `bars.xauusd.1m` instead of its own IBKR connection:

| `SCANNER_BAR_SOURCE` | Live path | Bars from |
|---|---|---|
| `ibkr` (default, also when unset) | `run_live`, unchanged | its own IBKR connection (clientId 11, history 21) |
| `stream` | `run_live_stream` | `bars.xauusd.1m` via `datafeed.bus`, consumer group `scanner` |

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
   - Otherwise it uses the IBKR history load `run_live` uses (clientId
     `IB_CLIENT_ID + 10`), minus the forming minute.
   - The log line `warm-up via store|ibkr: ...` says which path was used and
     why.
   - If the fallback also fails, the scanner backs off 20 s and exits 1
     (crash-only).
2. **Levels**: `StreamLevels`, which is `LiveLevels` plus one start-up fix
   (see 5b).
3. **Consumption** (`StreamFeed`).
   - Every entry is classified once:
     - `invalid_msg`
     - `history_skipped`: `history=true` is never live.
     - `wrong_source`: only `source=ibkr` is accepted.
     - `invalid_bars`
     - `before_warmup`
     - `duplicate`
     - `conflict`: same minute, different OHLC; the first bar is kept.
     - `out_of_order`: dropped.
     - `live_bars`: only these reach the level engine.
   - A live bar that skips market-open minutes is logged as a gap.
   - An entry is acked only after its alerts are journaled and sent.
   - Alerts are journaled once, so a bar redelivered after a crash doesn't
     resend its alert.
4. **Staleness**: no new live bar for `STREAM_STALE_MIN` = 10 market-open
   minutes sends one Telegram message. The next live bar sends one recovery
   message. Weekends and the daily break don't count. The ibkr path keeps its
   30-min `SILENCE_WARN_S` heartbeat.
5. **Journal**: alerts are epoch 2, `bar_source='stream'` (see §4).
6. **Telegram replies, MISSED sweep, counterfactual sweep**: same as
   `run_live`. A reply to an id that exists in both epochs means the stream
   epoch's alert.

Expected differences from the ibkr path:
- The bar contract deviations #1–#7 in `docs/DATAFEED_NOTES.md`: no stale-close
  fallback, late ticks dropped, a timer close at minute end + 2 s, and so on.
  Live bars, and so a few alerts, can differ slightly.
- The `StreamLevels` start-up fix (5b) and the longer warm-up window (5c).

## 2. What the compose stack needs

The current `deploy/scanner/docker-compose.yml` runs `ibgateway` and `scanner`.
Stream mode needs three more services and some image changes.

| Service | What | Notes |
|---|---|---|
| `redis` | `redis:7.4.x` | Decide on persistence (5g). Healthcheck `redis-cli ping`. |
| `feed` | `python -m datafeed.ingest`, `DATAFEED_SOURCE=ibkr` | `IB_HOST=ibgateway`, `IB_PORT=4004`, `IB_CLIENT_ID` **unused elsewhere** (taken: 11, 21, 996 watchdog probe, 999 entrypoint probe; e.g. 31). `restart: unless-stopped` (crash-only). See 5f: the datafeed docs currently forbid the production gateway. |
| `recorder` | `python -m datafeed.recorder` | `DATAFEED_DB=/data/datafeed.sqlite` on a `datafeed_store` volume, `DATAFEED_RECORDER_CONSUMER=recorder-1` |
| `scanner` | as today, plus the env below | mount `datafeed_store` (see below) |

**Scanner image** (`deploy/scanner/Dockerfile`):
- Add the `datafeed` package and `redis`: `COPY pyproject.toml` and
  `datafeed/`, then `pip install --no-deps .` and `redis==5.2.1`.
  - `ib_insync` stays: the IBKR fallback and the entrypoint probe use it.
  - `pandas` and `tzdata` are already there.
  - `pyarrow` is only needed for `--date` and `--selftest`.
- Python 3.12 is fine (`requires-python >= 3.12`).

**Store mount**: the scanner opens the store read-only (`mode=ro`). The store
is in WAL mode, and a WAL reader needs to write the `-shm` file. So mount the
volume **read-write** and rely on `mode=ro`, or verify a `:ro` mount on the
host before relying on it. This hasn't been tested here, because Docker isn't
installed on this machine.

**Entrypoint** (`deploy/scanner/entrypoint.sh`): it waits for the gateway API,
then runs `--live`.
- That still works in stream mode: the fallback needs the gateway anyway.
- But it ties the scanner's start to the gateway, which stream mode is meant to
  remove. Suggest: when `SCANNER_BAR_SOURCE=stream`, wait for Redis instead (and
  optionally the store file), not the gateway.

**Watchdog** (`deploy/scanner/watchdog.sh`):
- It checks the gateway and scanner restarts.
- In stream mode, a dead feed is reported by the scanner's own staleness
  message. A dead Redis makes the scanner crash-loop, which the watchdog's
  restart counter reports.
- Nothing watches the recorder. Its lag only matters at a scanner restart: a
  stale store means the IBKR fallback, which is logged.
- Consider adding `feed` and `recorder` restart counts.

## 3. Environment

| Variable | Mode | Meaning |
|---|---|---|
| `SCANNER_BAR_SOURCE` | both | `ibkr` (default) or `stream` |
| `REDIS_URL` | stream | the bus, e.g. `redis://redis:6379/0` |
| `DATAFEED_DB` | stream | store path, e.g. `/datafeed/datafeed.sqlite`. Unset or unreadable means the IBKR fallback, logged. |
| `SCANNER_STREAM_CONSUMER` | stream | consumer name in group `scanner`, default `scanner-1`. **Keep it stable across restarts**: unacked entries are redelivered to the same name. |
| `SCANNER_DB` | both | journal (unchanged) |
| `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` | both | ibkr: as today. stream: only for the warm-up fallback (clientId + 10). |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | both | unchanged (not needed with `--dry-live`) |

Tunables are named constants in the scanner: `WARMUP_DAYS`,
`WARMUP_MIN_COVERAGE`, `WARMUP_MAX_STALE_MIN`, `STREAM_STALE_MIN`,
`STREAM_BATCH`, `STREAM_BLOCK_MS`, `JOURNAL_EPOCHS`.

## 4. Journal migration (happens in both modes)

- The first `open_log` of the new code migrates the journal, **including in
  ibkr mode**. It runs in one transaction and sets `PRAGMA user_version` to 1.
  - `alerts` gets `epoch` and `bar_source`.
  - `decisions`, `outcomes` and `counterfactuals` get `epoch`.
- Existing rows get epoch 1 and `bar_source='ibkr'`.
- From then on:
  - ibkr mode writes epoch 1, `'ibkr'`;
  - stream mode writes epoch 2, `'stream'`;
  - `--date --db` replay rows are `'parquet'`.
- Alert ids are numbered per day from the bars, so on a switch day both epochs
  can use the same id. Child rows carry their alert's epoch, and the sweeps join
  on `(alert_id, epoch)`.

**The pre-migration code cannot write to a migrated journal.** Its INSERTs are
positional: "table alerts has 10 columns but 8 values were supplied". A test
pins this. So:
- Back up the journal before the first deploy, e.g.
  `sqlite3 /data/scanner_decisions.sqlite ".backup /data/pre-epoch.sqlite"`.
- Roll back with the switch, not with an older image.
- If an older image is ever unavoidable, either restore the backup (rows written
  since are lost) or drop the columns (SQLite ≥ 3.35):
  `ALTER TABLE alerts DROP COLUMN bar_source;`, then
  `ALTER TABLE <t> DROP COLUMN epoch;` for all four tables, then
  `PRAGMA user_version = 0;`.

## 5. Open decisions (owner)

- **a. Parity on 2025-07-04 (test left red).**
  - `--date` replay and stream mode differ on that day. Stream mode is
    identical to a continuous replay on all 8 days.
  - The cause: `replay_day` starts each day with a fresh `Scanner`. July 3's
    `ASIA_L` 3341.648 and `LONDON_L` 3342.865 were touched on July 3 (12:30,
    12:08), so they are dead under first-touch-only. `--date` alerts them again
    on July 4 at 04:16 and 04:23.
  - Options:
    - (i) Hold stream mode to continuous replay, the same criterion as
      selftest case 10, and drop the `--date` comparison or mark it.
    - (ii) Change `replay_day` to carry state from the previous days. This
      changes `--date` output.
- **b. ibkr-path start-up bug (left in place).**
  - `LiveLevels` started between 00:00 UTC and the day's first bar doesn't
    rebuild on that bar. The day's DOPEN, WOPEN, PDH, PDL, PWH and PWL are
    missing until 07:00. On 2025-06-16 that loses 3 alerts.
  - `StreamLevels` fixes this for stream mode. The fix for `run_live` is the
    same one line. A test pins the current ibkr behaviour.
  - The window is small in production: a restart between 00:00 and the
    00:00 bar.
- **c. Warm-up window.** "Last 10 days" is implemented as 10 days, extended to
  the previous ISO week's Monday. From Thursday to Sunday a plain 10-day window
  starts inside last week, and PWH/PWL would come from part of it.
  - Related, unverified: if IBKR's `durationStr="10 D"` means 10 *calendar*
    days, the ibkr path has the same partial-week PWH/PWL from Thursday to
    Sunday. Check against the gateway.
  - Also: right after the feed first starts, the store holds only the feed's
    IBKR history. A 13-day window on Thu–Sun may then fall below 80 % coverage
    and use the IBKR fallback (logged). Flip on Mon–Wed, or once the store has
    two weeks.
- **d. Restart semantics.**
  - Touches during scanner downtime aren't alerted. The warm-up covers them
    silently, as the ibkr path does today.
  - If the scanner crashes after a bar reached the level engine but before its
    alert was journaled, and the recorder already has that bar, the alert is
    lost. The ibkr path has the same window.
  - The alternative: persist the last processed bar_ts and replay the gap with
    alerts.
- **e.** History-flagged entries are ignored. They are not merged into the
  levels' history, even when they fill minutes the scanner missed.
- **f.** `deploy/datafeed` and `datafeed/README.md` say the feed must never point
  at the production gateway. Stream mode needs exactly that. The rule has to be
  changed explicitly in the deployment PR.
- **g. Redis persistence.**
  - The stream is transport, not storage: the store is the record.
  - Without persistence, a Redis restart loses both groups' positions and
    pending entries. Both resume on a new empty stream: the scanner via its
    warm-up, the recorder from the feed.
  - `appendonly yes` keeps pending entries across a Redis restart.
- **h. Epoch on rollback.** After a rollback, ibkr mode writes epoch 1 again.
  If a stream period between two ibkr periods should split the ibkr data, use
  a new epoch (3) for ibkr after a rollback.
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
     `SCANNER_BAR_SOURCE=stream`, with **another consumer name and no
     Telegram**. Compare its printed alerts with the live ones.
4. Set `SCANNER_BAR_SOURCE=stream` for `scanner` and add `REDIS_URL`,
   `DATAFEED_DB` and `SCANNER_STREAM_CONSUMER`. Then run
   `docker compose up -d scanner`.
5. Check the scanner log:
   - `warm-up via store: ...`, not `ibkr` (if `ibkr`, the reason is on the same
     line);
   - `scanning XAUUSD from stream bars.xauusd.1m (group scanner, consumer ...)`;
   - after the first-start backlog summary, no `out_of_order` or `conflict`
     lines.
6. Within 10 market-open minutes of stopping `feed` (as a test), Telegram gets
   the staleness message, and the recovery message after it restarts.

## 7. How to roll back

1. Set `SCANNER_BAR_SOURCE=ibkr`, then run `docker compose up -d scanner`.
   - That's the whole rollback: `run_live` is the unchanged IBKR path. It
     reconnects with clientId 11 and 21 and writes epoch 1.
   - `feed` (clientId 31) can stay up; it doesn't conflict.
2. The `scanner` group in Redis keeps its position and any pending entries.
   This is harmless. When stream mode is enabled again, the scanner skips that
   backlog against its warm-up.
3. Don't roll back to an image from before the epoch migration (§4).

## 8. Verification on this branch

- **Redis:** a local Redis 7.4.2 (built from source, 127.0.0.1, no persistence).
  The datafeed and scanner Redis tests run against DB 15.
- **Baseline** (`main` @ 9042270): full pytest 393 passed, 7 skipped; datafeed
  Redis tests 6 passed, 1 skipped (the manual IBKR test); selftest ALL PASSED.
- **This branch (step 7):**
  - Selftest: ALL PASSED. It now has a 12th case, for the journal migration.
  - Full pytest without `REDIS_TEST_URL` (as the baseline): 476 passed,
    34 skipped, 1 failed.
    - All 393 baseline tests still pass.
    - `tests/scanner` adds 111 tests: 83 pass, 27 Redis cases skip, and 1 fails:
      `test_stream_mode_matches_date_replay[2025-07-04-memory]` (5a, left
      red on purpose).
  - With `REDIS_TEST_URL=redis://localhost:6379/15`: 508 passed, 1 skipped
    (the manual IBKR test), 2 failed (the same 2025-07-04 case on both buses).
  - Datafeed Redis tests: 6 passed, 1 skipped.
- **Smoke test** (`--dry-live`, stream mode, local Redis, synthetic store):
  - warm-up via the store;
  - a history entry skipped and a replay-source entry ignored;
  - a gap logged;
  - one alert journaled as epoch 2, `stream`;
  - a restart with nothing redelivered and no duplicate alert;
  - a missing store with a failing IBKR fallback: exit 1 after the backoff;
  - Redis down: exit 1 after the backoff;
  - a bad `SCANNER_BAR_SOURCE`: exit 1.
- **Not tested:** no IBKR gateway (the fallback was only tested failing), no
  Telegram, no Docker build.
