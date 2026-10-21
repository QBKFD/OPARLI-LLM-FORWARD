# OPARLI

Research and tooling for spot gold (XAUUSD) on 1-minute bars. One service runs
in production: a deterministic level scanner that sends Telegram alerts and
journals the owner's TAKE/SKIP replies. Nothing here trades.

Diagrams and a map of every file and store:
[docs/ARCHITECTURE_CURRENT.md](docs/ARCHITECTURE_CURRENT.md).

## What runs

- **Production: the level scanner**
  - **Code:** `backend/scanner/` (`python -m backend.scanner`), on the level
    engine in `backend/levels/`.
  - **Deployment:** the `deploy/scanner/` Docker stack (IB Gateway + scanner)
    on one EC2 host, plus host crons `watchdog.sh` (every 5 min) and
    `backup.sh` (daily, to S3).
  - **Alerts:** on the first touch of 12 level types: previous day/week
    high/low, day/week open, and Asia/London/NY session high/low.
  - **Journal:** replies go to SQLite. Every 5 minutes it marks unanswered
    alerts MISSED, adds 1h/4h counterfactuals and scores TAKEs with
    `scoring/`.
- **The host's commit isn't tracked in git.** On 2026-10-18 the host ran
  branch `pipeline-fixes-and-cleanup` (PR #18). That branch has none of
  main's journal migrations, TAKE scoring, backups or stream mode.
- **Built, not running:**
  - the datafeed `stream` profile (opt-in);
  - the agents graph;
  - the LLM agents, which live on the unmerged `feat/agents-*` branches.

## Packages

| Path | Status |
|---|---|
| `backend/scanner/`, `backend/levels/` | **production**: the scanner (CLI, live loops, bar sources incl. IBKR, journal, read-only daily summary) and the pure level engine |
| `deploy/scanner/` | **production** stack: compose, entrypoint, watchdog, backup/restore; the `stream` profile is opt-in |
| `scoring/` | **production** inside the scanner image: TAKE outcomes. `score_neutral` (k·σ bracket) has no caller yet |
| `risk/` | `bracket` is used through `scoring`; `sizing` has no caller |
| `datafeed/` | built and tested, runs only in the `stream` profile: IBKR → Redis → point-in-time SQLite (`bars_before`); HAR σ in `features/vol_block.py` |
| `agents/` | v0 skeleton, not deployed: LangGraph graph, snapshot, store. The three voters are stubs that abstain; no LLM, no trigger |
| `research/` | 12 pre-registered studies, below; index in [research/README.md](research/README.md) |
| `tools/` | IBKR history fetch, calendar scraper, σ calibration, IBKR duration check |
| `tests/` | datafeed, scanner, deploy, agents, risk, scoring |
| `deploy/datafeed/` | local replay stack only |
| `legacy/` | the old agent framework: port source, never imported ([legacy/PORT_CANDIDATES.md](legacy/PORT_CANDIDATES.md)) |
| `docs/` | architecture, [deploy runbook](docs/DEPLOY_RUNBOOK.md), design notes |

## Study verdicts

| Study | Verdict |
|---|---|
| `vol_features` r1–r3 | 3 volatility features don't replace the 13 at 4h (r1); r2 and r3 fail the no-clock rule, r3 narrowly |
| `vol_block` | L, S_hd, T locked as an engineering decision |
| `trend_features` | not recommended (the 24h rule fails by 0.00007) |
| `regime_v2` | jump-model regimes add nothing over L terciles |
| `levels` | 0 of 6 hypotheses supported; PDH/PDL breaks continue less than placebo breaks |
| `levels_confirm` | rejected: near-open breaks continued more, not less (+11.3 pp) |
| `levels_diag` | stopped at machinery validation (bootstrap coverage outside 93–97 %) |
| `sigma_compare` | HAR σ replaces the plug-in (QLIKE −18 % at 60 min, −16 % at 240) |
| `agents_v1` | draft PREREG; no agent code |
| `level_target` | spec only |

Data periods ([research/SEALING.md](research/SEALING.md)): 2024-01-01 to
2026-01-30 is spent (read once by `levels_confirm`), and data from 2026-10-01
is sealed.

## Install and test

```
python3.13 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements/requirements.lock
.venv/bin/pip install --no-deps --no-build-isolation -e .
.venv/bin/pytest -m "not ibkr"
.venv/bin/python -m backend.scanner --selftest
```

- **Redis tests** run when `REDIS_TEST_URL` points at a throwaway database,
  e.g. `redis://localhost:6379/15` (the tests flush it). Otherwise they skip.
- **Data tests** need the untracked files in `database/ohlcv_data/` and
  `research/*/cache/`, and skip themselves without them. The selftest needs
  the parquet. CI has none of these files, so they run only locally.
- **IBKR tests** are manual: `-m ibkr` with `DATAFEED_IBKR_LIVE=1`, against a
  paper gateway only.

## Disclaimer

For research and educational purposes only. Not financial advice and not a
recommendation to buy or sell any instrument. Trading leveraged instruments
such as spot gold carries substantial risk of loss.
