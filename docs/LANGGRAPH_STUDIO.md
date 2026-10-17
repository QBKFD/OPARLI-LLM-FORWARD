# LangGraph Studio: debugging the agents graph locally

Studio shows the agents graph (`agents/graph.py`) as a picture, runs it on a
trigger you type in, and lets you step through the state after every node.
It's a laptop tool for debugging only:
- no LLM calls and no API keys;
- replay data only (the seal holds: an `as_of` after 2026-10-01 raises
  `SealedError`);
- nothing is written outside a temp directory and `.langgraph_api/`
  (gitignored);
- not used by CI, the tests, the scanner or any deployment.

## What runs

- **`langgraph.json`** (repo root) points `langgraph dev` at
  `tools/langgraph_studio/studio.py:make_graph`.
- **`make_graph`** builds the normal graph once per server process, with:
  - **the replay datafeed store** from `$STUDIO_DATAFEED_DB`. If that is
    unset, a synthetic store is built in a temp dir: a seeded random walk,
    Mon 2025-06-09 to Wed 2025-06-11 UTC. The same data the agents tests use,
    so it is **not market data**;
  - **the config** `agents/config.example.toml` (replay, debate off), or
    `$STUDIO_AGENTS_CONFIG`. Live mode is refused;
  - **an in-memory agent store**, so Studio runs leave no rows anywhere.
- The three voters are still the skeleton's stubs. Every run ends in
  `abstain` votes and a blind decision `none`. Studio shows the plumbing,
  not a strategy.

## Install (once)

The dev server needs `langgraph-cli[inmem]`: 35 packages on top of
`requirements.lock`. They are pinned with hashes in **`requirements-studio.lock`**: every pin of
`requirements.lock`, unchanged, plus the Studio tooling, all released at
least two weeks before 2026-10-17. Use a separate venv, so the project
`.venv` keeps matching `requirements.lock`:

```
python3.13 -m venv .venv-studio
.venv-studio/bin/pip install --require-hashes -r requirements-studio.lock
.venv-studio/bin/pip install --no-deps --no-build-isolation -e .
```

`.venv-studio/` and the server's `.langgraph_api/` state are gitignored. To
regenerate the lock after a pin change, run the `uv pip compile` command in
its header (inputs: `pyproject.toml`, `requirements-studio.in`).

## Start it

```
# 1. Build the replay store once (under a second). Without this, the first
#    run in Studio builds it inside the server, which takes ~16 s and logs a
#    "Slow graph load" error.
.venv-studio/bin/python tools/langgraph_studio/studio.py build-db --synthetic --out /tmp/studio-datafeed.sqlite

# 2. Start the server: no browser tab, no analytics, no tracing
STUDIO_DATAFEED_DB=/tmp/studio-datafeed.sqlite \
LANGGRAPH_CLI_NO_ANALYTICS=1 LANGSMITH_TRACING=false \
  .venv-studio/bin/langgraph dev --no-browser
```

It prints:
- the API, `http://127.0.0.1:2024` (bound to localhost);
- the Studio UI link, `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024`.

**About the UI and the network:**
- The Studio page is served by smith.langchain.com and may ask you to sign
  in to LangSmith. The page then talks to your local server from the
  browser.
- No LangSmith key is set, so the server sends no traces.
- On start, the server checks PyPI for a newer `langgraph-api` and prints a
  "Critical support" notice for 0.15.1. The newer 0.16.x is less than two
  weeks old, so it isn't pinned yet.

**Real bars instead of synthetic ones** (development period only: the
builder refuses an end after 2021-01-01):

```
.venv-studio/bin/python tools/langgraph_studio/studio.py build-db \
    --parquet database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet \
    --start 2020-06-08T00:00Z --end 2020-06-13T00:00Z --out /tmp/studio-2020.sqlite
```

**Without Studio**, the same graph runs once from the shell and prints a JSON
summary (status, skip reason, snapshot hash, bars per timeframe, votes,
decisions):

```
.venv-studio/bin/python tools/langgraph_studio/studio.py run --t 2025-06-11T14:30:00Z --db /tmp/studio-datafeed.sqlite
```

## Run something

In Studio, pick the graph `agents` and give it this input:

```json
{"event": {"source": "studio", "t": "2025-06-11T14:30:00Z", "trigger_id": "studio-1", "payload": {}}}
```

Three inputs show the three paths through the graph:

| `t` | Path | What you should see |
|---|---|---|
| `2025-06-11T14:30:00Z` (Wed, market open) | trigger → snapshot → gate → the three voters in one step → aggregator → risk → store | `status: done`. Three `abstain` votes, blind decision `none` |
| `2025-06-11T21:30:00Z` (the daily break, EDT) | trigger → snapshot → gate → store | `status: skipped`, `gate: market closed at as_of …` |
| `2025-06-14T12:00:00Z` (Saturday, after the data) | trigger → snapshot → store | `status: skipped`, `stale: newest 1m bar … behind the last market-open minute …` |
| `2026-10-02T14:00:00Z` (sealed) | trigger → snapshot (raises) | the run fails with `SealedError` |

## What to look at

- **The graph picture.** The fan-out from `gate` to the voters, the fan-in
  at `aggregator`, and the two conditional edges into `store` (stale, gate
  no-go). With `debate.enabled` a `debate` node would sit between
  `aggregator` and `risk`. No debaters ship yet, so that config fails at
  build time.
- **State after `snapshot`.** `snap.as_of` is `t` floored to the minute.
  `snap.newest_bar_ts` is strictly before `as_of`: the forming minute is
  never included. Also check `snap.bars` per timeframe (1m 240, 15m 192, 1h
  and 4h as far as the data goes), the `complete` flag on the last candle of
  each timeframe, and `snap.hash`.
- **State after the voters.** `votes.<agent>.abstain` and its `reason`. A
  voter that raises or times out shows as an abstain with the error as the
  reason, never as a direction.
- **State after `aggregator` and `risk`.** `decisions.blind` and
  `final_decisions.blind`: the same while the risk node is a pass-through.
- **Checkpoints.** Every step is a checkpoint you can open and inspect.
  - **Don't replay or fork from a checkpoint after `trigger`.** `trigger` sets
    `run_id`, so the replayed run reaches `store` with a `run_id` that is
    already stored, and fails with `StoreConflict` (checked 2026-10-17). The
    agent store is append-only, in memory too.
  - **To try another `t`, start a new thread** (or a new run on the same
    thread) with a new input.

## Cost and footprint

Measured on the development Mac, 2026-10-17:
- **Server memory:** ~150 MB RSS with a prebuilt store, ~220 MB when it
  builds the synthetic store itself.
- **Per run:** ~1 s end to end through the HTTP API (`runs/wait`).
- **Builds:** the synthetic store takes 0.4–0.7 s from the CLI. Inside the
  server it took ~16 s; hence step 1 above.

The server is for a laptop session, not for the 3.8 GB EC2 host.

## Tests

`tests/agents_tests/test_studio.py` covers:
- the factory: async, built once;
- the three paths above and the seal;
- the CLI and its failure paths;
- the store builders, including the refusal of post-2020 parquet windows;
- `langgraph.json`, which must point at the factory and hold no env values;
- `requirements-studio.lock`, which must contain every project pin
  unchanged.

The tests run in the normal project venv and need neither the dev server nor
the network:

```
.venv/bin/pytest tests/agents_tests/test_studio.py
```
