# Agents skeleton: report

**Status: steps 0–7 done, on branch `feat/agents-skeleton`. Not pushed, not
merged. 236 tests pass, 7 are skipped (agents 160, datafeed 76; same skips as
the datafeed baseline).** No step was blocked, so there's no question at the
top of `DESIGN.md`. Four design choices were made with the proposed defaults
and are still yours to confirm (section 5).

Date: 2026-10-14 (overnight run). Base: `feat/datafeed-v1` @ c3d25fb.
Details of every decision: `agents/DESIGN.md`. Usage: `agents/README.md`.

## 1. What was built

| Step | Commit | What |
|---|---|---|
| 0 | 9ad4090, e228a67 | Proposal: layout, graph state, schemas. Moved to `agents/DESIGN.md` |
| 1 | 2dcf9e2 | `[agents]` extra: `langgraph==1.2.12`, `pydantic==2.13.5` (newest releases ≥ 2 weeks old). `.venv` in the worktree |
| 2 | 9f71ad6 | Schemas: Snapshot (self-hashing, refuses bars ≥ as_of), Vote, Claim, Turn, DebateResult, Decision, TriggerEvent. Canonical JSON + sha256 |
| 3 | 21db4fa | Snapshot builder. `bars_before(…, floor(t))`; 1m → 15m/1h/4h; hash; save before agents; stale → skip + reason; seal guard |
| 4 | ad50e6e | LangGraph run graph: trigger → snapshot → gate → visual ‖ technical ‖ sentiment (stubs, blind, parallel) → aggregator v0 → risk stub → store |
| 5 | 6810e73 | Debate subgraph (off by default). Bull/bear rounds, evidence check in code, pluggable judge (`code_v0` placeholder), `prompts/bull.md` and `bear.md` (not called) |
| 6 | 9ebb292 | SQLite agent store (`$AGENTS_DB`): snapshots, runs, votes, debate_turns, decisions. Append-only, one transaction per run. TOML config |
| 7 | 98d1ed9 | Acceptance tests A1–A9 on replay data + a real parquet day; README |
| — | (this commit) | This report; removed one unused test import |

Size: `agents/` is 1,730 lines of Python plus prompts, config and docs.
`tests/agents_tests/` is 1,572 lines. Nothing outside `agents/`,
`tests/agents_tests/` and `pyproject.toml` changed.
`git diff c3d25fb..HEAD -- datafeed backend deploy research legacy` is empty.
Nothing in `agents/` imports backend, legacy, IBKR, Redis, Postgres or any LLM
SDK (a test checks this).

## 2. Test results

| Suite | Result |
|---|---|
| `pytest tests/agents_tests` | **160 passed** |
| `pytest tests/datafeed` | **76 passed, 7 skipped**: identical to the datafeed step 8 baseline |
| Both together, in a **fresh venv** installed from the pins | **236 passed, 7 skipped** (37 s) |
| Acceptance A1–A9 + real parquet day | all pass (table in `DESIGN.md`, step 7) |
| Wheel build | contains every `agents` subpackage, the prompts and `config.example.toml` |
| pyflakes over `agents/` | clean |

The acceptance list, one test each, all through the full graph and the SQLite
store, asserting on stored rows:

- **A1.** No bar ≥ t in any snapshot, including triggers that aren't on a
  whole minute.
- **A2.** Same inputs → same hash, across rebuilt stores.
- **A3.** A raising node → abstain, and the graph finishes.
- **A4.** Debate off → blind decision only.
- **A5.** Debate on → blind + debate decisions on the same snapshot, with the
  round-1 rows identical to a debate-off run.
- **A6.** An invalid evidence_ref is stored as invalid and hidden from the
  judge.
- **A7.** A tie → hold, and a debater error → the debate still finishes.
- **A8.** A debater sees exactly snapshot + round-1 votes + the opponent's
  previous turn.
- **A9.** Identical DebateResult for the same snapshot and the same fakes.

**Real data:**
- 2025-06-04..11 was replayed from the parquet through datafeed into a temp
  store, and 48 triggers on 2025-06-11 were run with the debate on.
- 45 runs finished. 3 were skipped: 21:00 by the gate (market closed), 21:30
  and 22:00 as stale.

## 3. Findings during the build

