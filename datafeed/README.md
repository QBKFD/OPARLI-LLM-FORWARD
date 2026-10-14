# datafeed

XAUUSD 1-min bar feed. It fetches bars from IBKR (or replays them from
parquet), publishes them to a Redis stream, records them in a point-in-time
SQLite store, and reads them back "as of t".

**Status: v1, branch `feat/datafeed-v1`. Not deployed, and nothing in
production uses it.** The deployed scanner still owns its own IBKR connection.
Bar semantics, decisions and open questions are in `docs/DATAFEED_NOTES.md`.

```
 IBKR (local paper gateway)  ─┐
                              ├─► python -m datafeed.ingest ─► Redis stream ─► python -m datafeed.recorder ─► SQLite
 parquet replay (default)    ─┘        bars.xauusd.1m                              (commit, then ack)          │
                                                                                                               ▼
                                                                    datafeed.store.query.bars_before(symbol, t, lookback)
```

## Install

```
python3.13 -m venv .venv                 # any Python >= 3.12
.venv/bin/pip install -e '.[feed,replay,dev]'
.venv/bin/pytest tests/datafeed          # no IBKR, no Redis needed
```

The core (bus interface, store, query) is stdlib-only, plus `tzdata`.
- `feed` adds `ib_insync` and `redis`.
- `replay` adds `pandas` and `pyarrow`.
- `features` adds `numpy` and `pandas` for `datafeed.features` (vol_block).
  `import datafeed` never loads it.

## Run locally (replay)

**Docker** (local only; `deploy/datafeed/` is never deployed). Run it from the
repo root. It replays 2025-06-11 into the store volume, then the feed exits:

```
docker compose -f deploy/datafeed/docker-compose.yml up --build
```

To change the day, set `DATAFEED_REPLAY_START` / `DATAFEED_REPLAY_END`. To
pace the replay, set `DATAFEED_REPLAY_BARS_PER_S` (0 = as fast as possible).

**Without Docker**, with a Redis you run yourself:

```
export REDIS_URL=redis://<host>:6379/0 DATAFEED_DB=database/datafeed.sqlite
export DATAFEED_REPLAY_PARQUET=database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet
export DATAFEED_REPLAY_START=2025-06-11 DATAFEED_REPLAY_END=2025-06-12
python -m datafeed.recorder &
python -m datafeed.ingest
```

**Coverage.** The parquet covers bar_ts **2020-01-01 23:00 to 2026-01-30 21:59
UTC** (inclusive; `PARQUET_COVERAGE`). A replay window must lie inside it, i.e.
`[2020-01-01 23:00, 2026-01-30 22:00)`, otherwise replay fails with
`CoverageError` rather than replaying fewer bars. A missing start or end means
the coverage bound. `health()` reports `coverage` and `window`.

**Seal guard.** Replay refuses any window that ends after 2026-10-01 00:00 UTC
(the research seal, see `research/SEALING.md`). A sealed
row stops the run. There's no override.

**Without anything** (prints JSON lines): `python -m datafeed.ingest --stdout`.

**Live IBKR**: set `DATAFEED_SOURCE=ibkr` plus `IB_HOST`, `IB_PORT` and
`IB_CLIENT_ID`. All three are required, with no defaults. Point it only at a
**local paper gateway**, never the production one, and use a client id that no
other client of that gateway uses (the scanner uses 11 and 21). It's
crash-only: on any connection problem it backs off 20 s and exits non-zero.

## Environment

| Variable | Used by | Meaning |
|---|---|---|
| `REDIS_URL` | ingest, recorder | Redis for the bus |
| `DATAFEED_SOURCE` | ingest | `replay` (default) or `ibkr` |
| `DATAFEED_REPLAY_PARQUET` | ingest (replay) | parquet path |
| `DATAFEED_REPLAY_START` / `_END` | ingest (replay) | UTC window, `[start, end)` |
| `DATAFEED_REPLAY_BARS_PER_S` | ingest (replay) | rate, 0 = unthrottled |
| `IB_HOST`, `IB_PORT`, `IB_CLIENT_ID` | ingest (ibkr) | gateway connection |
| `DATAFEED_DB` | recorder, query | SQLite store path |
| `DATAFEED_RECORDER_CONSUMER` | recorder | consumer name, stable across restarts |
| `REDIS_TEST_URL` | tests | a **throwaway** Redis; tests flush it |
| `DATAFEED_IBKR_LIVE=1` | tests | enables the manual paper-gateway test |

## Message schema: stream `bars.xauusd.1m`, schema_version 1

