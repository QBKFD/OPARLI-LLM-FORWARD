# Datafeed v1: notes

Working notes for the `feat/datafeed-v1` branch. Step 0 records the baseline and
the **current** bar contract: what production does today, including its flaws.
The new package must reproduce this behaviour exactly unless a deviation is
listed under "Decisions" and approved.

## Gateway rule (owner decision 2026-10-14)

This replaces "local paper gateway only, never the production one" below for
production. That rule still holds for development and tests.
- With the scanner in stream mode (`SCANNER_BAR_SOURCE=stream`), the feed's
  `IbkrXauusdSource` is the **only** client of the production gateway.
- The scanner in stream mode never connects to IBKR. It has no warm-up
  fallback, and waits for the store instead. The ibkr-mode scanner, the
  rollback, keeps clientIds 11 and 21.
- Details and client ids: `datafeed/README.md` and
  `docs/SCANNER_SWITCH_NOTES.md`.

## 0. Baseline (2026-10-13, branch point `research/levels-diag` @ 29dec96)

Interpreter: `algo_project/backend/venv` (Python 3.13.1, pandas 2.3.3,
ib_insync 0.9.86). The worktree has no venv of its own.

| Check | Command | Result |
|---|---|---|
| Scanner selftest | `python backend/deterministic_scanner.py --selftest` | `SELFTEST: ALL PASSED`, exit 0 |
| research/levels | pytest | 11 passed |
| research/levels_confirm | pytest | 8 passed |
| research/levels_diag | pytest | 4 passed |
| research/regime_v2 | pytest | 9 passed |
| research/trend_features | pytest | 7 passed |
| research/vol_block | pytest | 5 passed |
| research/vol_features | pytest | 14 passed |
| research/vol_features_r2 | pytest | 9 passed |
| research/vol_features_r3 | pytest | 11 passed |
| research/test_sealing.py | pytest | 2 passed |
| All research tests together | pytest on every tracked `research/**/test_*.py` | **80 passed** |
| `docker build deploy/scanner` | n/a | **Not run: Docker is not installed on this machine** |

Without a baseline image, step 8 can't compare scanner image builds. Step 8 will
instead show that no file the image COPYs has changed (`git diff` on
`deploy/scanner/`, `backend/deterministic_scanner.py`,
`backend/services/tws_connector.py`, `backend/services/__init__.py`).

## 1. Current bar contract (production scanner)

The sources are `backend/services/tws_connector.py` (TC) and
`backend/deterministic_scanner.py` (DS). The scanner gets bars from two paths:
a one-off **history load** at startup and a **live tick stream** after that.

### 1.1 History load: `DS._load_recent_history` (DS:475–494)

- **Connection.** A separate short-lived `ib_insync.IB()` with
  `clientId = IB_CLIENT_ID + 10` (21 by default) and `readonly=True`, connect
  timeout 10 s. It disconnects in `finally`.
- **Contract.** `Contract(symbol="XAUUSD", secType="CMDTY", exchange="SMART", currency="USD")`,
  qualified, first match.
- **Request.** `reqHistoricalData(endDateTime="", durationStr="10 D",
  barSizeSetting="1 min", whatToShow="MIDPOINT", useRTH=False, formatDate=2)`.
- **Timestamp.** `b.date` from IBKR. With `formatDate=2` that's the bar
  **start (open) time**, tz-aware UTC. It's converted to UTC (or localized if
  naive), and the index is sorted.
- **Columns.** open, high, low, close as float. **No volume column is kept.**
- **Filtering.** Each row goes through `_valid_bar` (DS:561) before the level
  engine sees it.
- **Failure.** Any exception: sleep `RECONNECT_BACKOFF_S` (20 s), then
  `SystemExit(1)` (DS:555–560).
- The forming minute at load time may be included as a partial bar. IBKR's
  behaviour here hasn't been verified; listed under open questions.

### 1.2 Live stream: `TC._on_pending_tickers` (TC:361–460)

- **Subscription.** `TWSConnector(host, port, client_id=IB_CLIENT_ID (11), readonly=True)`.
  The CMDTY contract is qualified, then
  `reqMktData(contract, genericTickList='', snapshot=False)` (TC:176). Bars are
  built from `ib.pendingTickersEvent`, not `reqRealTimeBars`: CMDTY doesn't
  support real-time bars.
- **What counts as a "tick".** One entry per ticker in each
  `pendingTickersEvent` callback. The event fires on *any* field change (bid,
  ask, sizes, last, and so on), so an unchanged midpoint is still appended as
  another tick. `n_ticks` counts callback events, not price changes.
- **Price per tick** (TC:383–394):
  1. If `bid > 0` and `ask > 0` (and neither is None or NaN): `(bid + ask) / 2`.
  2. Otherwise, if `close > 0`: `ticker.close`. **This is IBKR's previous-session
     close**, a stale price. It enters the bar whenever one quote side is
     missing.
  3. Otherwise the tick is dropped. A -1, 0 or NaN quote never becomes a price.
- **Tick time.** `ticker.time` (ib_insync sets it to the receive time, tz-aware
  UTC). If that's falsy, the fallback is `datetime.now()`, which is **naive local
  time** (TC:398).
