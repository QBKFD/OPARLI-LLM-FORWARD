# refactor/scanner-package

PR 2 of [docs/RESTRUCTURE_PLAN.md](../history/RESTRUCTURE_PLAN.md): the scanner split
into `backend/levels/` (pure) and `backend/scanner/`, with no behaviour
change. Decisions taken without the owner, each with its alternative.
Phase 0 and PR 1 decisions are in
[refactor-backend-layout.md](refactor-backend-layout.md).

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| SP1 | **One module per commit, moved verbatim by a script** that cuts each top-level definition out of `deterministic_scanner.py` with its attached comments and leaves an import behind. That kept the scanner, the tests and the CLI working at every commit. A check after every commit: every non-blank line of `main`'s three scanner files is still there, apart from the intended edits. | Every commit ran, and a moved line can't silently change. | Rewrite the modules by hand in one commit. |
| SP2 | **The golden replay ran after each of the 13 extraction commits**: all byte-identical to `main` @ c556533. The last three commits (the levels API, lint and CI; the committed golden test; this file) add no scanner behaviour, so the golden replay ran once more on the PR head instead of after each. | The owner asked for faster progress; those commits don't move scanner code. | A full pass after every commit (about 30 min each). |
| SP3 | **`TWSConnector` and `run_live` moved last, together with the entry point and the deploy files.** `sources/ibkr.py` imports `ib_insync` at module level (as `tws_connector.py` did). Moving it earlier would have made every mode import `ib_insync` through the scanner's re-exports, and `tests/deploy` checks the Dockerfile's COPY paths. | Every commit kept the import-time behaviour, and the full test suite passed. | Move `tws_connector.py` in its own commit. |
| SP4 | **`backend/scanner/__init__.py` exposes the old module's 86 names, loaded on first use** (a module-level `__getattr__`). So `import backend.scanner` imports nothing; the level names load pandas only; only the IBKR names load `ib_insync`. Checked: after `import backend.scanner`, none of pandas, `ib_insync` or `backend.datafeed` is loaded. | Tests, research and later callers keep one import, and no mode imports more than before. | An eager package API that imports every module, which would load pandas for `python -m backend.scanner.journal.summary` (stdlib-only today). |
| SP5 | **`main()` imports the live loop it runs inside the dispatch** (`sources.stream.run_live_stream` or `sources.ibkr.run_live`), as the old `run_live` imported `TWSConnector` lazily. `--date` and `--selftest` still need only pandas. | The same import-time behaviour per mode. | Import both loops at the top of `__main__.py`. |
| SP6 | **The live loops keep their own env reads** (owner's answer to Q1). `config.py` holds `PARQUET`, `DB_PATH` (`$SCANNER_DB`), `BAR_SOURCES`, `bar_source()` and `RECONNECT_BACKOFF_S`. | No source-text assertion changes (F2 in the PR 1 file). | Every env read in `config.py`. |
| SP7 | **Default paths:** `PARQUET` and `DB_PATH` resolve from the repo root (`parents[2]` of `backend/scanner/config.py`), and `journal_summary`'s `DEFAULT_DB` from `parents[3]`. Checked: they are the same paths as on `main`. **In the image** they are now `/app/database/…` instead of `/database/…`: unused there, since `SCANNER_DB` is set and the parquet isn't in the image. To run the selftest in the image, mount the parquet at `/app/database/ohlcv_data`. | The Mac and host paths don't change. | Compute them differently in the image. |
| SP8 | **The image** copies `backend/scanner` and `backend/levels`, and the entrypoint runs `python -m backend.scanner --live` from `/app`. No other deploy file changes: compose, the watchdog, backups and the S3 layout are as they were. The runbook gains §12 "Deploying the restructure" (rebuild, same volumes, no migration, rollback = the previous image tag). Its commands are updated for `main` after this PR, and §11 says what they were before. | Same container, same volumes, same flags; one start command. | none |
| SP9 | **The CLI's usage line** follows `sys.argv[0]`: it now says `usage: __main__.py …` instead of `usage: deterministic_scanner.py …` (on `--help` and argument errors only). `ArgumentParser()` moved verbatim. Logged as the one expected visible difference. | No change to the parser itself. | `ArgumentParser(prog="python -m backend.scanner")`. |
| SP10 | **Logger name:** `TWSConnector`'s logger is now `backend.scanner.sources.ibkr` (it follows the module). The scanner configures no logging, so only the message goes to stderr; the golden replay compares stderr byte for byte and found no difference. | Same output. | Pin the old logger name. |
| SP11 | **ruff's default rules on `backend/scanner` and `backend/levels`.** Fixed only where behaviour can't change: `a; b` and `try: x` lines split onto separate lines (E701/E702), and three unused imports in `sources/ibkr.py` (`util`, `Optional`, `ThreadFuture`). Kept with a `noqa` and its reason: the bare `except:` in `TWSConnector.connect`, which also catches `KeyboardInterrupt`; the unused `ticker =` result in `_subscribe_bars_sync`; and `replay_day`'s unused `day =`, which rejects a bad `--date` before anything else. | No line whose behaviour could change was touched. | Fix all 19 findings. |
| SP12 | **mypy.** `--strict` on `backend/levels` needed type annotations only (`Level` and `Alert` aliases, attribute and return types). With `from __future__ import annotations` they are never evaluated. On `backend/scanner`, CI fails only on a finding missing from `tools/mypy_scanner_baseline.txt`, compared by severity and message (file and line ignored, so moved code is not a change). The baseline was taken in CI's lint environment (mypy only), where `main` had 17 findings and the branch has 14: none new, 3 fewer (the levels annotations). With `ib_insync` installed, 5 more appear on both sides, so the environment matters. mypy is 2.4.0, the newest release at least two weeks old. | No type fix that could change behaviour; a reproducible gate. | Fix the scanner's 14 findings, which means code changes. |
| SP13 | **`pandas` is `Any` for mypy** (`ignore_missing_imports`): pandas ships no type information, and `pandas-stubs` isn't in the lock. | No new dependency. | Add `pandas-stubs`. |
| SP14 | **The level key** (owner's answer to Q2): `level_key(L) = f"{type}@{active_from:%Y-%m-%dT%H:%MZ}"`, documented in `backend/levels/source.py`. It is unique because `build_levels` makes at most one level per type and UTC day. Tested: unique within a build; the same key and `active_from` across a longer window and a later-starting one, while the ids move; the same value once the source period is complete in both. | Ids, and so Scanner state, are unchanged. | A new id scheme in the Scanner. |
| SP15 | **`LevelSource`** is a protocol with one implementation, `FrameLevelSource` (`levels_at`, `trigger_levels`). Nothing in the scanner or `backend/agents` uses it yet. | No behaviour change; the agents can adopt it in their own PR. | Wire it into the agents' snapshot now. |
| SP16 | **Tests** import `backend.scanner` instead of loading files by path, and `tests/scanner/conftest.py` (the path loader) is gone. Besides import lines, the test edits are: the dispatch test patches the live loops in their source modules (2 lines); `test_journal_epoch` patches `load_data` in `sources.replay` (1 line); `test_compose`'s entrypoint and COPY expectations name the new start command and paths; `test_xauusd_bars` and `test_spread_capture` import the scanner modules; and the research loader returns `backend.scanner`. Its test's assertion is unchanged, because `SCANNER_PATH` names the package file. | Assertions keep their rules; only moved names and paths change. | Rewrite the source-text tests now (F2, a follow-up PR). |
| SP17 | **New tests:** `tests/levels/` (19, synthetic bars, in CI), `tests/scanner/test_golden.py` (a synthetic digest in CI, plus 28 pinned real days that skip without the parquet) and `tests/scanner/test_module_size.py` (no module over 1,000 lines). The golden digests were computed on `main`'s scanner and on `backend.scanner` with the same helper, and were identical before they were pinned. | The pins describe `main`'s behaviour, not the branch's. | Pin the branch's own output. |
| SP18 | **History stays as written:** provenance notes such as "lifted from backend/deterministic_scanner.py" in `backend/datafeed/ingest/xauusd_bars.py`, research records, and the docstrings of moved code (`TelegramMessage` still says tests load the file by path). | Records and moved code stay verbatim. | Rewrite them. |

## Line counts (limit 1,000)

| Module | Lines |
|---|---|
| `backend/scanner/sources/ibkr.py` | 875 |
| `backend/scanner/sources/stream.py` | 439 |
| `backend/scanner/engine.py` | 231 |
| `backend/scanner/selftest.py` | 218 |
| `backend/scanner/journal/summary.py` | 185 |
| `backend/scanner/journal/outcomes.py` | 180 |
| `backend/scanner/telegram.py` | 170 |
| `backend/scanner/journal/writes.py` | 150 |
| `backend/scanner/journal/schema.py` | 132 |
| `backend/levels/touch.py` | 124 |
| `backend/levels/build.py` | 83 |
| `backend/levels/source.py` | 75 |
| `backend/scanner/__init__.py` | 69 |
| `backend/scanner/__main__.py` | 59 |
| `backend/scanner/market.py` | 52 |
| `backend/scanner/sources/replay.py` | 47 |
| `backend/scanner/config.py` | 32 |
| `__init__.py` files of `levels`, `journal`, `sources` | 14, 3, 2 |