One JSON object per entry (Redis field `json`).

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | int | 1 |
| `source` | str | `ibkr` or `replay` |
| `symbol` | str | `XAUUSD` |
| `source_ts` | str | market time = `bar_ts` |
| `ingest_ts` | str | when the source produced the message |
| `bar_ts` | str | minute **open** time |
| `open`, `high`, `low`, `close` | float | bid/ask midpoint (live) or vendor/IBKR bar |
| `n_ticks` | int or null | quote-update events in the minute (live); null for history and replay |
| `history` | bool | true = IBKR history warm-up, never live |

All timestamps are ISO-8601 UTC with `+00:00`.
- **No `volume` field.** Live CMDTY XAUUSD has no usable volume.
- **Only bars that pass the scanner's `_valid_bar`** are published: inside
  market hours, all prices > 0, `low <= high`.

**Live bar rules** (details in `docs/DATAFEED_NOTES.md`):
- price = (bid + ask) / 2 when both are > 0. One-sided quotes are skipped.
- A bar closes at minute end + 2 s (`GRACE_S`), or earlier when the next
  minute's first tick arrives.
- Ticks for an already-closed minute are dropped as late.
- Minutes without ticks produce no bar.

## Store

SQLite in WAL mode. Migrations are numbered in `datafeed/store/schema.py`.

- **`bars`**: primary key `(symbol, bar_ts, kind)`, kind `live`, `history` or
  `replay` (bars from a parquet replay).
  - Columns: OHLC, `n_ticks`, `source`, `schema_version`, `ingest_ts`,
    `recorded_ts`.
  - Writing the same values again is a no-op.
  - Different values for an existing key are not written. They're logged in
    `bar_conflicts`, and the first recorded row is kept.
- **`bar_conflicts`**: append-only log of rejected writes (stored row + incoming
  message, as JSON).
- **`rejected_messages`**: bus entries the recorder acked without a bar row,
  with the raw message JSON, the error, `first_seen` and the stream id.
  Stage `invalid`: the message failed validation; the row is written in the
  batch's transaction, before the ack. Stage `poison`: a message that still failed the store write when retried on
  its own, after its batch failed 3 times (`MAX_BATCH_FAILURES`). The other
  messages in that batch are recorded. If the store itself is failing, nothing
  is given up on: the batch waits until the store recovers.
- **`schema_version`**: applied migrations.

## The "as of t" rule

`bars_before(symbol, t, lookback, db=None, known_by=None, kinds=("live", "history"))` returns only bars
that had **ended by t** (`bar_ts + 1 min <= t`).
- **On a minute boundary** that is `bar_ts < t`: the minute that opens at t
  is never included.
- **Mid-minute** (t = 10:07:30) the 10:07 bar is still forming and is
  excluded too. It appears from 10:08:00.
- **One bar per minute.** The live bar if there is one, otherwise the history
  bar. Each row says which (`kind`).
- **Replay bars only on request.** They're returned only with
  `kinds=("replay",)`, never mixed with live or history and never used as a
  fallback.
- **`lookback`** is a bar count or a `timedelta` window.
- **`known_by`** additionally requires `ingest_ts <= known_by`. Use it to
  simulate what a live process could have seen, so later backfills stay
  invisible.

Also available:
- `latest_bar_ts(symbol, kind=None)`: staleness checks.
- `compare_live_vs_history(symbol, start, end)`: per-minute live − history
  deltas plus summary stats.

Later tables follow the same append-only, `ingest_ts`-versioned pattern
(documented in `datafeed/store/query.py`).

## Modules

| Module | Status |
|---|---|
| `bus.py` | v1: `RedisStreamBus`, `InMemoryBus` (commit-then-ack, redelivery of unacked entries) |
| `ingest/base.py` | v1: `Source` protocol, message envelope |
| `ingest/ibkr.py` | v1: the one IBKR connection (readonly, crash-only) |
| `ingest/xauusd_bars.py` | v1: `BarBuilder`, `_valid_bar` / `_market_open` (from the scanner), `IbkrXauusdSource`, `ReplaySource` |
| `ingest/__main__.py` | v1: `python -m datafeed.ingest` |
| `recorder/` | v1: `python -m datafeed.recorder` |
| `store/schema.py`, `store/query.py` | v1 |
| `ingest/gold_options.py` | planned, later PR |
| `ingest/calendar.py` | planned, later PR |
| `features/vol_block.py` | v1: L, S_hd, T as in `research/vol_block` (bit-identical), as of t from the store; sigma over H minutes (engineering decision). Needs numpy and pandas |
| `features/exposures.py` | planned, later PR |
| `store/sealed.py` | planned, later PR |
| `store/manifest.py` | planned, later PR |

Later root-level packages (not part of this package): `time/`, `levels/`,
`stats/`, `scanner/`.
