# Agents skeleton: report

**Status (2026-10-14, after the owner's decisions): every decision is applied,
one commit each. On branch `feat/agents-skeleton`; not pushed, not merged.
Committed tree: 305 passed, 7 skipped (agents 229, datafeed 76). The slow exit
did not recur.**

**Needs your attention:** this worktree holds an **uncommitted, staged sync of
`feat/datafeed-v1` @ 5f285d3** (7 datafeed files). I didn't make it. It got
into one of my commits and I took it out again (section 0.3). It's still
staged, untouched.

**Research extra (later on 2026-10-14):** `[research]` is pinned to the old
backend venv and included in the lock. The full root `pytest` gives **393
passed, 7 skipped**, with research at **80 passed**, the baseline count in
every suite (DESIGN.md, "Research extra"). The staged datafeed sync from 0.3
has since been merged into this branch (9567a2a, not by me).

Details of every decision: `agents/DESIGN.md`. Usage: `agents/README.md`.
Sections 1–6 below are the overnight report, kept as written. Answers to its
open questions are marked in section 5.

## 0. Owner decisions applied

### 0.1 Commits

| Decision | Commit | Result |
|---|---|---|
| 1. Live mode only with a PREREG'd experiment, t within 120 s of the wall clock; replay keeps the seal; `experiment_id` on every snapshot and decision | 79168cf | `agents/prereg.py`, `agents/experiments/`, agent store migration 2; 27 tests (`test_live_mode.py`) |
| 2. ≥ 2 agreeing non-abstaining votes, else none; confidence ignored | 220cbc1 | fixed quorum of 2; abstains never count; 14 three-voter cases (8 with abstains) + 3 more |
| 3. Simultaneous openings | — | kept; no change |
| 4. 4h candles on the NY 17:00 trading-day open, DST-safe | c6c0246 | `bucket_start` / `bucket_end` in NY wall-clock time; 11 tests across 2025-03-09 and 2025-11-02 |
| Staleness against the last market-open minute | 8b4fc01 | the daily reopen and the Sunday open now run; the break and the weekend are skipped by the gate, not as stale |
| Pytest timeout | b308208 | `pytest-timeout==2.4.0`, 120 s per test (2.5.0 is yanked upstream); slow-exit watchdog |
| Lock file | 7fc166d | `requirements.lock`: 56 packages, hashed, build backend included; two-week cutoff on transitive deps |
| Report | (this commit) | |

### 0.2 Tests

| Run | Result |
|---|---|
| Committed tree (git archive of HEAD, old datafeed) | **305 passed, 7 skipped** (agents 229, datafeed 76); exited 0.7 s after pytest |
| Working tree (with the uncommitted datafeed sync) | 313 passed, 7 skipped (agents 229, datafeed 84) |
| **Fresh venv from `requirements.lock` only** (`--require-hashes`, `pip check` clean) | 313 passed, 7 skipped; exited ~3 s after pytest |
| pytest-timeout probe (sleep 10 s, `timeout=2`) | killed at 2.0 s |
| Exit watchdog probe (a non-daemon thread holds the exit 40 s) | stacks dumped at 30 s, naming the blocking thread |

The 7 skips are unchanged: 6 Redis, 1 manual IBKR. The agents code passes
with both datafeed versions, because it calls `bars_before` with `floor(t)`,
where the old and new datafeed rules agree.

**Slow exit: did not recur.** Every run since the watchdog was added exited
within ~3 s of pytest finishing (about 10 runs in total, including two fresh
venvs). If it comes back, the run will print every thread's stack 30 s after
pytest ends, and that is what to send.

### 0.3 Incident: someone else's datafeed sync got into my commit

- **What was found.** At 09:03:10, while I was working on D1, 7 files were
  written and staged in this worktree, all with the same timestamp, which
  points to a git checkout:
  - `datafeed/README.md`, `datafeed/recorder/recorder.py`,
    `datafeed/store/query.py`, `datafeed/store/schema.py`;
  - `docs/DATAFEED_NOTES.md`;
  - `tests/datafeed/test_recorder.py`, `tests/datafeed/test_store.py`.

  They are byte-identical to `feat/datafeed-v1` @ 5f285d3. That branch has
  moved on 5 commits since c3d25fb, including "bars_before returns only bars
  that ended by t (no mid-minute look-ahead)", which is overnight finding #1,
  fixed in datafeed itself.
- **What went wrong.** My D1 commit used `git add <my paths>` and then a plain
  `git commit`, which commits everything staged. So the first D1 commit
  contained those 7 files.
- **What I did.**
  - `git reset --soft HEAD~1`, then unstaged those 7 paths.
  - Re-committed D1 with my 17 files only.
  - Re-staged the 7 paths, so the index is exactly as it was before my commit:
    staged and still identical to 5f285d3.
  - Every later commit used `git commit -- <paths>`.
  - Nothing was pushed, and the datafeed files were never edited or discarded.
- **Your call:** keep the sync on this branch (as a proper merge of
  `feat/datafeed-v1`), or drop it. It's an uncommitted staged change, so a
  `git commit` or `git restore --staged` would affect it. I left it alone.

### 0.4 Notes on the decisions

- **D1.**
  - The PREREG path is `agents/experiments/<experiment_id>/PREREG.md`. It must
    exist and be non-empty. The non-empty check is my addition, so a
    placeholder can't pass.
  - Commit order isn't checked: there's no git check that the PREREG was
    committed before the run.
  - `experiment_id` is part of the snapshot hash, so two experiments never
    share a snapshot row. `Snapshot.schema_version` is therefore 2.
  - The prompt templates say never to render the id, and the experiments
    README asks for opaque ids (`exp-0001`), so an id can't hint at a
    hypothesis.
  - No experiment exists yet, so live mode can't run until you add one.
- **D2.** With 3 voters the outcomes are the same as before. Only with more
  voters do abstains stop counting against. With 4 or more voters, if both
  directions reach 2, the result is `none`.
- **D4.** A 4h bucket spanning a DST change lasts 3 h (spring) or 5 h
  (autumn). Both fall early on Sunday, NY time, while the market is closed.
  The week's first 4h bucket (Sunday 17:00 NY) holds at most 3 h of bars,
  because the market opens at 18:00.
- **Staleness.** Holidays still aren't modelled (as in `_market_open`). On a
  holiday the feed is silent while the market counts as open, so runs are
  skipped as stale after 5 min.
- **Lock.**
  - Resolved on macOS / Python 3.13.1. The Linux wheel hashes are included,
    but it hasn't been run on Linux or Python 3.12.
  - `deploy/datafeed/Dockerfile` still installs from `pyproject.toml`
    (`deploy/` is out of scope).
  - The worktree `.venv` now equals the lock: 12 transitive packages moved
    back to releases at least two weeks old (e.g. langchain-core 1.6.10 →
    1.6.6).

### 0.5 Still open

- **The staged datafeed sync** (0.3).
- **PREREG commit check:** whether live mode should also require the PREREG to
  be committed in git (today it checks the file on disk only).
- **Gate minimums** (`min_bars` = 1 per timeframe) are still a placeholder.
- **`_market_open`** is still imported under its private name from datafeed.
- **Timeouts can't kill threads.** Real LLM agents should also set a timeout in
  their client.
- **The distribution is still named `datafeed`.**

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

## 5. Open questions (answered 2026-10-14: see section 0)

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
