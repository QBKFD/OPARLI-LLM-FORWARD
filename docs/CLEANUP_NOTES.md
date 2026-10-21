# Cleanup phase 1: notes

> **Status (2026-10-14): steps 0–7 done. Every check passed; no open
> question blocks this PR.** The full report is in "Step 7" and "PR
> description" at the end.
>
> **One follow-up for the datafeed branch** (not this PR): `bars_before(t)`
> can return a still-forming bar when `t` isn't on a minute boundary (see
> step 5).

Branch `cleanup/phase1`, created from `feat/datafeed-v1` @ c3d25fb.

**Goal.** Move unused code into `legacy/`. Production must not change.

**Production files:**
- `deploy/scanner/*`
- `backend/deterministic_scanner.py`
- `backend/services/__init__.py`
- `backend/services/tws_connector.py`

`datafeed/` and `tests/datafeed/` are not touched.

## Step 0: baseline (2026-10-14)

Local tag **`legacy-agents-v0`** → c3d25fb, the last commit before any
cleanup. Not pushed.

**Interpreters:**
- **Scanner, research and old agent tests:** `algo_project/backend/venv`
  (Python 3.13.1).
- **datafeed and the root run:** `.venv` (Python 3.13.1, editable install of
  `datafeed`).

| Check | Result |
|---|---|
| `python backend/deterministic_scanner.py --selftest` | `SELFTEST: ALL PASSED`, exit 0; output byte-identical to the datafeed step 0 baseline |
| research/levels | 11 passed |
| research/levels_confirm | 8 passed |
| research/levels_diag | 4 passed |
| research/regime_v2 | 9 passed |
| research/trend_features | 7 passed |
| research/vol_block | 5 passed |
| research/vol_features | 14 passed |
| research/vol_features_r2 | 9 passed |
| research/vol_features_r3 | 11 passed |
| research/test_sealing.py | 2 passed |
| **Research total** | **80 passed** |
| tests/datafeed | **76 passed, 7 skipped** (6 need `REDIS_TEST_URL`, 1 manual paper-gateway test) |
| Old agent tests, run explicitly (`tests/test_meta_renormalization.py`, `tests/test_pipeline_integration.py`) | 15 passed (9 warnings) |
| Root `pytest` (testpaths = research, tests/datafeed) | 156 passed, 7 skipped |
| `docker build deploy/scanner` | **not run**: no Docker locally, and the owner rules out building on EC2. Replaced by the byte-identity proof below, re-checked in step 7 |

Tracked files at baseline: 399. The list is kept outside the repo for the
step 7 reconciliation.

### Production files at baseline

Step 7 must show the same git blob and SHA-256 for each.

| File | git blob | SHA-256 (first 16) |
|---|---|---|
| deploy/scanner/Dockerfile | c2758bcd85cb | 1741751de7a2e172 |
| deploy/scanner/docker-compose.yml | a2fcaf83fe74 | 2d07de53e4709532 |
| deploy/scanner/entrypoint.sh | 6cb15723314c | d8d13c41d684c619 |
| deploy/scanner/watchdog.sh | e57c78d34452 | 4300ab33baf55cfa |
| deploy/scanner/.env.example | 0682d5806589 | daccbd4f4b3dddbc |
| backend/deterministic_scanner.py | 9c30b9045ef2 | 939c8fef4a8e7f24 |
| backend/services/\_\_init\_\_.py | e69de29bb2d1 | e3b0c44298fc1c14 (empty) |
| backend/services/tws_connector.py | cdee417c0840 | d92ca81b3d0ad59c |

### Notes for later steps

- **Old agent tests won't be in the step 6 root run.** They pass only when
  run explicitly with the backend venv: they import backend modules that
  `.venv` doesn't have installed. Step 6 makes root `pytest` collect `tests/`.
  Once step 4 moves those three files to `legacy/tests_agents/`, `tests/`
  only holds `tests/datafeed/`, so the root run stays green.
- **Git history keeps the old `scripts/deploy.sh` values.** Step 1 replaces
  the hard-coded server address and key path with env vars, but the old
  values stay in git history and in the `legacy-agents-v0` tag. Owner
  decision: no history rewrite.

## Step 1: secrets hygiene in scripts/deploy.sh

- **What changed.** The hard-coded SSH key path and `user@address` are
  replaced by required env vars `DEPLOY_KEY` and `DEPLOY_HOST`. The script
  stops with a clear message if either is unset (`${VAR:?...}`). Nothing else
  changed. `bash -n` passes.
- **Rest of the repo.** A repo-wide grep finds no other real address or key
  path: the remaining `~/.ssh/id_rsa` strings are generic defaults in
  `deploy/.env.example`, `scripts/connect.sh` and
  `scripts/backfill/export_backtest_data.py`.
- **`deploy/.env.example` (then at the root) not touched**, per the owner's no-`.env`-changes rule.
  `deploy.sh` moves to `legacy/` in step 4.
- **The old values remain in git history** and in the tag `legacy-agents-v0`.
  No history rewrite (owner decision). If that key is still valid, rotating
  it is the only real fix.