- **Bar bucket.** `_get_bar_start_time` (TC:327–331) truncates to the whole
  minute: `second // 60 * 60` is always 0, so it's `replace(second=0, microsecond=0)`.
  **The bar timestamp is the minute's open time.** Its timezone is the tick's
  (UTC from ib_insync).
- **OHLC** (`_create_bar_from_ticks`, TC:333–359), over the ticks in the bucket
  in arrival order:
  - open = first price;
  - high = max;
  - low = min;
  - close = last price;
  - `average` = mean of tick prices (unweighted);
  - `tick_count` = number of ticks.
- **Volume.** `ticker.volume` is read per tick, NaN → 0, and the bar's volume is
  `sum` of the non-zero values. For CMDTY XAUUSD this is normally NaN, so
  **live volume is 0**. If IBKR did send `volume`, it's a cumulative session
  figure, so the sum would be meaningless. The scanner ignores volume. The
  historical parquet has vendor volume (around 20k–50k per minute), so live and
  historical volume can't be compared.
- **When a bar is emitted.** A completed bar (`is_final=True`) is emitted
  **only when the first tick of a later minute arrives** (TC:412–431).
  - No wall-clock timer exists. If ticks stop (market close, a quiet feed, a
    disconnect), the last bar is **held until the next tick**. Example: the
    Friday 16:59 NY bar is emitted only when a later tick arrives. That may be a
    frozen post-close quote, or not until the Sunday reopen. The held bar keeps
    its own timestamp, so `_valid_bar` still accepts it, and it reaches the
    scanner late. The post-close bar that triggered its emission is dropped
    later by `_valid_bar`.
  - On shutdown the forming bar is never emitted.
- **Partial updates.** A partial (`is_final=False`) bar is also emitted when at
  least 5 s of tick time have passed since the last update (TC:437–456).
  - The scanner ignores these (DS:567).
  - Quirk: the 5-s timer resets on new bars, and the first tick of a new bar
    sets `last_update_time` to that tick's time.
- **Gaps.** Minutes with no ticks produce **no bar**. Nothing is forward-filled
  and no empty bars are created.
- **Late or out-of-order ticks.** If a tick's minute is *earlier* than the
  current bar's minute, it goes to the `else` branch and is **appended to the
  current (later) bar** (TC:432–435). It can change that bar's high, low or
  close. Nothing is dropped or reopened.
- **Reconnect.** The scanner sets `max_retries = 0` (DS:592). On disconnect it
  backs off 20 s and runs `SystemExit(1)`, and Docker restarts it into a cold
  start with history reload. The connector's in-place reconnect is a stub
  (TC:533–543) and isn't used.

### 1.3 Scanner-side filter (DS `on_bar`, DS:566–572)

1. Drop `is_final=False` updates.
2. Convert the timestamp to UTC (localize if naive). A naive local fallback
   timestamp would be **mislabelled as UTC** here.
3. Drop the bar unless `_valid_bar(ts, o, h, l, c)` is true.
4. Pass `(ts, o, h, l, c)` to `LiveLevels.on_bar`, which **sets**
   `hist.loc[ts]`. A repeated timestamp overwrites; it doesn't duplicate.

The heartbeat is the wall-clock time of the last *valid final* bar. It warns
once after 30 min of silence while `_market_open(now)` is true.

### 1.4 `_valid_bar(ts, o, h, l, c)` (DS:524–530)

```
_market_open(ts) and all(x == x and x > 0 for x in (o, h, l, c)) and l <= h
```