1. **Rounding t down to the minute matters (lookahead).**
   - `bars_before(t)` with t = 12:00:30 returns the 12:00 bar, which is still
     forming at t. In a replay store it is already complete.
   - The snapshot is therefore built as of `floor(t)`, and A1 tests triggers
     that aren't on a whole minute.
2. **The test directory `tests/agents/` shadowed the package.** pytest puts
   `tests/` on the import path (because of `tests/conftest.py`), and the
   directory was then imported as a namespace package `agents`. Steps 2–3
   passed by luck. The directory is renamed `tests/agents_tests/`, and a guard
   test checks which `agents` package loaded. The same guard also covers
   `backend/agents/`.
3. **Debater views carried timings.**
   - The round-2 view included the opponent's `latency_ms`, so the view hash
     (the future prompt hash) changed from run to run.
   - Views now carry content only: no model ids, prompt hashes, tokens or
     latency. A real-clock test checks this.
4. **The fetch window is aligned to the largest timeframe.** Otherwise the
   oldest 4h bar is cut at the window edge but still labelled complete.
5. **The first trigger after the daily reopen is skipped as stale** (22:00
   UTC: the newest bar is still 20:59). That follows from `max_staleness =
   5 min`. Flagged as a question below.
6. **One-off slow exit.** Once, pytest printed its summary after 12 s but the
   process took over 3 minutes to exit. It didn't happen again in more than 8
   later runs, and the cause wasn't found. If it comes back, the first suspect
   is a daemon thread left behind by a timed-out agent.

## 4. Skipped, and why

- **Research suites:** not run, because this venv has no scipy (collection
  fails on 9 modules with "No module named 'scipy'"). The branch doesn't touch
  `research/`. I didn't install their dependencies, because finding the right
  versions means reading the backend venv, which this task excludes.
- **6 Redis tests + 1 manual IBKR test** (datafeed): skipped, as at the
  datafeed baseline. They aren't affected by this branch.
- **Live mode:** implemented (live/history kinds, `known_by = t`), but it can't
  run: the seal guard refuses `as_of` after 2026-10-01 (Q1). Tested only as far
  as "live never falls back to replay bars".
- **LLM agents, debaters and judge:** not built, as specified. The voters are
  stubs that abstain, the debaters are fakes (tests only), and the LLM judge is
  a registry slot.
- **Graph drawing:** LangGraph's ASCII drawing needs grandalf. Not installed;
  the diagram is in `DESIGN.md` and the README.

## 5. Open questions

The proposal's defaults are applied. Each is a small change if you decide
otherwise.

- **Q1. Seal vs live shadow runs.** The snapshot builder refuses
  `as_of > 2026-10-01` with no override, so the graph runs on replay only.
  Allowing live shadow runs (which read sealed data and are later scored on
  it) needs your explicit decision.
- **Q2. Aggregator v0.** A strict majority of all voters (≥ 2 of 3; abstains
  count against); confidence ignored. Placeholder.
- **Q3. Debate order.** Simultaneous rounds: blind openings, then each side
  sees only the opponent's previous turn.
- **Q4. 4h alignment.** UTC epoch (00/04/…/20), not NY session.

New ones, from the build:

5. **Staleness after the reopen.** With `max_staleness = 5 min`, triggers
   during the first ~minute after 22:00/23:00 UTC are skipped. Keep it,
   lengthen it, or let the gate handle the break?
6. **Gate minimums.** `min_bars` is 1 per timeframe, a placeholder. Real
   minimums depend on what the real agents need.
7. **Transitive dependencies aren't pinned.** langchain-core 1.6.10, langsmith
   0.14.8 and others resolve freely under the two pins. A lock file or
   constraints file would make the install reproducible.
8. **`_market_open` is a private datafeed name.** The gate imports it so it
   uses the same rule as the scanner. A public alias in datafeed would be
   cleaner, but that's a datafeed change, which this task excludes.
9. **Timeouts can't kill threads.** A timed-out agent keeps running in the
   background. Real LLM agents should also set a timeout in their client.
10. **Distribution name.** `pyproject.toml` still names the distribution
    `datafeed`, though it now ships `agents` too.

## 6. To review

```
git log --oneline c3d25fb..feat/agents-skeleton
.venv/bin/pytest tests/agents_tests tests/datafeed
less agents/DESIGN.md        # proposal + one section per step
```

The parquet is symlinked into this worktree at
`database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet`. It's gitignored and not
committed. Without it, only the real-parquet test (and 3 datafeed tests) skip.