## Step 2: deleted

| Deleted | Lines | Why |
|---|---:|---|
| scripts/3-start-backend.sh | 6 | launches the undeployed agent API |
| scripts/4-start-frontend.sh | 6 | launches the undeployed frontend |
| scripts/start-all.sh | 42 | opens the two above in macOS Terminal windows |

Nothing else references them, apart from one sentence in `README.md:345` and
the pre-cleanup survey docs. The README isn't edited in this PR: the shorter
cleanup prompt has no README step, and it is stale in other ways after step 4
(it documents `backend/main.py`, `run_system.py` and `scripts/*` at their old
paths). Listed for the PR description.

## Step 3: research no longer depends on regime_classifier/

- **Copied unchanged** to `research/_shared/regime_features/` (`cmp`
  byte-identical): `regime_classifier/regime_features.py` and
  `regime_config.py`. A `PROVENANCE.md` there says where they came from and
  not to edit them.
- **Loader change.** `research/vol_features/run_study.py::_import_feature_engine`
  now points its synthetic package at the copy. It keeps the package name
  `regime_classifier`, so module identities are unchanged. The docstring
  explains why. That's the only research code edit.
- **Callers that go through the loader:** `vol_features/run_study.py`,
  `vol_features/exploratory.py`, `vol_features_r2/run_r2.py`,
  `vol_features_r3/run_r3.py`, `trend_features/run_trend.py`, and two tests:
  `vol_features/test_vol_features.py:124` and `trend_features/test_trend.py:122`.
  No other research file references `regime_classifier/` in code.
- **Numbers identical.** `existing_features()` on 2024-02-01..2024-04-30
  (86,816 rows × 13 features) was computed from the old path before the
  change and from the new path after. `assert_frame_equal(check_exact=True)`
  passes, and both hashes are `df8c1e950866f9df`.
- **Research suites:** 11 / 8 / 4 / 9 / 7 / 5 / 14 / 9 / 11 + sealing 2 =
  **80 passed**, same as baseline.
- **No cache regenerated.** The exact feature comparison covers the only
  computation that changed location. Step 4 re-runs every suite with
  `regime_classifier/` gone from the root, which proves nothing still loads
  from there.

## Step 4: moved with `git mv` (history kept)

197 files renamed. Rename detection keeps their history: `git log --follow`.

| From | To | Files |
|---|---|---:|
| backend/ (all except the 3 production files) | legacy/backend_v0/ (same sub-paths: agents/, config/ incl. news_events.py, core/, database/, middleware/, routes/, services/, static/, main.py, run_system.py, live_trading.py, requirements.txt) | 69 |
| frontend/ | legacy/frontend/ | 85 |
| regime_classifier/ | legacy/regime_classifier/ | 8 |
| notebooks/ | legacy/notebooks/ | 3 |
| tests/conftest.py, tests/test_meta_renormalization.py, tests/test_pipeline_integration.py | legacy/tests_agents/ | 3 |
| scripts/ (everything left after step 2, except the one below) | legacy/scripts/ (same sub-paths: backfill/, no_go_strategies/) | 24 |
| scripts/backfill/fetch_data_xauusd.py | tools/data/fetch_data_xauusd.py | 1 |
| database/schema.sql | legacy/database/schema.sql | 1 |
| scraper_fxfac/ | tools/scraper_fxfac/ | 3 |

**Still at the old location:**
- `backend/` now holds exactly the 3 production files.
- `tests/` holds `tests/datafeed/`, untouched.
- `database/historical_news/DATA_NOTES_events.md` stays: it's calendar
  timezone provenance, not Postgres material.
- `deploy/scanner/`, `datafeed/`, `research/`, `docs/` are unchanged.

**Cleanup of what the moves left behind.** The vacated directories held only
empty folders and ignored `__pycache__` files: no untracked files, no `.env`.
The empty folders and the stale caches in `regime_classifier/` and `tests/`
were removed, so no empty `regime_classifier/` directory stays importable as a
namespace package.

**Edits:** none to legacy code. One non-legacy edit: the usage line in
`tools/data/fetch_data_xauusd.py` now says `python tools/data/fetch_data_xauusd.py`.
Its data path (`../../database/snapshots/...`, relative to the file) still
resolves to the repo root, at the same depth as before. Note that this script
hard-codes a local TWS paper port and `clientId=2`. That's unchanged here.

**Checks after the move:**

| Check | Result |
|---|---|
| Scanner `--selftest` | ALL PASSED, output byte-identical to baseline |
| Research suites | 11 / 8 / 4 / 9 / 7 / 5 / 14 / 9 / 11 + sealing 2 = **80 passed** (baseline 80), now with no `regime_classifier/` at the root |
| `existing_features()` reference (step 3) | still exactly identical |
| tests/datafeed | 76 passed, 7 skipped (baseline 76 / 7) |
| Imports of `legacy` from outside `legacy/` | none (grep) |
| Code outside `legacy/` naming a moved path | only comments/docstrings: `pyproject.toml` comment, two comments in `backend/deterministic_scanner.py` (production, not edited), research docstrings and PREREG/DECISIONS records (historical, not edited) |

