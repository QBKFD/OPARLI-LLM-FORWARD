# datafeed

XAUUSD 1-min bar feed: fetch bars from IBKR, publish them to a stream, record
them to a point-in-time SQLite store, read them back "as of t".

**Status: v1 in progress (branch `feat/datafeed-v1`). Nothing in production
uses this package.** The deployed scanner still owns its own IBKR connection.
Full usage docs arrive with step 7.

## Install

```
python3.13 -m venv .venv
.venv/bin/pip install -e '.[feed,replay,dev]'
```

## Modules

| Module | Status |
|---|---|
| `bus.py` | v1 (step 2) |
| `ingest/base.py`, `ingest/ibkr.py`, `ingest/xauusd_bars.py`, `ingest/__main__.py` | v1 (steps 2–4) |
| `recorder/` | v1 (step 6) |
| `store/schema.py`, `store/query.py` | v1 (step 5) |
| `ingest/gold_options.py` | planned, later PR |
| `ingest/calendar.py` | planned, later PR |
| `features/vol_block.py` | planned, later PR |
| `features/exposures.py` | planned, later PR |
| `store/sealed.py` | planned, later PR |
| `store/manifest.py` | planned, later PR |

Later root-level packages (not this package): `time/`, `levels/`, `stats/`, `scanner/`.

Bar semantics are defined in `docs/DATAFEED_NOTES.md`.
