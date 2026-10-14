# OPARLI

Research and tooling for spot gold (XAUUSD). One small thing runs in
production: a deterministic level scanner that alerts on Telegram and logs
the owner's decisions. Everything else is research, a data feed that is built
but not deployed, or legacy code kept as a port source.

It does not trade. Not investment advice.

## What runs in production

**The deterministic level scanner.**

- **Code:** `backend/deterministic_scanner.py`, plus the IBKR connector
  `backend/services/tws_connector.py`.
- **Deployment:** a two-container Docker stack (IB Gateway + scanner) from
  `deploy/scanner/` on one AWS EC2 host. A host-cron `watchdog.sh` probes the
  gateway every 5 minutes, alerts on Telegram and re-logs the gateway in when
  it stays down.
- **What it does:**
  1. Builds 1-min bars from IBKR bid/ask midpoints.
  2. Computes 12 level types (previous day/week high/low, day/week open,
     Asia/London/NY session high/low; UTC days and sessions).
  3. Alerts Telegram on the first touch of a level.
  4. Records `TAKE`/`SKIP` replies in an append-only SQLite journal, and fills
     in counterfactual 1h/4h MFE/MAE later.
- **What it doesn't use:** no LLM, no Postgres, no agents.
- **Data it keeps:** bars live only in memory (about 10 days, reloaded from
  IBKR on every restart). Only the decision journal is persisted.

```
python backend/deterministic_scanner.py --selftest   # needs pandas, pyarrow and the local parquet
```

## Folders

| Folder | Status | Contents |
|---|---|---|
| `backend/` | **production** | `deterministic_scanner.py`, `services/tws_connector.py`, `services/__init__.py`. Nothing else |
| `deploy/scanner/` | **production** | Dockerfile, compose file (IB Gateway + scanner), entrypoint, host-cron `watchdog.sh`, `.env.example` |
| `datafeed/` | built, **not deployed** | Datafeed v1: IBKR or parquet-replay bars → Redis stream → recorder → point-in-time SQLite store, read back with `bars_before(symbol, t, lookback)`. See [datafeed/README.md](datafeed/README.md) |
| `deploy/datafeed/` | local only | Compose file for running the datafeed locally in replay mode. Never deployed |
| `tests/` | active | `tests/datafeed/`: the datafeed test suite |
| `research/` | active | Pre-registered studies, each with its PREREG/DECISIONS/REPORT: `levels`, `levels_confirm`, `levels_diag`, `vol_block`, `vol_features` (r1–r3), `trend_features`, `regime_v2`. Shared code in `research/_shared/`. Data from 2026-10-01 is sealed ([research/SEALING.md](research/SEALING.md)) |
| `tools/` | utilities | `tools/data/fetch_data_xauusd.py` (IBKR 1-min MIDPOINT history fetch: data provenance), `tools/scraper_fxfac/` (Forex Factory calendar scraper, for rebuilding the historical event file) |
| `database/` | data notes | `historical_news/DATA_NOTES_events.md` (calendar timezone rules). The data files themselves (`ohlcv_data/`, calendar exports) are local and not in git |
| `legacy/` | **port source only** | The old multi-agent framework (FastAPI app, bus agents, services, frontend, regime classifier, notebooks, old scripts). Unmaintained, not deployed, never imported, excluded from tests. See [legacy/README.md](legacy/README.md) and [legacy/PORT_CANDIDATES.md](legacy/PORT_CANDIDATES.md) |
| `docs/` | docs | Survey and work notes (below) |

## Tests

```
python3.13 -m venv .venv && .venv/bin/pip install -e '.[feed,replay,dev]'
.venv/bin/pytest            # research/ + tests/, never legacy/
```

- **Research suites need extra packages.** They also import scipy, matplotlib
  and numba, and several read the local parquet
  (`database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet`, not in git).
- **Datafeed tests that need a throwaway Redis** run only when
  `REDIS_TEST_URL` is set.

## Docs

- [docs/ARCHITECTURE_MAP.md](docs/ARCHITECTURE_MAP.md),
  [docs/REPO_MAP.md](docs/REPO_MAP.md): surveys of the repo **before** the
  phase 1 cleanup (old paths).
- [docs/DATAFEED_NOTES.md](docs/DATAFEED_NOTES.md): the scanner's bar
  contract, datafeed decisions, open questions, scanner-switch plan.
- [docs/CLEANUP_NOTES.md](docs/CLEANUP_NOTES.md): what the cleanup moved,
  deleted and verified (old → new paths).

## Disclaimer

For research and educational purposes only. Not financial advice and not a
recommendation to buy or sell any instrument. Trading leveraged instruments
such as spot gold carries substantial risk of loss.
