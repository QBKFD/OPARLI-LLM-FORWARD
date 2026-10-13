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
