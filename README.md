# OPARLI

A multi-agent, LLM-assisted research system for spot gold (XAUUSD), built so that
the same decision code runs in live and in backtest.

The system streams market data from Interactive Brokers into PostgreSQL, runs a
pipeline of specialised agents over that data, and exposes a FastAPI backend and
a Vue 3 frontend for monitoring and research. A separate research stack covers
Smart Money Concepts strategy validation and a jump-model market-regime
classifier.

This is a solo research project under active development. **It does not trade
autonomously today** — see [Project Status](#project-status). It is not
investment advice.

---

## Contents

- [Design Goals](#design-goals)
- [Architecture](#architecture)
- [Repository Layout](#repository-layout)
- [Data](#data)
- [Research Stack](#research-stack)
- [Testing](#testing)
- [Getting Started](#getting-started)
- [Running the System](#running-the-system)
- [API Surface](#api-surface)
- [Deployment](#deployment)
- [Project Status](#project-status)
- [Known Gaps](#known-gaps)
- [Disclaimer](#disclaimer)

---

## Design Goals

1. **One codebase, two execution modes.** The logic that trades live must be
   replayable bar-for-bar, with no parallel re-implementation to drift out of
   sync. Achieved by injecting the data source and the clock rather than reading
   `datetime.now()` and the live database directly.

2. **Separation of decision from analysis.** Each analyst produces an opinion; a
   meta-agent aggregates weighted opinions; a risk manager validates; an
   execution agent places. No agent both analyses and executes.

3. **Cost-aware LLM usage.** A cheap deterministic filter gates the expensive
   multi-agent analysis, so vision and language calls only run on candidates
   that clear a quality bar.

4. **Run on constrained hardware.** Target deployment is an Oracle Cloud Free
   Tier instance (1 GB RAM). Inter-agent messaging is an in-process thread-safe
   bus, not an external broker.

---

## Architecture

### Agent Pipeline

Agents live in [backend/agents/](backend/agents/) and share
[base_agent.py](backend/agents/base_agent.py), which defines the agent taxonomy
and the message envelope.

| Agent | Role | LLM |
|-------|------|-----|
| Scanner | Watches for trigger events, runs the Stage-1 filter, renders charts, fans out analysis requests | no |
| Technical Analyst | Multi-timeframe indicator analysis, confluence, cascade | no |
| Visual Analyst | Chart-pattern recognition over a rendered PNG | vision |
| Sentiment Analyst | News / macro reasoning | text |
| Meta Agent | Weighted vote over analyst opinions, governance, emits a trade request | optional |
| Risk Manager | Hard limits, position sizing, stop-loss, take-profit | no |
| Execution | Places and tracks orders | no |
| Trade Manager | Owns the in-flight trade lifecycle, hard exits and soft triggers | no |

Flow:

```
Scanner ──ANALYSIS_REQUEST──▶ Technical ─┐
                              Visual    ─┼─ANALYSIS_RESULT─▶ Meta Agent
                              Sentiment ─┘                       │
                                                                 │ weighted vote
                                                                 │ + governance
                                                                 ▼
                                                          TRADE_REQUEST
                                                                 │
                                                          Risk Manager
                                                                 │ approve / size
                                                                 ▼
                                                          TRADE_APPROVED
                                                                 │
                                                    Execution ──▶ Trade Manager
```

The Meta Agent blocks until **all three** analysts have reported for a symbol,
then decides once and clears the slot.

### Message Bus

[message_bus.py](backend/core/message_bus.py) is an in-process, thread-safe set
of per-agent priority mailboxes with broadcast and direct addressing. Queue
entries are `(-priority, sequence, message)`; the sequence counter is required
because two messages of equal priority would otherwise fall through to comparing
`Message` objects, which are not orderable. It also makes delivery FIFO within a
priority.

[agent_orchestrator.py](backend/core/agent_orchestrator.py) instantiates the
agents, registers them with the bus, activates them, and schedules the two that
run on a timer (Scanner and Trade Manager, 1 s each) via
[scheduler.py](backend/core/scheduler.py).

There is no external message broker. That is deliberate — it fits the 1 GB
memory budget.

### Backtestable Services

Decision logic lives in pure services under [backend/services/](backend/services/)
that take their data source as an argument:

- **Market data** — `MarketDataProvider` (abstract) with `LiveDataProvider`
  (PostgreSQL) and `HistoricalDataProvider` (in-memory frame). The historical
  provider **requires** `as_of` and slices with a binary search, so returning a
  bar at or after `as_of` is structurally impossible.
- **Clock** — `Clock` with `LiveClock` and `SimulatedClock`, so agents that need
  "now" receive an injected time. Currently only the Trade Manager takes one
  (see [Known Gaps](#known-gaps)).
- **Analysis** — `technical_service.run_technical_analysis(provider, symbol, as_of)`
  is the single source of truth for the technical decision.
  `MetaDecisionService` and `VisualAnalysisService` are the equivalents for meta
  and visual.
- **Risk & governance** — `risk_service.run_risk_validation(trade_request,
  account_state)` is a pure function; `governance.py` holds circuit breakers,
  iterative weight-bound enforcement and position-size clamping.

### Scanner: Two-Stage Filtering

**Stage 1 — deterministic, no LLM.** A candidate must satisfy *all* of:

- signal is `LONG` or `SHORT` (not `PASS`/`NEUTRAL`)
- confidence ≥ 0.5
- confluence ≥ 2 on the lead timeframe

*and at least one of:* price at a key level (within 0.3 % of support/resistance,
0.2 % of a swing, 0.1 % of a $50 psychological level) **or** a volume spike
(≥ 2× the 20-bar average).

Lead timeframe is recorded but not enforced.

**Stage 2 — full multi-agent analysis.** Renders charts and fans the request out
to all three analysts. Throttled to ≥ 3 min between scans, ≤ 500 scans/day,
≤ 15 full analyses/hour.

### Confluence and the Cascade

`ConfluenceChecker` scores **five directional voters** — RSI, Bollinger, EMA,
VWAP, Support/Resistance. Volume is deliberately *not* a voter: it is
non-directional, so it is reported as context (`volume_context`) rather than
occupying a slot in the denominator.

Confidence ladder: 3 agreeing → 0.60, 4 → 0.75, 5 → 0.90. Two or more opposing
voters subtract 0.15.

`TimeframeCascade` combines the five timeframes. Its gates are **inclusive
minimums** and are compared with `>=`:

| Constant | Value | Meaning |
|---|---|---|
| `LEAD_TF_MIN_CONFIDENCE` | 0.60 | 4h/1h needed to lead a Case A/B signal |
| `OPPOSITION_MIN_CONFIDENCE` | 0.70 | lower-TF conviction that counts as opposition |
| `MID_TF_MIN_CONFIDENCE` | 0.75 | 15m/5m needed to carry a Case C signal alone |

These values are drawn from the confidence ladder itself, so a strict `>`
comparison silently discards the most common valid signal (a clean 3-indicator
agreement lands on exactly 0.60). Measured on 2023 XAUUSD, that single character
was the difference between 5 signals per 2,000 evaluations and 127.

Measured cascade behaviour, hourly over 2023 (8,760 scans):

| Metric | Value |
|---|---|
| Signal rate | 764 / 8,760 = **8.7 %** (≈ 14.7 per week) |
| Direction split | 430 LONG / 334 SHORT |
| Confluence 3 / 4 | 5.88 % / 0.15 % of timeframe-evaluations |

---

## Repository Layout

```
algo_project/
├── backend/                 FastAPI backend, agents, services, routes
│   ├── agents/              Agent classes + base agent
│   ├── core/                Message bus, orchestrator, scheduler
│   ├── services/            Pure services: providers, clock, technical, risk,
│   │                        governance, sizing, indicators, streaming
│   │   └── analysis/        Meta decision, visual analysis, market structure
│   ├── routes/              REST + WebSocket endpoints
│   ├── middleware/          API key, rate limiting, security headers
│   ├── config/              DB config, LLM provider abstraction
│   ├── database/            Agent / risk / meta / evolution SQL schemas
│   ├── main.py              API entry point
│   └── run_system.py        Agent runtime entry point
├── frontend/                Vue 3 + Vite dashboard
├── regime_classifier/       Jump-model market-regime classifier (+ 13 tests)
├── scripts/                 Backtests, SMC validation, walk-forward, backfill,
│                            deploy, cloud setup
├── database/                SQL schema + historical OHLCV and news data
├── notebooks/               Research notebooks
├── tests/                   Pipeline integration tests
└── scraper_fxfac/           Forex Factory economic-calendar scraper
```

---

## Data

**Price** — roughly five years of 1-minute XAUUSD bars (~990k bars, ~688 trading
days, 2021–2026) plus GC futures, as CSV and Parquet under
[database/ohlcv_data/](database/ohlcv_data/). In Postgres, `ohlcv_historical_1min`
and a partitioned `ohlcv_realtime_1min` are unioned into an `ohlcv_1min` view,
with materialised views for 5m/15m/30m/1h/4h/1d refreshed every 5 minutes.

**News** — Forex Factory economic-calendar exports per year (2010–2026) under
[database/historical_news/](database/historical_news/), normalised to a UTC
Parquet file. The conversion is documented in
[DATA_NOTES_events.md](database/historical_news/DATA_NOTES_events.md): per-file
timezone forensics resolved through DST-desync evidence, price-spike
ground-truthing at minute 0, explicit drop-rule accounting, and a frozen
pre-registered `usd_tier1` event set. That file is the authority for anything
touching the news data.

> The news dataset is not yet consumed by any code path.

---

## Research Stack

Independent of the live pipeline, used to decide *what* the agents should look
for.

- **[backtest.ipynb](notebooks/backtest.ipynb)** — the main research notebook and
  the methodological standard for this repo: session definitions (UTC,
  DST-split), a key-level engine free of look-ahead by construction (levels carry
  an `active_from`), outcomes in $ and ATR units, a **mandatory matched-random
  baseline control**, pre-registered conditions with multiple-comparison
  correction, in-notebook unit tests that corrupt future bars to prove causality,
  and a TSMOM study with block-bootstrap significance and a **sealed evaluation
  window** behind an explicit flag.
- **[smc_validation_backtest.ipynb](notebooks/smc_validation_backtest.ipynb)** —
  Smart Money Concepts validation: order blocks, fair value gaps, BOS/CHoCH,
  liquidity sweeps, with displacement, mitigation and higher-timeframe filters.
- **[multi_agent_simulation.ipynb](notebooks/multi_agent_simulation.ipynb)** —
  replays the agent pipeline over historical CSV using the shared services.
  Note it defines its own lightweight risk manager, so its P&L is not the
  production risk model.
- **[scripts/](scripts/)** — runnable counterparts (SMC sweeps, ORB and ORB-fade,
  BOS/CHoCH, FVG-leads-OB, train/test splits, walk-forward). Strategies that
  failed validation are kept under
  [scripts/no_go_strategies/](scripts/no_go_strategies/) as a record.
- **[regime_classifier/](regime_classifier/)** — a three-state
  (trending / ranging / volatile) statistical jump model: jump penalty tuned to
  downstream strategy Sharpe rather than log-likelihood, sparse feature
  selection, dwell-time constraints, label alignment across folds, and a
  random-forest next-regime layer. Walk-forward validation with embargo.

---

## Testing

```bash
# 9 pipeline tests — needs only backend/requirements.txt
backend/venv/bin/python -m pytest tests/ -q

# 13 classifier tests — needs regime_classifier/requirements.txt, which is a
# separate dependency set (joblib, scikit-learn, jumpmodels) not installed by
# backend/requirements.txt
pip install -r regime_classifier/requirements.txt
pytest regime_classifier/ -q
```

[tests/test_pipeline_integration.py](tests/test_pipeline_integration.py) asserts
the minimum the architecture claims: **one scan on fixed historical data
produces one decision that reaches the Risk Manager**. It uses the real message
bus, real routing, real agents, the real cascade, the real `MetaDecisionService`
and the real `run_risk_validation`. The price fixture is synthetic and seeded, so
the suite needs no database, no network and no data files.

Two seams are stubbed and documented in the test module: the Scanner's
`get_latest_analysis` and `generate_charts`, both of which read Postgres with no
`as_of` bound, and the Visual/Sentiment LLM clients.

---

## Getting Started

Prerequisites: Python 3.12+, Node.js 18+, PostgreSQL, and — for live data —
Interactive Brokers TWS or IB Gateway.

```bash
# 1. Backend
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Database
psql "$DATABASE_URL" -f ../database/schema.sql
for f in backend/database/*.sql; do psql "$DATABASE_URL" -f "$f"; done

# 3. Frontend
cd ../frontend && npm install
```

Configuration is read from `backend/.env`; see [.env.example](.env.example):

```
DATABASE_URL=postgresql://user:password@localhost:5432/oparli_database
API_KEY=your-generated-api-key
GOOGLE_API_KEY=your-gemini-api-key       # development
ANTHROPIC_API_KEY=your-anthropic-api-key # production
ENV=development                          # development = Gemini, production = Claude
IB_HOST=127.0.0.1
IB_PORT=4002
IB_CLIENT_ID=1
```

The frontend reads its own `frontend/.env` (`VITE_API_BASE_URL`, `VITE_WS_URL`,
`VITE_API_KEY`).

> Do not commit `.env`. Rotate any key that has ever been committed — removing a
> secret from the working tree does not remove it from git history.

---

## Running the System

```bash
cd backend && source venv/bin/activate
uvicorn main:app --reload    # API, WebSocket streaming, dashboard
python run_system.py         # agent runtime (orchestrator + scheduler)

cd frontend && npm run dev   # dashboard
```

`scripts/start-all.sh` opens both backend and frontend in separate macOS
Terminal windows.

Research runs:

```bash
python scripts/backtest_agents.py --start 2023-01-01 --end 2024-01-01
python scripts/demo_backtestable_technical.py    # provider causality demo
python scripts/run_walk_forward.py               # regime walk-forward
python scripts/smc_validation_train_test.py
```

---

## API Surface

[backend/main.py](backend/main.py) mounts seven routers:

| Prefix | Purpose |
|---|---|
| `/api/live-trading` | Market state, order and position control |
| `/api/data` | Historical backfill, gap detection, import/export |
| `/api/backtest` | Pre-computed sweep-strategy results |
| `/api/regime` | Regime labels for the chart overlay |
| `/api/validation` | Walk-forward validation reports |
| `/api/dashboard` | Aggregated monitoring, ad-hoc analysis run |
| `/api/auth` | JWT authentication |

Plus `/api/chart-data/{symbol}/{timeframe}`, `/api/symbols`, `/api/latest`,
`/api/stats`, `/api/streaming/*` and a `/ws/live-data/{symbol}` WebSocket.

Requests pass through API-key, rate-limiting and security-header middleware
([security.py](backend/middleware/security.py)) with validators for symbol,
timeframe and limit.

---

## Deployment

Target is a single small cloud instance.
[scripts/deploy.sh](scripts/deploy.sh) builds the frontend and rsyncs the build
and backend source over SSH, then restarts the API.
[scripts/setup-oracle-cloud.sh](scripts/setup-oracle-cloud.sh) provisions the
host. [scripts/connect.sh](scripts/connect.sh) opens a session.

---

## Project Status

Active development by a single author.

**Working and verified:**

- The agent pipeline completes end to end — a scan produces a decision that
  reaches the Risk Manager and is validated. This is covered by the integration
  test and was not true before it existed.
- The technical cascade produces signals at ≈ 8.7 % of scans (≈ 14.7/week).
- Data ingestion, backfill, gap detection, the chart API and the Vue dashboard.
- The research notebooks and the regime classifier, both independently tested.

**Not working yet:**

- **The system does not trade.** With only the Technical analyst voting, the
  weighted score ceiling is `0.50 × 0.75 = 0.375` against a 0.60 Meta gate, so
  every decision is `PASS`. `scripts/backtest_agents.py` takes **0 trades** on
  2023 in its default (technical-only) mode. Renormalising the weights over
  participating analysts yields 214 trades/year (≈ 4.1/week, 46.3 % win rate,
  +22 R) — a diagnostic measurement, not an approved change, and **not evidence
  of edge**.
- The Sentiment Analyst has no live news source.
- The Meta Agent's evolution mode runs on placeholder performance data.

---

## Known Gaps

Honest list of where the code does not match the architecture above.

1. **Clock injection is aspirational.** `SimulatedClock` has no call sites.
   Nine modules read `datetime.now()`/`utcnow()` directly, including
   `MetaDecisionService`, which stamps the *real* current date into the LLM
   prompt during historical backtests.
2. **The Scanner's Stage-1 data path bypasses the provider.**
   `TechnicalAnalystAgent.get_latest_analysis` and `ChartGenerator._fetch_ohlcv`
   query Postgres directly with no `as_of`, so the Scanner cannot be replayed.
3. **Provider asymmetry.** `HistoricalDataProvider` fetches a 1.5× buffer of
   1-minute bars; `LiveDataProvider` does not. With weekend gaps, live and
   backtest can compute different indicator values at the same instant.
4. **Two regime systems.** The rigorous jump model in `regime_classifier/` is
   used only for the chart overlay and research. Live decisions are gated by the
   rule-based `services/regime_detector.py`, which returns `RANGING` ~94 % of the
   time — so "regime-adaptive thresholds" are close to fixed thresholds. The
   `/api/regime` endpoint also fits in-sample and has no `as_of`, so its labels
   must not feed decisions as they stand.
5. **The orchestrator is not started by the API.** `run_system.py` starts it;
   `main.py` does not.
6. **`/api/dashboard/analyze` is a parallel implementation.** It re-implements
   the pipeline outside the bus and bypasses the governance gate.
7. **`executed_trades` schema drift.** The `INSERT` in `execution_agent.py`
   names six columns the table does not have.
8. **`Meta → Execution` mid-trade actions are unwired.** Meta sends
   `MID_TRADE_ACTION`; Execution has no handler. Left visible rather than
   papered over with a no-op.
9. **Unreachable modules** (~2,400 lines, nothing imports them):
   `agents/position_manager.py`, `services/meta_agent_strategist.py`,
   `services/performance_tracker.py`, `services/screenshot_service.py`,
   `services/sweep_backtest.py`, `config/news_events.py`.
10. **`PyJWT` is declared but not installed** in `backend/venv`, so
    `main.py` currently fails to import (`auth_routes.py` does `import jwt`).
    Fix: `backend/venv/bin/pip install PyJWT`.
11. **Unused dependencies:** `python-decouple`, `websockets`, `python-dateutil`.
12. **Two dependency sets.** `regime_classifier/` requires `joblib`,
    `scikit-learn` and `jumpmodels`, none of which are in
    `backend/requirements.txt` — so the classifier cannot run inside
    `backend/venv` as installed, even though `backend/routes/regime_routes.py`
    imports it. That endpoint will fail at import in a clean environment.

---

## Disclaimer

For research and educational purposes only. Not financial advice and not a
recommendation to buy or sell any instrument. Trading leveraged instruments such
as spot gold carries substantial risk of loss. Use against a live, funded account
entirely at your own risk. The authors accept no liability for any losses.