**Known consequences, by design:**
- **Old agent tests can't run where they are.** `legacy/tests_agents/conftest.py`
  still points at `<repo>/backend`, so those tests won't run from their new
  location. Legacy code isn't edited, and step 6 keeps `legacy/` out of
  pytest.
- **`tests/datafeed/conftest.py` is now a no-op.** It removes `backend/` from
  `sys.path`, which the old `tests/conftest.py` used to add. It's harmless,
  and untouched per the rules.
- **The README still documents the old layout:** `backend/main.py`,
  `run_system.py`, `scripts/*` and `scripts/start-all.sh`. For the PR
  description.

## Step 5: legacy/README.md and legacy/PORT_CANDIDATES.md

- **`legacy/README.md`.** Says the folder is unmaintained, not deployed,
  excluded from tests and CI, and kept as the port source for the LangGraph
  agents. Includes a folder table (new path, old path, contents).
- **`legacy/PORT_CANDIDATES.md`.** A verdict, a new role, the required fixes
  and file:line evidence (legacy paths) for: meta_decision, the
  market_data_provider + clock pair, the technical stack incl.
  regime_detector, chart_generator, visual_analysis, the sentiment agent,
  news_events, llm_provider, SL/TP + position_sizing + risk_service,
  hard_limit_checker, position_manager, and a DO NOT PORT table for the bus
  agents, core, live_trading, weight_manager and session_tracker.
- **How claims were checked.** Every claim was re-read at its legacy path.
  Those marked **(ran)** were reproduced by executing the legacy code, loaded
  by file path. Nothing in `legacy/` was edited.
- **Corrected or dropped from earlier reviews:**
  - The ATR $10 fallback is at `technical_indicators.py:323`, not `:327`.
  - The meta KeyError is not inside `meta_agent.py`'s own `.format` calls,
    which pass `evolved_instructions`. It happens when `meta_agent.py` hands
    its template to `MetaDecisionService` (`meta_agent.py:284-287` →
    `meta_decision.py:391-398`).
  - `llm_provider` "no retries" is softened to "no explicit timeout or retry
    policy (SDK defaults)".
- **New defects found while verifying:**
  1. `regime_detector.py:68-69` compares percent with dollars. `atr_trend` is
     "decreasing" on 100% of real bars (2023/2025, all timeframes), so the
     strong TRENDING/RANGING branches never fire.
  2. Rounding in `stop_loss_calculator.py:121` against the exact check at
     `risk_service.py:133` rejects 50% of trades whose 3×ATR clamp binds.
  3. With the installed pandas_ta 0.4.71b0, Bollinger Bands are silently
     missing from every chart (column-name template, `chart_generator.py:301-303`).
  4. The chart's session overlay has no `is_complete` filter, so replays draw
     the full-day/week high/low.
  5. The `news_events.py:135` `abs()` makes the Tier-1 after-window 45 min,
     not 30. `tag_events` leaves times naive (`:122`).
  6. The sentiment agent gets no news or calendar input at all
     (`sentimentanalyst_agent.py:207-249`).
  7. `HistoricalDataProvider.get_price(as_of=None)` returns the frame's last
     close. Higher-timeframe candles are returned while still forming. Unknown
     timeframes silently become 1-min.
  8. A Trade Manager / Execution payload key mismatch (`action`/`quantity`/
     `entry_time` vs `direction`/`position_size`/`fill_time`).
- **Follow-up for `datafeed` (not touched here).**
  `datafeed.store.query.bars_before(t)` uses `bar_ts < t`, the same rule as
  the legacy provider. For `t` not on a minute boundary (e.g. 10:07:30), it
  returns the 10:07 bar, which completes only at 10:08. For replay, history
  and backfilled bars that's up to 59 s of look-ahead. Live bars are recorded
  only after they close, so a `known_by` read hides them. The fix belongs in
  the datafeed branch: require minute-aligned `t`, or use
  `bar_ts + 1 min <= t`. The datafeed docstring's "as of t" explanation is
  only right for minute-aligned `t`.

## Step 6: pytest config, survey-doc notes, README

- **pytest** (`pyproject.toml`):
  - `testpaths = ["research", "tests"]`.
  - `norecursedirs` = pytest's defaults plus `legacy`, so legacy/ is skipped
    even when a parent directory is collected.
  - **Results:**
    - Root `pytest` and `pytest .` both collect 163 tests: 80 research + 83
      under `tests/` (76 + 7 skipped). Nothing from `legacy/`.
    - Root `pytest`: **156 passed, 7 skipped**, the same as baseline.
    - `pytest legacy` named explicitly reports 3 collection errors. That's
      expected, since legacy code is not runnable (see legacy/README.md).
  - The `pyproject.toml` comment that listed `scripts/` now lists the new
    top-level folders.
