# Data sealing

## History

- Until 2026-09-29, all data from **2024-01-01** onward was sealed (every study
  under `research/` asserted timestamps < 2024-01-01).
- **2024-01-01 to 2026-01-30** is used for the levels confirmation test,
  `research/levels_confirm/` (pre-registration commit: `305d1d0`),
  by decision of the owner. After that test it is spent: seen data.

## Current seal

**NEW SEALED PERIOD: all data from 2026-10-01 onward, reserved for the final
strategy test. Nothing may read it until then.**

Enforced by `research/sealing.py` (`assert_before_global_seal`), which every
loader calls from now on, and tested in `research/test_sealing.py`.

## Instrument periods (owner, 2026-10-28)

Every instrument below has the same periods, enforced by
`sealing.assert_period(symbol, ts, period)` (tested in `test_sealing.py`):

| Period | Range (UTC) | Use |
|---|---|---|
| dev | 2016-01-01 .. 2023-12-31 | development |
| holdout | 2024-01-01 .. 2026-09-30 | read once, by a study whose committed PREREG names the file |
| sealed | from 2026-10-01 | the final strategy test; nothing may read it (`assert_before_global_seal`) |

Instruments: XAUUSD (gold, as its studies already use these periods), XAGUSD,
EURUSD, USDJPY, GBPUSD, USDCAD, USDSEK, USDCHF, and DXY_PROXY (built from the
six FX pairs).

- **The new instruments' 1-min files** (`database/ohlcv_data/<SYMBOL>_ibkr_1min_20160101_20260930.parquet`)
  are listed in the data manifest with `"status": "unused by any study"`.
  `research/data_guard.py` refuses each of them to any study whose committed
  PREREG doesn't name it.
- **Gold's own history still holds:** 2024-01-01 .. 2026-01-30 was spent once
  by `levels_confirm` (above).
