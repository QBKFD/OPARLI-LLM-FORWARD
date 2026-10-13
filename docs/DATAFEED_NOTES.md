# Datafeed v1: notes

Working notes for the `feat/datafeed-v1` branch. Step 0 records the baseline and
the **current** bar contract: what production does today, including its flaws.
The new package must reproduce this behaviour exactly unless a deviation is
listed under "Decisions" and approved.

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

## 3. Points needing your decision before step 3

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

## 4. Open questions (not blocking step 1)

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
