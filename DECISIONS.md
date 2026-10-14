# Decisions taken without the owner (ci/github-actions)

Overnight run, 2026-10-15, unattended. Rule: on any decision, take the
conservative option, log it here, continue. The workflow is
`.github/workflows/ci.yml`.

| # | Decision | Conservative because | Alternative |
|---|---|---|---|
| C1 | Cut from `main` (05fe838), as asked. The stream and deploy branches add `tests/scanner` and `tests/deploy` to `testpaths`, so CI picks them up when they merge; their Redis tests use the same service container. | CI tests what is on `main` now. | Cut from `feat/deploy-stack`. |
| C2 | The **selftest runs only when the parquet is present**. It isn't in git (`database/ohlcv_data` is untracked), so on GitHub the step prints a warning annotation instead of failing. Checked locally: `main`'s selftest passes with the parquet. | CI doesn't go red over missing data, and the gap is visible on every run, not hidden. | (a) a data-free selftest subset (cases 2–9, 11, 12 use synthetic data); (b) a small tracked fixture parquet. Both change code or data and need the owner. |
| C3 | ruff in two passes. **Errors only** (`E9,F63,F7,F82`: syntax errors, undefined names) on the whole repo; **default rules** (`E4,E7,E9,F`, minus E741 because `l` is the codebase's OHLC low) on `agents`, `risk`, `tools` and `datafeed`. `backend/`, `research/` and `tests/` aren't style-linted: they carry 87 existing style findings (`l`, semicolons, lambdas) and their code is kept as run or calibrated. `legacy/` is excluded. | Green on `main` without editing any code, while still catching real errors. | Lint everything and fix 90 style findings, which touches the calibrated scanner and run research code. |
| C4 | One per-file ignore: F821 in `research/levels/run_levels.py`. `frames` is deleted (`del frames`) only after the last call of the closure that appends to it: a static false positive, not a runtime bug. | No edit to a study's code after its run. | Rewrite the closure. |
| C5 | **shellcheck at error severity.** `main`'s `watchdog.sh` and `entrypoint.sh` have 4 warning/info findings (SC2164, SC1090, SC2034, SC2086). `feat/deploy-stack` already fixes them and is clean at the default severity. | No edits to files `feat/deploy-stack` rewrites (no merge conflict). | Raise to `-S warning` once `feat/deploy-stack` is merged (one-line change). |
| C6 | **gitleaks** runs as the pinned release binary (8.30.1, SHA-256 checked), over the full history (`fetch-depth: 0`), not as `gitleaks-action`. | No third-party action with repo access; `gitleaks-action` needs a license key for organisation accounts. | `gitleaks/gitleaks-action@v2` with `GITLEAKS_LICENSE`. |
| C7 | Actions pinned by **commit SHA** (checkout v4.2.2, setup-python v5.6.0). `permissions: contents: read`. No secrets. Runs on every push and PR. `ubuntu-24.04`, Python 3.13 (the lock's version). ruff pinned to 0.12.10. All versions are older than two weeks. | Supply-chain hygiene; nothing the workflow can write or leak. | Floating tags (`@v4`). |
| C8 | IBKR tests are deselected (`-m "not ibkr"`); they also skip without `DATAFEED_IBKR_LIVE=1`. | Never a gateway connection from CI. | none |
