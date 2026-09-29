# Housekeeping (Phase A1)

Report only. None of the files listed below were edited.

## Round 1 report restored

`git checkout -- research/vol_features/REPORT.md` was run (2026-09-29, branch
`research/trend-features`). The file is back to its committed version (190 lines).

## References to the missing data files

`xauusd_5year_1min.csv`, `XAUUSD_COMPLETE*.csv` and `xauusd_gap_filled.csv` are
not in `database/ohlcv_data/`. `data/snapshots/` and `database/snapshots/`,
which many scripts point at, don't exist either. So every reference below
currently points at a missing file. The data files were gitignored and never
committed, so git can't restore them.

16 files reference them (`grep -rn` over `*.py` and `*.ipynb`, excluding venv):

| file:line | reference | path used |
|---|---|---|
| `backend/services/sweep_backtest.py:24` | XAUUSD_COMPLETE.csv | PROJECT_ROOT/data/snapshots/XAUUSD_COMPLETE.csv (Path join) |
| `scripts/demo_backtestable_technical.py:30` | XAUUSD_COMPLETE.csv | database/ohlcv_data/XAUUSD_COMPLETE.csv |
| `scripts/smc_bos_choch_validation.py:26` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/smc_bos_choch_validation.py:27` | XAUUSD_COMPLETE.csv | data/snapshots/XAUUSD_COMPLETE.csv |
| `scripts/smc_validation_train_test.py:33` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/smc_validation_train_test.py:34` | XAUUSD_COMPLETE.csv | data/snapshots/XAUUSD_COMPLETE.csv |
| `scripts/smc_fvg_leads_ob_validation.py:31` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/smc_fvg_leads_ob_validation.py:32` | XAUUSD_COMPLETE.csv | data/snapshots/XAUUSD_COMPLETE.csv |
| `scripts/smc_backtest_orb.py:36` | XAUUSD_COMPLETE.csv | data/snapshots/XAUUSD_COMPLETE.csv |
| `scripts/no_go_strategies/smc_backtest_regime.py:25` | xauusd_5year_1min.csv | data/snapshots/xauusd_5year_1min.csv |
| `scripts/smc_backtest_orb_fade.py:27` | XAUUSD_COMPLETE.csv | PROJECT_ROOT/data/snapshots/XAUUSD_COMPLETE.csv (Path join) |
| `scripts/smc_backtest_sweeps.py:25` | xauusd_5year_1min.csv | data/snapshots/xauusd_5year_1min.csv |
| `scripts/visualize_sweep_trades.py:37` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/visualize_sweep_trades.py:38` | XAUUSD_COMPLETE.csv | data/snapshots/XAUUSD_COMPLETE.csv |
| `scripts/no_go_strategies/smc_backtest_orderblocks.py:18` | xauusd_5year_1min.csv | data/snapshots/xauusd_5year_1min.csv |
| `scripts/backtest_agents.py:60` | XAUUSD_COMPLETE.csv | database/ohlcv_data/XAUUSD_COMPLETE.csv |
| `notebooks/backtest.ipynb:117` | XAUUSD_COMPLETE.csv | /Users/x/Desktop/algo_project/database/ohlcv_data/XAUUSD_COMPLETE.csv |
| `notebooks/backtest.ipynb:1933` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/backfill/fetch_data_xauusd.py:3` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/backfill/fetch_data_xauusd.py:28` | XAUUSD_COMPLETE.csv | ../../database/snapshots/XAUUSD_COMPLETE.csv |
| `scripts/backfill/fetch_data_xauusd.py:145` | XAUUSD_COMPLETE.csv | (name only, in a message or comment) |
| `scripts/no_go_strategies/smc_backtest_fvg.py:21` | xauusd_5year_1min.csv | data/snapshots/xauusd_5year_1min.csv |
| `notebooks/smc_validation_backtest.ipynb:121` | XAUUSD_COMPLETE.csv | ../data/snapshots/XAUUSD_COMPLETE.csv |
| `notebooks/smc_validation_backtest.ipynb:138` | XAUUSD_COMPLETE.csv | ../data/snapshots/XAUUSD_COMPLETE.csv |

Notes:
- `backend/services/sweep_backtest.py` is in `backend/`, which this work may
  not change. It is listed only.
- `notebooks/backtest.ipynb` is also modified in the working tree (an uncommitted
  change that isn't mine). It is left as is.
- `scripts/backfill/fetch_data_xauusd.py` *appends* to
  `database/snapshots/XAUUSD_COMPLETE.csv`. Running it now would create a new
  partial file there.
- The research code in `research/` uses only `XAUUSD_1min_UTC_clean.parquet` and
  `XAUUSD.txt`, and neither is affected.