It drops:
- bars whose **open timestamp** falls outside `_market_open`;
- any NaN field;
- any field ≤ 0 (IBKR's −1 "no quote", 0);
- `low > high`.

It does **not** check that `open` and `close` lie within `[low, high]`, and it
doesn't check staleness or repeats. The selftest pins it (DS:793–802).

### 1.5 `_market_open(ts)` (DS:497–513)

Evaluated in `America/New_York` (DST-safe). It requires a tz-aware timestamp:
`tz_convert` raises on a naive one.

| NY time | Open? |
|---|---|
| Saturday | closed |
| Sunday before 18:00 | closed |
| Sunday 18:00 onward | open |
| Friday 17:00 onward | closed |
| Any day, 17:00–17:59 | closed (daily break) |
| Everything else | open |

Granularity is the hour: 17:00:00–17:59:59 is closed. Holidays aren't
modelled. In UTC the break is 21:00–22:00 in summer and 22:00–23:00 in winter.
The selftest pins six DST cases (DS:765–772).

### 1.6 Historical parquet (replay source)

`database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet` (untracked, not in git).
- Columns: `timestamp` (datetime64[ns, UTC]), open, high, low, close, volume
  (float64). Every timestamp is on a whole minute.
- 2,156,239 rows, from 2020-01-01 23:00 to 2026-01-30 21:59 UTC.
- The scanner's `load_data` treats `timestamp` as the bar open time.
- **It isn't IBKR MIDPOINT.** It's vendor data with vendor volume. Replay
  through the scanner's code reproduces the replay path, not the live price
  source.

## 2. Exact-equivalence targets for step 3

"Identical" means: for the same tick sequence, the new builder emits the same
`(bar_ts, open, high, low, close, n_ticks)` final bars, at the same tick
(emission on the next minute's first tick), as TC's CMDTY path. After
`_valid_bar`, the same bars reach consumers. That includes:

- an unchanged midpoint still counting as a tick;
- the `close` fallback when a quote side is missing;
- late ticks folded into the current bar;
- no gap bars;
- no emission on silence.

`is_final=False` updates aren't part of the contract: no consumer uses them.

## 3. Points that needed a decision before step 3 (decided; see Decisions)

Each item is current production behaviour that I think is wrong. The task says
to reproduce *exactly*, so by default I will copy all of them and list them as
known flaws. Tell me which ones, if any, to change instead. Any change breaks
exact equivalence for that case and must be called out in the scanner-switch PR.

1. **Stale `close` fallback.** When one quote side is missing, the previous
   session's close enters the bar.
2. **Late ticks** are folded into a later bar.
3. **No emission without a following tick.** The last bar before a pause
   arrives late, possibly days later.
4. **Naive local `datetime.now()` fallback** labelled as UTC.
5. **`n_ticks` counts callback events**, not price changes.
6. **Partial `is_final=False` updates.** I plan to *omit* them from the stream
   (no consumer). Confirm.

## 4. Open questions raised in step 0 (all resolved; see Decisions)

- Does IBKR's 10-day MIDPOINT history include the forming minute as a partial
  bar? The scanner then sets the same `ts` again from the live stream (an
  overwrite in `hist.loc`). For the new store, insertion is idempotent with
  conflict logging, so a partial history bar followed by the final live bar
  would show up as a **conflict**, not an overwrite. This needs a rule, for
  example dropping history bars with `bar_ts >= floor(now)`.
- **Python version.** The backend venv is 3.13.1, but the scanner image is
  `python:3.12-slim`. Step 1 says "match the backend venv". I'd set
  `requires-python >= 3.12` so the same package runs in a 3.12 image, and test
  on 3.13. Confirm.
- **History vs live price source.** History is IBKR's MIDPOINT bars; live is
  our own tick midpoint (+ close fallback). These can differ slightly for the
  same minute.
- **`IB_PORT` default.** Compose says 4004 (paper) / 4003 (live), the code
  defaults to 7497, and some backend scripts use 4002. Step 4 reads `IB_PORT`
  only, with **no default**: fail if unset. Confirm.

## Decisions

- **Scanner image build (step 0).** Docker exists only on the production EC2
  host, and a `compose build` there would retag the image the running scanner
  restarts from. So no build is run. Step 8 shows instead that none of the
  image's inputs changed.
- **Step 1 packaging.**
  - `requires-python = ">=3.12"`: the backend venv is 3.13.1 and the scanner
    image is 3.12. Tested on 3.13.1 only; there's no local 3.12 interpreter.
  - Extras: `feed` (ib_insync 0.9.86, redis 5.2.1), `dev` (pytest 9.1.1), plus
    `replay` (pandas 2.3.3, numpy 2.2.6, pyarrow 25.0.0, tzdata 2025.3). The
    replay source needs parquet, and the core (bus, store, query) stays
    stdlib-only. Versions match the scanner image and backend venv. redis-py
    wasn't installed anywhere, so 5.2.1 is new.
  - Dev environment: `.venv/` at the repo root (gitignored), Python 3.13.1. The
    research test deps (scipy, matplotlib, numba) are installed there by hand
    at the backend venv's versions. They aren't package dependencies.
  - pytest: `testpaths = ["research", "tests/datafeed"]`. `tests/datafeed`
    sits under `tests/`, so pytest also loads the old `tests/conftest.py`
    (adds `backend/` to `sys.path`). `tests/datafeed/conftest.py` removes it
    again, so datafeed tests can't silently import backend code. The old tests
    still run when named explicitly.
- **Step 2 bus.**
  - Each message is JSON-encoded whole into one Redis field (`json`), so ints
    and floats round-trip unchanged.
  - Both timestamps are ISO-8601 UTC with `+00:00`. Naive datetimes are rejected.
  - `consume()` returns the consumer's own unacked entries first, and new ones
    only when none are pending, so recovery after a crash is just a restart
    under the same consumer name.
  - New groups start at stream id `0`.
  - An unacked entry that got trimmed before redelivery is acked, logged and
    counted in `bus.lost`.
  - Default `maxlen` is 100,000 (~70 days of 1-min bars).
  - **RedisStreamBus is untested against a real server**: there's no Redis here,
    and Docker on this Mac isn't available. Its tests skip unless
    `REDIS_TEST_URL` is set. I tried fakeredis and dropped it: it returned
    never-delivered entries for `XREADGROUP ... 0`, which real Redis doesn't do.
- **Step 3: bar contract decisions** (owner, 2026-10-13). Code:
  `datafeed/ingest/xauusd_bars.py`. Each counter is exposed in
  `BarBuilder.health()`.

  | # | Old behaviour | Decision | Reason | Counter |
  |---|---|---|---|---|
  | 1 | One-sided quote → previous session's `close` enters the bar | **Fix**: skip the tick | A stale price makes fake highs/lows and fake level touches | `skipped_one_sided` |
  | 2 | Tick from an earlier minute is folded into the current bar | **Fix**: drop it | It changes the wrong bar's H/L/C | `late_ticks` |
  | 3 | Bar closes only on the next minute's first tick | **Fix**: close at minute end + `GRACE_S` (2.0 s, named constant) via `advance(now)`. Ticks for a minute received after its deadline are late (#2) | Bars before a pause or close no longer arrive minutes or days late | `timer_closes` |
  | 4 | No `ticker.time` → naive local `datetime.now()`, later labelled UTC | **Fix**: use the injected UTC receive time; naive tick times raise | Wrong timestamps (4–5 h off on a non-UTC host), and a feed stall (below) | `ticks_without_ts` |
  | 5 | `n_ticks` counts quote-update events, including unchanged midpoints | **Copy**, documented | It's an activity count; changing it gains nothing | — |
  | 6 | Partial `is_final=False` bars every 5 s | **Leave out** of the stream | No consumer uses them | — |
  | 7 | History load may include the forming minute as a partial bar | **Fix**: `trim_history` drops history bars from the forming minute onward. Completed minutes present in both history and live keep conflict logging (store, step 5) | A partial bar would otherwise collide with the final live bar | — |

  Also counted: `dropped_no_quote` (no usable price at all; dropped in the old
  code too), `ticks_in`, `ticks_used`, `bars_closed`.

  **#4 is worse than it looked in step 0.** The equivalence test found it.
  After a tick without a timestamp, the old connector's open bar is keyed by a
  *naive* time. Every later tick has a tz-aware `ticker.time`. Comparing the
  two raises `TypeError` inside `_on_pending_tickers`, which is caught and
  logged ("Error in pending tickers handler"). So every later tick is dropped
  and **no bar is emitted again until the process restarts**. The scanner's
  30-min silence warning is the only symptom.

- **Step 3: equivalence result.** The new builder + `_valid_bar` gives
  **identical bars** (bar_ts, O, H, L, C, n_ticks) to the real old code path
  (`TWSConnector._on_pending_tickers` → scanner `on_bar` filter, loaded by file
  path, unmodified, no IBKR) on 11 synthetic sequences:
  - plain trading, many repeated midpoints, multi-minute gaps;
  - 15% −1 / 0 / NaN quotes;
  - US DST start (2026-03-08) and end (2026-11-01) around the break and Sunday
    reopen, plus the summer break;
  - Friday close → frozen weekend quotes → Sunday reopen.

  A guard test checks those edge cases actually occur in the sequences.
  `_market_open` matches the scanner's on every 7th minute of 2026 (75k
  points), and `_valid_bar` on 5,000 random bars. #1, #2, #3 (timer close and
  after-grace late tick), #4 and #7 each have a test asserting the documented
  difference.
