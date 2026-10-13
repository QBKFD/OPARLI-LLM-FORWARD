# legacy/

The old multi-agent trading framework and everything that only served it.
It was moved here in the phase 1 cleanup (`git mv`, history kept; the state
before the move is tagged `legacy-agents-v0`).

**Status: unmaintained. Not deployed. Excluded from tests and CI.**

- Nothing in production imports it. Production is the deterministic scanner:
  `deploy/scanner/*`, `backend/deterministic_scanner.py`,
  `backend/services/tws_connector.py`.
- Nothing outside `legacy/` may import from `legacy/`.
- Root `pytest` never collects it. The old agent tests in `tests_agents/` no
  longer run from here (their `conftest.py` still points at `<repo>/backend`),
  and that's intended.
- Paths inside these files still refer to the pre-move layout. They aren't
  fixed: the code here isn't meant to run.

**Why keep it.** It's the port source for the LangGraph agent rebuild. Pieces
are ported *out* of here into new, tested code, never run in place.
[PORT_CANDIDATES.md](PORT_CANDIDATES.md) lists, file by file, what's worth
porting, what must change first, and the evidence (file:line, checked against
this code).

| Folder | Was | What it is |
|---|---|---|
| `backend_v0/` | `backend/` (minus the 3 production files) | FastAPI app (`main.py`, `routes/`, `middleware/`), message bus + scheduler (`core/`), 8 bus agents + base class + unused position manager (`agents/`), analysis/risk/data services (`services/`), config incl. `llm_provider.py` and `news_events.py`, Postgres access (`config/database.py`, `database/`), paper simulator (`live_trading.py`) |
| `frontend/` | `frontend/` | Vue dashboard for the agent API |
| `regime_classifier/` | `regime_classifier/` | Jump-model/RF regime classifier. Research uses byte-identical copies of `regime_features.py` and `regime_config.py` in `research/_shared/regime_features/`, not these |
| `notebooks/` | `notebooks/` | Original backtest notebooks; the scanner's level engine came from `backtest.ipynb` |
| `tests_agents/` | `tests/` (3 files) | Tests for the agent pipeline and Meta renormalisation |
| `scripts/` | `scripts/` | SMC/regime/agent backtests, rejected strategies (`no_go_strategies/`), Postgres backfill/import, server setup and deploy scripts |
| `database/` | `database/schema.sql` | Postgres schema for the agent framework |
