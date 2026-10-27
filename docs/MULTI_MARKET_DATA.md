# Multi-market 1-min data, 2016-01-01 .. 2026-09-30

**Status:** seals and planned manifest entries committed first, before any
download.

| Instrument | IBKR contract | whatToShow | File |
|---|---|---|---|
| XAGUSD | XAGUSD CMDTY SMART USD | MIDPOINT | `database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet` |
| EURUSD | EUR CASH IDEALPRO USD | MIDPOINT | `database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet` |
| USDJPY | USD CASH IDEALPRO JPY | MIDPOINT | `database/ohlcv_data/USDJPY_ibkr_1min_20160101_20260930.parquet` |
| GBPUSD | GBP CASH IDEALPRO USD | MIDPOINT | `database/ohlcv_data/GBPUSD_ibkr_1min_20160101_20260930.parquet` |
| USDCAD | USD CASH IDEALPRO CAD | MIDPOINT | `database/ohlcv_data/USDCAD_ibkr_1min_20160101_20260930.parquet` |
| USDSEK | USD CASH IDEALPRO SEK | MIDPOINT | `database/ohlcv_data/USDSEK_ibkr_1min_20160101_20260930.parquet` |
| USDCHF | USD CASH IDEALPRO CHF | MIDPOINT | `database/ohlcv_data/USDCHF_ibkr_1min_20160101_20260930.parquet` |
| DXY_PROXY | derived from the six FX pairs | — | `database/ohlcv_data/DXY_PROXY_1min_20160101_20260930.parquet` |

**Seals:** `research/SEALING.md`, "Instrument periods":
- dev: 2016–2023;
- holdout: 2024-01-01 .. 2026-09-30;
- sealed: from 2026-10-01.

Every file is "unused by any study" until a PREREG names it
(`research/data_guard.py`).

**DXY proxy:** DXY = 50.14348112 × EURUSD^−0.576 × USDJPY^0.136 ×
GBPUSD^−0.119 × USDCAD^0.091 × USDSEK^0.042 × USDCHF^0.036. It is built only
on the minutes where all six pairs have a bar.

**Gold files are not touched.**
