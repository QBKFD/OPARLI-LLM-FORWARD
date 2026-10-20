# OPARLI

Research and tooling for spot gold (XAUUSD) on 1-minute bars. One service runs
in production: a deterministic level scanner that sends Telegram alerts and
journals the owner's TAKE/SKIP replies. Nothing here trades.

Diagrams and a map of every file and store:
[docs/ARCHITECTURE_CURRENT.md](docs/ARCHITECTURE_CURRENT.md).

## What runs

- **Production: the level scanner**
  - **Code:** `backend/deterministic_scanner.py`.
  - **Deployment:** the `deploy/scanner/` Docker stack (IB Gateway + scanner)
    on one EC2 host, plus host crons `watchdog.sh` (every 5 min; it also
    writes `health.json` for the status board) and `backup.sh` (daily, to
    S3).
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
  - the agents v2 graph and its LLM layer: nothing deployed runs them, and
    the prompts are still templates. LangGraph Studio runs the graph
    locally on replay data, for debugging.

## Packages

| Path | Status |
|---|---|
| `backend/` | **production**: the scanner, `journal_summary.py` (read-only daily summary), `services/tws_connector.py` (IBKR) |
| `deploy/scanner/` | **production** stack: compose, entrypoint, watchdog, backup/restore; the `stream` profile is opt-in |
| `scoring/` | **production** inside the scanner image: TAKE outcomes. `score_neutral` (k·σ bracket) has no caller yet |
| `risk/` | `bracket` is used through `scoring`; `sizing` has no caller |
| `datafeed/` | built and tested, runs only in the `stream` profile: IBKR → Redis → point-in-time SQLite (`bars_before`), with the bid/ask spread of live bars; HAR σ in `features/vol_block.py` |
| `agents/` | v2, built and tested, **not deployed** ([diagrams](docs/ARCHITECTURE_CURRENT.md#d-agents-v2)). Graph: snapshot → gate → visual, technical, sentiment, GEX → Meta → discussion → Meta → risk (stub) → store. LLM client layer (official SDK, cache, budget, kill switch, ledger) and epochs. No runner; prompts are templates. Without an LLM client the three LLM voters are stubs, and GEX always abstains. Technical v1: not built |
| `research/` | 12 pre-registered studies, below; index in [research/README.md](research/README.md) |
| `tools/` | **production** status board (`tools/status`, reads the watchdog's `health.json`); LangGraph Studio for the agents graph (local, replay only, [docs](docs/LANGGRAPH_STUDIO.md)); spread by hour; IBKR history fetch, calendar scraper, σ calibration, IBKR duration check |
| `tests/` | datafeed, scanner, deploy, agents, risk, scoring, status |
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
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install --no-deps --no-build-isolation -e .
.venv/bin/pytest -m "not ibkr"
.venv/bin/python backend/deterministic_scanner.py --selftest
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