- **`_market_open` uses `astimezone(ZoneInfo("America/New_York"))`**, not
  pandas `tz_convert`, so the core package doesn't need pandas. Equivalence is
  tested as above. `tzdata` moved from the `replay` extra to core dependencies,
  because slim images ship no zoneinfo.
- **Bar message (schema_version 1):** the common fields plus `bar_ts` (=
  `source_ts`), `open`, `high`, `low`, `close`, `n_ticks`, `history` (bool).
  There's **no volume field**: live CMDTY XAUUSD has no usable volume (0 in the
  old bars).
- **The builder returns unfiltered bars.** Applying `_valid_bar` before publish
  is wired in step 4.

- **Step 4: sources.**
  - **Layout.** `ingest/ibkr.py` holds only the connection (`IbkrConfig`,
    `IbkrConnection`, `crash`). Both sources (`IbkrXauusdSource`,
    `ReplaySource`) live in `ingest/xauusd_bars.py`, as the target layout says.
  - **One connection.** History is loaded on the same connection as the quote
    stream. The scanner instead opens a second short-lived client
    (clientId + 10).
  - **Config.** `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` are all required, with no
    defaults. No other port variable or port number appears in `ibkr.py`
    (a test checks this).
  - **Crash-only, same as the scanner.** Connect failure, history failure or a
    lost connection: back off 20 s, then `SystemExit(1)`.
  - **Live loop.** `ib.sleep(LOOP_S=0.25)`, then `builder.advance(now)`, so a
    bar closes within ~0.25 s after its grace deadline.
  - **Warm-up.** History bars are trimmed at the forming minute (#7) and
    published with `history=True`.
  - **`n_ticks` is `null`** for history and replay bars: neither has tick
    counts. Live bars carry the event count (#5).
  - **Validity filter.** Only bars passing `_valid_bar` are published, in all
    three paths. Rejected bars are counted in `invalid_bars`.
  - **Replay.** Bars are published in timestamp order, optionally inside
    `[START, END)`, at `BARS_PER_S` (0 = unthrottled). Vendor volume is not
    published.
  - **Entry point.** `python -m datafeed.ingest [--stdout]`;
    `DATAFEED_SOURCE=replay` is the default. `--stdout` prints JSON lines
    instead of publishing to Redis.
- **Open question for step 5 (store conflicts on every restart).** Each
  restart republishes 10 days of IBKR MIDPOINT history. Those minutes are
  already in the store as *live tick-built* bars, and the two usually differ
  slightly. So by your conflict rule, every restart would log thousands of
  `bar_conflicts` rows. Options:
  - (a) the recorder skips `history=True` bars whose minute already exists
    (log only real conflicts between two live bars, or between two history
    bars);
  - (b) store history and live bars as separate rows keyed by
    `(symbol, bar_ts, kind)`;
  - (c) keep the rule as written and accept the volume.

  I'd pick (a). It needs your decision before step 5.
- **Not tested here.** A real gateway. The manual test
  `test_live_paper_gateway_manual` runs only with `DATAFEED_IBKR_LIVE=1` and a
  local paper gateway. Two things are untested: IBKR's `formatDate=2` dates
  being tz-aware UTC (assumed; naive dates are treated as UTC), and the real
  `pendingTickersEvent` flow.

- **Step 5: store keyed by `(symbol, bar_ts, kind)`** (owner decision,
  option b, 2026-10-13).
  - **`kind`** is `history` for `history=True` messages, and `live` otherwise.
    Replay bars are `live` too: they stand in for the live feed locally, and
    `source` tells them apart.
  - **Why.** IBKR MIDPOINT history and tick-built live bars for the same minute
    usually differ slightly. With one row per minute, every restart's 10-day
    warm-up would log thousands of conflicts. Separate rows keep both versions
    and make that difference measurable.
  - **Write rule.**
    - The same values again (OHLC + `n_ticks`; `ingest_ts` and `source` are
      ignored) are a no-op.
    - Different values for an existing `(symbol, bar_ts, kind)` are a real
      conflict. The stored row is kept as first recorded, and the incoming row
      is logged as JSON in `bar_conflicts`.
    - `insert_bar` never commits; the caller commits, then acks (step 6).
  - **Reads.**
    - `bars_before(symbol, t, lookback, db=None, known_by=None)` returns one bar
      per minute with `bar_ts` strictly `< t`, live preferred and history as
      fallback, each row with its `kind`. `lookback` is an int (count) or a
      timedelta (window). (Fix D tightened this to `bar_ts + 1 min <= t`.)
    - Optional `known_by`: also requires `ingest_ts <= known_by`, so a later
      backfill can't leak into a simulated live view.
    - `latest_bar_ts(symbol, kind=None)` for staleness checks.
    - `compare_live_vs_history(symbol, start, end)` returns per-minute
      live − history deltas (O/H/L/C) for minutes that have both, plus counts
      and mean / mean |Δ| / max |Δ| per field.
  - **Query plan.** Both the outer search and the live-exists subquery use the
    primary key (checked with `EXPLAIN QUERY PLAN`). Timestamps are stored as
    canonical `YYYY-MM-DDTHH:MM:SS+00:00` text, so string order is time order.
    Another offset for the same instant normalises to the same key.
  - **Storage details.**
    - Migrations are numbered SQL in `schema.MIGRATIONS`, each applied in its
      own transaction and recorded in `schema_version`.
    - WAL + `synchronous=NORMAL`. Readers open `mode=ro`.
    - The "as of t" rule and the append-only `ingest_ts`-versioned pattern for
      later tables are documented in the `query.py` docstring.
  - **Signature change.** `bars_before` gained `db` and `known_by` keyword
    arguments compared with the step 1 placeholder. `db` defaults to
    `$DATAFEED_DB`.

- **Step 6: recorder.**
  - **Loop.** Consume a batch (up to 500, own unacked entries first), write it
    in one transaction, `COMMIT`, then ack every entry. Consumer group
    `recorder`; the consumer name comes from `DATAFEED_RECORDER_CONSUMER`
    (default `recorder-1`) and must stay stable across restarts.
  - **Crash before COMMIT.** Rollback, then redelivery, so no gap.
  - **Crash after COMMIT, before ack.** Redelivery, and identical values are a
    no-op, so no duplicate rows and no conflicts.
  - **Invalid message** (missing common or bar fields, bad timestamps). It can
    never succeed, so it's logged, counted as `rejected` and acked. (Fix F
    also stores it in `rejected_messages`.)
  - **Store error.** Rollback, `errors += 1`, pause 5 s; the batch is retried
    as pending.
  - **Ack error** propagates (crash-only).
  - **Health.** `last_recorded_bar_ts`; `lag_s` = now − (newest bar_ts + 60 s),
    i.e. measured from when that bar completed (Fix G: per kind, and from the
    newest live bar only); `error_count` / `last_error`;
    counts of inserted / duplicate / conflict / rejected / batches.
    `python -m datafeed.recorder` logs it every 60 s.
  - **Not covered.** A message the store keeps failing on (not a validation
    error) is retried forever and shows up only in `error_count`. No
    dead-letter stream in v1. (Fixed later by Fix E.)

- **Step 7: local run.**
  - **Files.** `deploy/datafeed/docker-compose.yml` (header: "Not deployed.
    Local testing only.") with redis (7.4.1-alpine, healthchecked), feed
    (replay of 2025-06-11 by default) and recorder (`on-failure`), and a
    `datafeed_store` volume. The parquet is mounted read-only. There's also a
    new `deploy/datafeed/Dockerfile` (python:3.12-slim, `.[feed,replay]`): the
    compose services need an image, and this is the only file outside the
    planned list. `redis://redis:6379/0` in the compose file is the compose
    service name, not a host.
  - **Not run: Docker isn't available on this Mac.** The compose file was
    checked only by parsing it (services feed, recorder, redis).
  - **End-to-end test** `tests/datafeed/test_end_to_end.py`: one day of
    parquet (2025-06-11) → `ReplaySource` → bus → `Recorder` → store →
    `bars_before` equals the parquet rows after `_valid_bar` (exact float
    equality, >1,000 bars). It runs over `InMemoryBus` (passes) and over
    Redis when `REDIS_TEST_URL` is set (skipped here: no Redis).
  - `datafeed/README.md`: full usage, env table, message schema, store tables,
    the "as of t" rule, and planned modules.

- **Fix A: kind `replay`** (owner decision, 2026-10-13). Before this, v1
  stored replay bars as `kind='live'`, so vendor parquet bars could stand in
  for real tick-built bars.
  - **Migration 0002** rebuilds `bars` with
    `CHECK (kind IN ('live','history','replay'))` and moves rows with
    `source='replay'` to `kind='replay'`.
  - **`kind_of`:** `source == "replay"` maps to `replay`.
  - **Reads.** `bars_before(..., kinds=("live","history"))` is the default.
    `kinds=("replay",)` returns replay bars only. Any mix with replay raises
    `ValueError`, so replay is never a fallback.
  - **Index test.** It now checks the plan of the SQL `bars_before` actually
    runs (captured with a trace callback), not a stand-in query.

- **Fix B: seal guard on replay** (owner decision, 2026-10-13).
  - **Constant.** `xauusd_bars.SEAL_START` = 2026-10-01 00:00 UTC. It's
    separate from research code (datafeed doesn't import research), and a test
    loads `research/sealing.py` by path and asserts it equals `GLOBAL_SEAL`.
  - **Window check.** `ReplaySource` raises `SealedDataError` at construction
    if the window ends after `SEAL_START`. (Fix C later made a missing end
    default to the coverage end.) Ending exactly at the
    seal is allowed, since the end is exclusive.
  - **Row check.** During the run, any row with `bar_ts >= SEAL_START` raises
    before it's published. It's defence in depth: the window check already
    excludes such rows.
  - **No override.**

- **Fix C: parquet coverage** (owner decision, 2026-10-13).
  - **Constant.** `PARQUET_COVERAGE` = bar_ts 2020-01-01 23:00 to 2026-01-30
    21:59 UTC, inclusive. A test checks it against the real parquet's min/max
    when the file is present.
  - **Window rule.** The replay window must satisfy
    `first <= start < end <= last + 1 min`, else `CoverageError`. That also
    covers empty or reversed windows, and windows partly or wholly outside.
    There's no silent short replay.
  - **Defaults.** A missing start or end means the coverage bound, so an open
    end is now allowed: it can't reach the seal.
  - **Other parquet files.** `coverage=` is a parameter. Tests pass the small
    fixture's coverage, and other files need their own.
  - **Docs.** Coverage and window are in `health()` and the README.

- **Fix D: `bars_before` returns only completed bars** (owner go,
  2026-10-14). Found while verifying the phase 1 cleanup.
  - **Bug.** The filter was `bar_ts < t`. For a mid-minute t (10:07:30) that
    returned the 10:07 bar, which only completes at 10:08:00. For replay,
    history and backfilled bars, whose stored values are the final ones, that
    was up to 59 s of look-ahead. The step 5 test even asserted it (at
    10:01:59.999 it expected the 10:01 bar).
  - **Fix.** The filter is now `bar_ts + 1 min <= t`, i.e. the bar had ended
    by t. On a minute boundary the result is unchanged, so every caller that
    passes whole minutes (all current tests and the step 7 end-to-end run)
    sees the same bars. Live bars were never affected in practice: they reach
    the store only after their minute ends.
  - **Timedelta lookback.** Unchanged rule (`bar_ts >= t - lookback`). With
    the new end rule it keeps the bars that lie wholly inside
    `[t - lookback, t]`.
  - **Also.** `t` may now be an ISO string with an offset for a timedelta
    lookback too (it was parsed only for a count lookback before). A naive
    datetime still raises.
  - **Tests.** The step 5 assertion is corrected. A new test covers
    mid-minute t for live, history and replay, the bar appearing exactly at
    its end, a string t with a non-UTC offset, the timedelta window and a
    naive t. Both tests fail on the old code. tests/datafeed: 77 passed,
    7 skipped (was 76 + 7).

- **Fix E: poison messages** (owner request, 2026-10-14).
  - **Problem.** A message that passes validation but makes the store write
    fail (e.g. `open: null` → NOT NULL) rolled back its whole batch, and the
    batch was redelivered and failed forever: the recorder was stuck.
  - **Rule.** Every failed batch counts a failure against each of its
    entries. Once an entry has failed `MAX_BATCH_FAILURES` = 3 times, its batch
    is reprocessed one message per transaction. A message that still fails is
    written to `rejected_messages` (stage `poison`) and acked; the rest are
    recorded and acked one by one.
  - **Failing store is not poison.** If the `rejected_messages` row can't be
    written either, the store itself is failing (disk full, locked). Nothing
    more is acked, the error is counted and the batch is retried after 5 s.
    Bars are never given up on because of a broken store.
  - **Failure counts are in memory.** After a restart a batch gets 3 fresh
    attempts.
  - **Migration 0003: `rejected_messages`.** Columns `stream`, `stream_id`,
    `raw` (message JSON), `error`, `stage` (`invalid` | `poison`),
    `first_seen` (first failure). Primary key `(stream, stream_id)`; a
    redelivery after a crash between COMMIT and ack is a no-op.
  - **Health.** New counters `poison` and `split_batches`.
  - **Tests.** One bad message in a batch of 10: three batch failures, then
    9 recorded, 1 in `rejected_messages`, all 10 acked, and later messages go
    through as normal batches. A failing store (insert and reject both fail)
    records nothing, gives up on nothing and records everything once it
    recovers. `reject_message` is idempotent per stream id.

- **Fix F: validation rejects are stored too** (owner request, 2026-10-14).
  - **Rule.** A message that fails validation is written to
    `rejected_messages` (stage `invalid`, `first_seen` = when the recorder
    saw it) inside the batch's own transaction, then acked with the batch
    after the COMMIT. Before, it was only logged and counted.
  - **Atomic with the batch.** If the batch rolls back, the rejection row
    rolls back with it and nothing is acked. A crash after the COMMIT but
    before the ack redelivers the entry, and the second rejection is a no-op.
  - **Tests.** Both invalid messages are stored with stage, error, raw JSON
    and `first_seen`. A rolled-back batch leaves no rejection row; a crash
    between COMMIT and ack leaves exactly one. Both tests fail without the
    fix.

- **Fix G: recorder lag from live bars only** (owner request, 2026-10-14).
  - **Problem.** `lag_s` used the newest bar of any kind. At feed start the
    history warm-up is published up to the forming minute, so it is fresh by
    construction: a live feed that stopped right after start showed a lag of
    seconds.
  - **Rule.** `health()` tracks the newest recorded bar_ts per kind:
    `last_live_bar_ts`, `last_history_bar_ts`, `last_replay_bar_ts`
    (replacing `last_recorded_bar_ts`). `lag_s` = now − (newest live bar_ts +
    60 s), and None until a live bar has been recorded.
  - **Tests.** Live up to 10:02 and history up to 10:19, checked at 10:20:05:
    `lag_s` is 1,025 s (from the live bar), not 5 s. History and replay only:
    `lag_s` is None, and both per-kind timestamps are reported.

- **Fix H: autocommit connections, now tested** (owner request, 2026-10-14).
  - **Confirmed.** `schema.connect()` opens both the writer and the
    read-only connection with `isolation_level=None`; the recorder and
    `python -m datafeed.recorder` get their connection from it. No code
    change.
  - **Why it matters.** Under sqlite3's default, a write outside an explicit
    `BEGIN` silently opens a transaction. It holds the write lock until
    something commits, and the next explicit `BEGIN` fails with "cannot start
    a transaction within a transaction". Explicit `BEGIN` itself still works
    under the default; the danger is any stray write.
  - **Test.** Both connections have `isolation_level is None`; `migrate()`
    leaves no open transaction; a write outside `BEGIN` is committed at once
    and visible to a reader; explicit `BEGIN`/`COMMIT` works. A control shows
    the default connection leaving the transaction open and the next `BEGIN`
    failing. With `isolation_level=None` removed from `connect()`, the test
    fails.

## Scanner bugs to fix at switch time (new journal epoch)

When the scanner switches to this feed, these fixes change which bars it sees,
so the decision journal needs a new epoch marker. Alerts before and after the
switch aren't strictly comparable.

1. **#1 stale-close fallback.** One-sided quotes put the previous session's
   close into bars.
2. **#2 late ticks** folded into the wrong bar.
3. **#4 missing tick time.** Naive local time labelled UTC, and the bar-builder
   stall after it.

(#3 and #7 also change behaviour at the switch: bar *timing* and the history
warm-up. They aren't bugs in the bars the scanner kept, but list them in the
switch PR.)

## Follow-ups (not this PR)

- **Backfill job.** IBKR MIDPOINT history in chunks → `bars` with
  `source="ibkr_history"`. It would fill the gap from the parquet's end
  (2026-01-30) to the recorder's start, and any recorder outages. It must run
  against a local paper gateway. The scanner never kept its bars (memory only,
  ~10 days, lost on each restart).

## Step 8: verification (2026-10-14)

Baseline is `research/levels-diag` @ 29dec96 (step 0). Branch
`feat/datafeed-v1`.

| Check | Result |
|---|---|
| Scanner `--selftest` | `SELFTEST: ALL PASSED`; full output **byte-identical** to the step 0 output |
| Research suites (backend venv, per suite) | levels 11, levels_confirm 8, levels_diag 4, regime_v2 9, trend_features 7, vol_block 5, vol_features 14, vol_features_r2 9, vol_features_r3 11, test_sealing 2 = **80 passed**, the same counts as baseline |
| Root `pytest` (`.venv`) | **156 passed, 7 skipped** (80 research + 76 datafeed; skips below) |
| `git diff 29dec96..HEAD -- backend deploy/scanner research` | **empty** |
| Scanner image build | **not run** (no Docker locally; nothing is built on EC2, per owner). Replaced by the proof below |
| Image inputs unchanged | every file the scanner image or compose uses has the same git blob at baseline and HEAD (table below), and the working tree is clean in those paths |
| Equivalence (step 3) | **identical** bars (bar_ts, O, H, L, C, n_ticks) between the new builder and the real scanner path on all 11 sequences; `_market_open` identical at 75k timestamps across 2026; `_valid_bar` identical on 5,000 random bars. Deviations #1, #2, #3 (timer close + after-grace tick), #4 and #7 each pass their explicit difference test |
| Secrets | no IPs, hostnames, key paths or secrets in new files (grep). `<host>` placeholders in `.env.example` only |

Image inputs (git blob at baseline = HEAD, first 16 hex of SHA-256 of the
working file):

| File | Blob | SHA-256 |
|---|---|---|
| deploy/scanner/Dockerfile | c2758bcd | 1741751de7a2e172 |
| deploy/scanner/docker-compose.yml | a2fcaf83 | 2d07de53e4709532 |
| deploy/scanner/entrypoint.sh | 6cb15723 | d8d13c41d684c619 |
| deploy/scanner/watchdog.sh | e57c78d3 | 4300ab33baf55cfa |
| deploy/scanner/.env.example | 0682d580 | daccbd4f4b3dddbc |
| backend/deterministic_scanner.py | 9c30b904 | 939c8fef4a8e7f24 |
| backend/services/\_\_init\_\_.py | e69de29b | e3b0c44298fc1c14 (empty) |
| backend/services/tws_connector.py | cdee417c | d92ca81b3d0ad59c |

Branch size vs `research/levels-diag`: 29 files, +4,340 lines, 0 deletions.
That includes the two survey docs (ARCHITECTURE_MAP, REPO_MAP). Without them:
27 files, +3,386.

### Skipped tests: to run before merge

None failed. Six need a throwaway Redis (`REDIS_TEST_URL`, e.g. DB 15 on a
local Redis; the tests flush it). Owner chose to skip Redis for now.

1. `tests/datafeed/test_bus.py::test_ordering_and_roundtrip[redis]`
2. `tests/datafeed/test_bus.py::test_count_limits_batch_and_keeps_order[redis]`
3. `tests/datafeed/test_bus.py::test_unacked_redelivered_after_crash[redis]`
4. `tests/datafeed/test_bus.py::test_groups_are_independent[redis]`
5. `tests/datafeed/test_bus.py::test_trimming_keeps_newest[redis]`
6. `tests/datafeed/test_end_to_end.py::test_replay_one_day_end_to_end[redis]`

**Until these run, `RedisStreamBus` has never run against a real server.**
The part most worth seeing is redelivery after a crash (#3), plus how a
trimmed pending entry comes back from `XREADGROUP ... 0`.

Manual, not a merge blocker:

- `tests/datafeed/test_sources.py::test_live_paper_gateway_manual`. Needs
  `DATAFEED_IBKR_LIVE=1` and a **local paper** gateway. It would confirm the
  two assumptions that are still untested: IBKR `formatDate=2` history dates
  being tz-aware UTC, and the real `pendingTickersEvent` flow.

Also not run: `docker compose -f deploy/datafeed/docker-compose.yml up`
(no Docker locally).

### Open questions (carried forward)

- **Python 3.12** (the scanner image) is allowed by `requires-python` but
  untested; only 3.13.1 was available.
- **Backfill job** (follow-up PR, see above). It's the only way to recover the
  gap from the parquet's end (2026-01-30) to the recorder's start.
- **The scanner never kept its bars.** History before the recorder starts can
  only come from IBKR MIDPOINT history, not from the tick-built bars the
  scanner actually saw.
- **No dead-letter stream.** A message the store keeps failing on retries
  forever and shows only in `error_count`.
- **Has #4's stall happened in production?** Grep the scanner logs for
  "Error in pending tickers handler".

### What the scanner-switch PR will need

1. **Consume `bars.xauusd.1m`** instead of owning IBKR. Drop
   `services/tws_connector.py` from the scanner image, and give the feed its
   own client id.
2. **Warm-up from the store.** Build levels from
   `bars_before("XAUUSD", now, timedelta(days=N))` (live preferred, history
   fallback) instead of a 10-day IBKR reload. That removes the partial-week
   PWH/PWL risk. Keep the IBKR history load as a fallback while the store is
   young.
3. **A test proving live bars == replay bars** through the scanner, as in
   the target plan, before switching.
4. **A new journal epoch.** The fixes listed under "Scanner bugs to fix at
   switch time" (#1, #2, #4) and the behaviour changes #3 and #7 change which
   bars the scanner sees.
5. **Staleness.** Replace the scanner's silence heartbeat with
   `latest_bar_ts(kind="live")` + recorder `lag_s` (live only, Fix G).
6. **Run the six Redis tests** above, and a paper-gateway smoke test of the
   feed.
