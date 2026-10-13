# Cleanup phase 1: notes

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
  `.env.example`, `scripts/connect.sh` and
  `scripts/backfill/export_backtest_data.py`.
- **`.env.example` not touched**, per the owner's no-`.env`-changes rule.
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