- **Survey docs.** `docs/REPO_MAP.md` and `docs/ARCHITECTURE_MAP.md` now start
  with "**Describes the pre-cleanup state.**" and a pointer to this file for
  old → new paths. Nothing else in them changed.
- **`README.md` rewritten** (469 → 77 lines), short and for the new layout:
  - what runs in production (scanner, `deploy/scanner/`, what it keeps and
    doesn't);
  - datafeed v1 (built, not deployed);
  - research/, tools/, legacy/ (port source only);
  - a folder | status | contents table, how to run the tests, and links to
    the docs.
  - The sentence about the deleted launch scripts is gone. So are the old
    agent-pipeline, API, Postgres, deployment and status sections: they
    described code that now lives in `legacy/`.
  - No study verdicts are stated: each study's REPORT.md is the source.

## Step 7: verification (final HEAD, 2026-10-14)

Baseline = step 0, tag `legacy-agents-v0` (c3d25fb).

| Check | Baseline | Now | Result |
|---|---|---|---|
| Scanner `--selftest` | ALL PASSED | ALL PASSED | output **byte-identical** |
| research/levels | 11 | 11 | = |
| research/levels_confirm | 8 | 8 | = |
| research/levels_diag | 4 | 4 | = |
| research/regime_v2 | 9 | 9 | = |
| research/trend_features | 7 | 7 | = |
| research/vol_block | 5 | 5 | = |
| research/vol_features | 14 | 14 | = |
| research/vol_features_r2 | 9 | 9 | = |
| research/vol_features_r3 | 11 | 11 | = |
| research/test_sealing.py | 2 | 2 | = |
| **Research total** | **80 passed** | **80 passed** | = |
| `existing_features()` reference (step 3) | — | exact match | = |
| tests/datafeed | 76 passed, 7 skipped | 76 passed, 7 skipped | = |
| Root `pytest` | 156 passed, 7 skipped | 156 passed, 7 skipped (step 6 run; collects 163 = 80 research + 83 tests/, 0 legacy) | = |
| Scanner image build | not run (no Docker locally; nothing built on EC2) | not run | replaced by the identity proof below |
| Production files | blob/SHA-256 table in step 0 | same 8 blobs, same 8 SHA-256 | **identical** |
| Dockerfile `COPY` sources | present | all 4 present at the same paths | = |
| `deploy/scanner/`, `datafeed/`, `tests/datafeed/` | — | `git diff legacy-agents-v0..HEAD` empty | **unchanged** |
| Imports of `legacy` outside `legacy/` | — | none (grep for `import legacy`, `from legacy`, `legacy/backend_v0`, `sys.path…legacy`) | **none** |
| Tracked files | 399 | 402 | 197 renamed, 3 deleted, 6 added, 5 modified |

**Notes on the checks:**
- **Old agent tests.** The 15 old agent tests that passed at baseline (when
  run explicitly) now live in `legacy/tests_agents/` and are deliberately not
  run (legacy is excluded).
- **Path-limited diffs look like deletions.** Any `git diff` limited to
  `backend/` lists the 69 moved files as deletions, because it can't see
  their new location under `legacy/`. Without the path limit, all 197 moves
  show as renames.

**What changed, apart from the 197 renames:**

| Change | Files |
|---|---|
| Added | `docs/CLEANUP_NOTES.md`, `legacy/README.md`, `legacy/PORT_CANDIDATES.md`, `research/_shared/regime_features/{regime_features.py, regime_config.py, PROVENANCE.md}` |
| Modified | `README.md` (rewritten), `pyproject.toml` (pytest paths, comment), `docs/REPO_MAP.md` + `docs/ARCHITECTURE_MAP.md` (pre-cleanup note only), `research/vol_features/run_study.py` (loader path + docstring) |
| Deleted | `scripts/3-start-backend.sh`, `scripts/4-start-frontend.sh`, `scripts/start-all.sh` |
| Renamed with an edit | `scripts/deploy.sh` → `legacy/scripts/deploy.sh` (env vars, R082), `scripts/backfill/fetch_data_xauusd.py` → `tools/data/fetch_data_xauusd.py` (usage line, R098) |

`git diff -M --stat legacy-agents-v0..HEAD` before this final notes commit:
**211 files changed, 1,451 insertions(+), 515 deletions(-)** (197 of them
pure renames).

### Reconciliation: every file in docs/REPO_MAP.md

REPO_MAP listed 152 code files: **45 kept, 104 moved, 3 deleted, 0 missing.**
Three were edited: `research/vol_features/run_study.py` (step 3),
`scripts/deploy.sh` (step 1, then moved) and
`scripts/backfill/fetch_data_xauusd.py` (moved, usage line). Rename detection
is git's (`-M`); "moved" with no edit means a 100% rename.

<details><summary>All 152 rows</summary>

| Pre-cleanup path (REPO_MAP) | Status | Path now |
|---|---|---|
| `backend/agents/__init__.py` | moved | `legacy/backend_v0/agents/__init__.py` |
| `backend/agents/base_agent.py` | moved | `legacy/backend_v0/agents/base_agent.py` |
| `backend/agents/execution_agent.py` | moved | `legacy/backend_v0/agents/execution_agent.py` |
| `backend/agents/meta_agent.py` | moved | `legacy/backend_v0/agents/meta_agent.py` |
| `backend/agents/position_manager.py` | moved | `legacy/backend_v0/agents/position_manager.py` |
| `backend/agents/riskmanager_agent.py` | moved | `legacy/backend_v0/agents/riskmanager_agent.py` |
| `backend/agents/scanner_agent.py` | moved | `legacy/backend_v0/agents/scanner_agent.py` |
| `backend/agents/sentimentanalyst_agent.py` | moved | `legacy/backend_v0/agents/sentimentanalyst_agent.py` |
| `backend/agents/technicalanalyst_agent.py` | moved | `legacy/backend_v0/agents/technicalanalyst_agent.py` |
| `backend/agents/trademanager_agent.py` | moved | `legacy/backend_v0/agents/trademanager_agent.py` |
| `backend/agents/visual_analyst_agent.py` | moved | `legacy/backend_v0/agents/visual_analyst_agent.py` |
| `backend/config/__init__.py` | moved | `legacy/backend_v0/config/__init__.py` |
| `backend/config/database.py` | moved | `legacy/backend_v0/config/database.py` |
| `backend/config/llm_provider.py` | moved | `legacy/backend_v0/config/llm_provider.py` |
| `backend/config/news_events.py` | moved | `legacy/backend_v0/config/news_events.py` |
| `backend/core/agent_orchestrator.py` | moved | `legacy/backend_v0/core/agent_orchestrator.py` |
| `backend/core/message_bus.py` | moved | `legacy/backend_v0/core/message_bus.py` |
| `backend/core/scheduler.py` | moved | `legacy/backend_v0/core/scheduler.py` |
| `backend/deterministic_scanner.py` | kept | `backend/deterministic_scanner.py` |
| `backend/live_trading.py` | moved | `legacy/backend_v0/live_trading.py` |
| `backend/main.py` | moved | `legacy/backend_v0/main.py` |
| `backend/middleware/__init__.py` | moved | `legacy/backend_v0/middleware/__init__.py` |
| `backend/middleware/security.py` | moved | `legacy/backend_v0/middleware/security.py` |
| `backend/routes/auth_routes.py` | moved | `legacy/backend_v0/routes/auth_routes.py` |
| `backend/routes/backtest_routes.py` | moved | `legacy/backend_v0/routes/backtest_routes.py` |
| `backend/routes/dashboard_routes.py` | moved | `legacy/backend_v0/routes/dashboard_routes.py` |
| `backend/routes/data_management_routes.py` | moved | `legacy/backend_v0/routes/data_management_routes.py` |
| `backend/routes/live_trading_routes.py` | moved | `legacy/backend_v0/routes/live_trading_routes.py` |
| `backend/routes/regime_routes.py` | moved | `legacy/backend_v0/routes/regime_routes.py` |
| `backend/routes/validation_routes.py` | moved | `legacy/backend_v0/routes/validation_routes.py` |
| `backend/run_system.py` | moved | `legacy/backend_v0/run_system.py` |
| `backend/services/__init__.py` | kept | `backend/services/__init__.py` |
| `backend/services/account_state_manager.py` | moved | `legacy/backend_v0/services/account_state_manager.py` |
| `backend/services/analysis/__init__.py` | moved | `legacy/backend_v0/services/analysis/__init__.py` |
| `backend/services/analysis/market_structure.py` | moved | `legacy/backend_v0/services/analysis/market_structure.py` |
| `backend/services/analysis/meta_decision.py` | moved | `legacy/backend_v0/services/analysis/meta_decision.py` |
| `backend/services/analysis/visual_analysis.py` | moved | `legacy/backend_v0/services/analysis/visual_analysis.py` |
| `backend/services/chart_generator.py` | moved | `legacy/backend_v0/services/chart_generator.py` |
| `backend/services/clock.py` | moved | `legacy/backend_v0/services/clock.py` |
| `backend/services/confluence_checker.py` | moved | `legacy/backend_v0/services/confluence_checker.py` |
| `backend/services/database_writer.py` | moved | `legacy/backend_v0/services/database_writer.py` |
| `backend/services/entry_timing.py` | moved | `legacy/backend_v0/services/entry_timing.py` |
| `backend/services/gap_detector.py` | moved | `legacy/backend_v0/services/gap_detector.py` |
| `backend/services/governance.py` | moved | `legacy/backend_v0/services/governance.py` |
| `backend/services/hard_limit_checker.py` | moved | `legacy/backend_v0/services/hard_limit_checker.py` |
| `backend/services/historical_backfill.py` | moved | `legacy/backend_v0/services/historical_backfill.py` |
| `backend/services/key_level_detector.py` | moved | `legacy/backend_v0/services/key_level_detector.py` |
| `backend/services/market_data_provider.py` | moved | `legacy/backend_v0/services/market_data_provider.py` |
| `backend/services/market_data_streamer.py` | moved | `legacy/backend_v0/services/market_data_streamer.py` |
| `backend/services/meta_agent_strategist.py` | moved | `legacy/backend_v0/services/meta_agent_strategist.py` |
| `backend/services/performance_tracker.py` | moved | `legacy/backend_v0/services/performance_tracker.py` |
| `backend/services/position_sizing.py` | moved | `legacy/backend_v0/services/position_sizing.py` |
| `backend/services/regime_detector.py` | moved | `legacy/backend_v0/services/regime_detector.py` |
| `backend/services/risk_service.py` | moved | `legacy/backend_v0/services/risk_service.py` |
| `backend/services/screenshot_service.py` | moved | `legacy/backend_v0/services/screenshot_service.py` |
| `backend/services/session_tracker.py` | moved | `legacy/backend_v0/services/session_tracker.py` |
| `backend/services/stop_loss_calculator.py` | moved | `legacy/backend_v0/services/stop_loss_calculator.py` |
| `backend/services/sweep_backtest.py` | moved | `legacy/backend_v0/services/sweep_backtest.py` |
| `backend/services/take_profit_calculator.py` | moved | `legacy/backend_v0/services/take_profit_calculator.py` |
| `backend/services/technical_indicators.py` | moved | `legacy/backend_v0/services/technical_indicators.py` |
| `backend/services/technical_service.py` | moved | `legacy/backend_v0/services/technical_service.py` |
| `backend/services/throttle_manager.py` | moved | `legacy/backend_v0/services/throttle_manager.py` |
| `backend/services/timeframe_cascade.py` | moved | `legacy/backend_v0/services/timeframe_cascade.py` |
| `backend/services/trigger_monitor.py` | moved | `legacy/backend_v0/services/trigger_monitor.py` |
| `backend/services/tws_connector.py` | kept | `backend/services/tws_connector.py` |
| `backend/services/weight_manager.py` | moved | `legacy/backend_v0/services/weight_manager.py` |
| `deploy/scanner/Dockerfile` | kept | `deploy/scanner/Dockerfile` |
| `deploy/scanner/docker-compose.yml` | kept | `deploy/scanner/docker-compose.yml` |
| `deploy/scanner/entrypoint.sh` | kept | `deploy/scanner/entrypoint.sh` |
| `deploy/scanner/watchdog.sh` | kept | `deploy/scanner/watchdog.sh` |
| `frontend/Dockerfile` | moved | `legacy/frontend/Dockerfile` |
| `frontend/entrypoint-frontend.sh` | moved | `legacy/frontend/entrypoint-frontend.sh` |
| `notebooks/backtest.ipynb` | moved | `legacy/notebooks/backtest.ipynb` |
| `notebooks/multi_agent_simulation.ipynb` | moved | `legacy/notebooks/multi_agent_simulation.ipynb` |
| `notebooks/smc_validation_backtest.ipynb` | moved | `legacy/notebooks/smc_validation_backtest.ipynb` |
| `regime_classifier/__init__.py` | moved | `legacy/regime_classifier/__init__.py` |
| `regime_classifier/regime_classifier.py` | moved | `legacy/regime_classifier/regime_classifier.py` |
| `regime_classifier/regime_config.py` | moved | `legacy/regime_classifier/regime_config.py` |
| `regime_classifier/regime_features.py` | moved | `legacy/regime_classifier/regime_features.py` |
| `regime_classifier/regime_validator.py` | moved | `legacy/regime_classifier/regime_validator.py` |
| `regime_classifier/tests/__init__.py` | moved | `legacy/regime_classifier/tests/__init__.py` |
| `regime_classifier/tests/test_regime_classifier.py` | moved | `legacy/regime_classifier/tests/test_regime_classifier.py` |
| `research/levels/exploratory_levels.py` | kept | `research/levels/exploratory_levels.py` |
| `research/levels/levels.py` | kept | `research/levels/levels.py` |
| `research/levels/run_levels.py` | kept | `research/levels/run_levels.py` |
| `research/levels/tables_levels.py` | kept | `research/levels/tables_levels.py` |
| `research/levels/test_levels.py` | kept | `research/levels/test_levels.py` |
| `research/levels_confirm/audit_2024.py` | kept | `research/levels_confirm/audit_2024.py` |
| `research/levels_confirm/confirm.py` | kept | `research/levels_confirm/confirm.py` |
| `research/levels_confirm/test_confirm.py` | kept | `research/levels_confirm/test_confirm.py` |
| `research/levels_diag/aa_investigation.py` | kept | `research/levels_diag/aa_investigation.py` |
| `research/levels_diag/bias_check.py` | kept | `research/levels_diag/bias_check.py` |
| `research/levels_diag/confirm_ci_correction.py` | kept | `research/levels_diag/confirm_ci_correction.py` |
| `research/levels_diag/diag.py` | kept | `research/levels_diag/diag.py` |
| `research/levels_diag/test_diag.py` | kept | `research/levels_diag/test_diag.py` |
| `research/regime_v2/exploratory_regime.py` | kept | `research/regime_v2/exploratory_regime.py` |
| `research/regime_v2/jump.py` | kept | `research/regime_v2/jump.py` |
| `research/regime_v2/run_regime.py` | kept | `research/regime_v2/run_regime.py` |
| `research/regime_v2/tables_regime.py` | kept | `research/regime_v2/tables_regime.py` |
| `research/regime_v2/test_regime.py` | kept | `research/regime_v2/test_regime.py` |
| `research/sealing.py` | kept | `research/sealing.py` |
| `research/test_sealing.py` | kept | `research/test_sealing.py` |
| `research/trend_features/exploratory_trend.py` | kept | `research/trend_features/exploratory_trend.py` |
| `research/trend_features/run_trend.py` | kept | `research/trend_features/run_trend.py` |
| `research/trend_features/tables_trend.py` | kept | `research/trend_features/tables_trend.py` |
| `research/trend_features/test_trend.py` | kept | `research/trend_features/test_trend.py` |
| `research/vol_block/test_vol_block.py` | kept | `research/vol_block/test_vol_block.py` |
| `research/vol_block/vol_block.py` | kept | `research/vol_block/vol_block.py` |
| `research/vol_features/exploratory.py` | kept | `research/vol_features/exploratory.py` |
| `research/vol_features/run_study.py` | kept (edited) | `research/vol_features/run_study.py` |
| `research/vol_features/test_vol_features.py` | kept | `research/vol_features/test_vol_features.py` |
| `research/vol_features_r2/audit_long.py` | kept | `research/vol_features_r2/audit_long.py` |
| `research/vol_features_r2/exploratory_r2.py` | kept | `research/vol_features_r2/exploratory_r2.py` |
| `research/vol_features_r2/run_r2.py` | kept | `research/vol_features_r2/run_r2.py` |
| `research/vol_features_r2/tables_r2.py` | kept | `research/vol_features_r2/tables_r2.py` |
| `research/vol_features_r2/test_r2.py` | kept | `research/vol_features_r2/test_r2.py` |
| `research/vol_features_r3/exploratory_r3.py` | kept | `research/vol_features_r3/exploratory_r3.py` |
| `research/vol_features_r3/run_r3.py` | kept | `research/vol_features_r3/run_r3.py` |
| `research/vol_features_r3/tables_r3.py` | kept | `research/vol_features_r3/tables_r3.py` |
| `research/vol_features_r3/test_r3.py` | kept | `research/vol_features_r3/test_r3.py` |
| `scraper_fxfac/calendar_web_scape.py` | moved | `tools/scraper_fxfac/calendar_web_scape.py` |
| `scripts/3-start-backend.sh` | deleted | — |
| `scripts/4-start-frontend.sh` | deleted | — |
| `scripts/analyze_backtest.py` | moved | `legacy/scripts/analyze_backtest.py` |
| `scripts/asian_london_regression.py` | moved | `legacy/scripts/asian_london_regression.py` |
| `scripts/backfill/backfill_month.py` | moved | `legacy/scripts/backfill/backfill_month.py` |
| `scripts/backfill/export_backtest_data.py` | moved | `legacy/scripts/backfill/export_backtest_data.py` |
| `scripts/backfill/fetch_data.py` | moved | `legacy/scripts/backfill/fetch_data.py` |
| `scripts/backfill/fetch_data_xauusd.py` | moved (edited, R098) | `tools/data/fetch_data_xauusd.py` |
| `scripts/backfill/import_csv.py` | moved | `legacy/scripts/backfill/import_csv.py` |
| `scripts/backfill/import_csv_to_postgres.py` | moved | `legacy/scripts/backfill/import_csv_to_postgres.py` |
| `scripts/backtest_agents.py` | moved | `legacy/scripts/backtest_agents.py` |
| `scripts/connect.sh` | moved | `legacy/scripts/connect.sh` |
| `scripts/demo_backtestable_technical.py` | moved | `legacy/scripts/demo_backtestable_technical.py` |
| `scripts/deploy.sh` | moved (edited, R082) | `legacy/scripts/deploy.sh` |
| `scripts/no_go_strategies/smc_backtest_fvg.py` | moved | `legacy/scripts/no_go_strategies/smc_backtest_fvg.py` |
| `scripts/no_go_strategies/smc_backtest_orderblocks.py` | moved | `legacy/scripts/no_go_strategies/smc_backtest_orderblocks.py` |
| `scripts/no_go_strategies/smc_backtest_regime.py` | moved | `legacy/scripts/no_go_strategies/smc_backtest_regime.py` |
| `scripts/regime_strategy_backtest.py` | moved | `legacy/scripts/regime_strategy_backtest.py` |
| `scripts/run_walk_forward.py` | moved | `legacy/scripts/run_walk_forward.py` |
| `scripts/setup-oracle-cloud.sh` | moved | `legacy/scripts/setup-oracle-cloud.sh` |
| `scripts/smc_backtest_orb.py` | moved | `legacy/scripts/smc_backtest_orb.py` |
| `scripts/smc_backtest_orb_fade.py` | moved | `legacy/scripts/smc_backtest_orb_fade.py` |
| `scripts/smc_backtest_sweeps.py` | moved | `legacy/scripts/smc_backtest_sweeps.py` |
| `scripts/smc_bos_choch_validation.py` | moved | `legacy/scripts/smc_bos_choch_validation.py` |
| `scripts/smc_fvg_leads_ob_validation.py` | moved | `legacy/scripts/smc_fvg_leads_ob_validation.py` |
| `scripts/smc_validation_train_test.py` | moved | `legacy/scripts/smc_validation_train_test.py` |
| `scripts/start-all.sh` | deleted | — |
| `scripts/visualize_sweep_trades.py` | moved | `legacy/scripts/visualize_sweep_trades.py` |
| `tests/conftest.py` | moved | `legacy/tests_agents/conftest.py` |
| `tests/test_meta_renormalization.py` | moved | `legacy/tests_agents/test_meta_renormalization.py` |
| `tests/test_pipeline_integration.py` | moved | `legacy/tests_agents/test_pipeline_integration.py` |

</details>

Files outside REPO_MAP's scope (Markdown, SQL, JSON, Vue/JS, configs) moved
along with their folders. All 399 baseline files are accounted for: 197
renamed, 3 deleted, 199 kept in place (5 of them modified). Adding the 6 new
files gives 402 now.

### Incident during step 7 (no effect)

- **What happened.** While writing this report, one shell command used an
  unquoted heredoc. zsh therefore tried to run some backtick-quoted Markdown
  as command substitutions.
- **What ran.** zsh hit a parse error and aborted before anything was
  written. The only substitutions attempted were non-commands (`t`,
  `legacy-agents-v0`, `--selftest`, a glob), and all failed with "command not
  found" or "no matches".
- **Checked afterwards.** `git status` was clean, and no file had changed.
- **Fix.** The report was then written from a file, with no shell expansion.

## PR description

**cleanup/phase1: move the unused agent framework into legacy/ (production unchanged)**

Branch `cleanup/phase1`, based on `feat/datafeed-v1`. Tag `legacy-agents-v0`
marks the state before the move. Local only; not pushed.

**What changes**
- **The old multi-agent framework moves to `legacy/`** with `git mv` (197
  renames, history kept): `backend/` except the scanner's 3 files,
  `frontend/`, `regime_classifier/`, `notebooks/`, the 3 old agent tests,
  `scripts/` and `database/schema.sql`.
  - `scraper_fxfac/` → `tools/scraper_fxfac/`.
  - `scripts/backfill/fetch_data_xauusd.py` → `tools/data/`.
- **Research no longer depends on legacy code.** `regime_features.py` and
  `regime_config.py` are copied byte-for-byte to
  `research/_shared/regime_features/`, and the one importlib loader points
  there. Feature output was checked to be exactly identical.
- **`scripts/deploy.sh`** reads `DEPLOY_HOST` / `DEPLOY_KEY` instead of a
  hard-coded address and key path. The old values remain in git history (no
  rewrite); rotate that key if it's still valid.
- **The three local launch scripts are deleted.**
- **pytest:** root `pytest` collects `research/` and `tests/` only, and
  `legacy` is in `norecursedirs`.
- **Docs.**
  - `README.md` is rewritten for the new layout.
  - `legacy/README.md` and `legacy/PORT_CANDIDATES.md` give a port verdict,
    the required fixes and verified file:line evidence for each candidate
    file.
  - `docs/REPO_MAP.md` and `docs/ARCHITECTURE_MAP.md` are marked as
    describing the pre-cleanup state.

**What does not change**
- The deployed scanner: `deploy/scanner/*` and the 3 production files are
  byte-identical (git blob + SHA-256).
- `datafeed/` and `tests/datafeed/`.
- No `.env` file was touched.

**Verification**
- Selftest output byte-identical.
- Research 80/80.
- tests/datafeed 76 passed, 7 skipped (Redis/manual).
- Root pytest 156 passed, 7 skipped.
- No imports of `legacy/` from outside it.
- All 152 REPO_MAP files reconciled (45 kept, 104 moved, 3 deleted, 0
  missing).
- The scanner image wasn't built (no Docker locally, and nothing is built on
  EC2). Its inputs are byte-identical instead.

**Reviewer notes**
- **`legacy/` code is unedited and not runnable.** `pytest legacy` reports 3
  collection errors by design.
- **`PORT_CANDIDATES.md` lists new defects found while verifying.** These
  include:
  - `regime_detector` percent-vs-dollar comparison;
  - 50% float-rounding rejections in `risk_service`;
  - Bollinger Bands silently missing with the installed pandas_ta;
  - a 45-min (not 30) news blackout;
  - a "sentiment" agent with no news input.

  None of them affect production.
- **Datafeed follow-up:** `bars_before(t)` for `t` off a minute boundary
  (step 5 notes).
