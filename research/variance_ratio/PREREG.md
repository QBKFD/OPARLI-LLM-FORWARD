# variance_ratio: analysis plan (EXPLORATORY, descriptive)

**This is not a pre-registration of a hypothesis.** It is the plan of a
descriptive analysis the owner asked for on 2026-10-31. There is no
hypothesis, no decision rule and no pass/fail. It lives in `PREREG.md` only
because `research/data_guard.py` reads that file name: it lets this study
read a guarded file only when a committed `PREREG.md` names it (D1 in
`DECISIONS.md`). Every value below is fixed here, before any data is read,
and is coded in `vr_params.py`.

## V1. Markets and files

| Market | Manifest key |
|---|---|
| XAUUSD | `database/ohlcv_data/XAUUSD.txt` (through the audit_long cache, as `pullback_structure` reads it) |
| XAGUSD | `database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet` |
| EURUSD | `database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet` |
| USDJPY | `database/ohlcv_data/USDJPY_ibkr_1min_20160101_20260930.parquet` |
| News days (V7) | `database/historical_news/macro_calendar_utc.parquet` (data/macro-calendar, PR #60, commit 12f65c6, SHA-256 `dfde970c…`) |

Every file goes through `data_guard.assert_may_read(path, study="variance_ratio")`
and `manifest.verify`. The calendar parquet isn't in main's manifest yet, so it
is checked by its pinned SHA-256 instead.

## V2. Period

Dev only: [2016-01-01, 2024-01-01) UTC. Every bar is checked with
`sealing.assert_period(market, ts, "dev")`. No holdout or sealed row is read.

## V3. Variance ratio

- **Base returns:** log returns of 1-hour bins. Each bin's close is its last 1-min close.
- **Check:** 5-min bins, run on the whole-sample cell only (V9).
- **Horizons:** k = 2, 4, 8 and 24 hours (q = k for the 1h base, q = 12k for the 5-min base).
- **The estimator:** VR(k) = Var(k-period return) / (k · Var(1-period return)) over the
  cell's overlapping windows and the returns inside them, demeaned by the market's mean
  1-period return (Lo & MacKinlay 1988).
- **z:** heteroskedasticity-robust. It is Lo-MacKinlay's z\*, generalized to a cell whose
  windows start only at some slots: each lag-j cross product is weighted by the number of
  the cell's windows that hold it. On a contiguous series with every start it is exactly
  z\* (`vr.py`, tested).
- **p:** two-sided, normal.

## V4. Exclusions (counted per reason, per market and base)

A base return is excluded when:
- either bin's last bar isn't in the bin's final 15 minutes (5 for 5-min);
- any gap between consecutive 1-min bars from one close to the next exceeds 15 minutes.
  This covers weekends, holidays and the metals' daily break;
- either close's trade date (New York, rolled at 17:00) has fewer than 1000 1-min rows
  (a short day);
- it straddles the gold seam, 2026-02-01, which lies outside dev, so the count is 0.

A k-window must hold k consecutive valid returns.

Reasons, counted for every slot, first match wins: seam, short_day, weekend, daily_break,
holiday, gap.

## V5. Splits

Every cell is defined at the window's start.

| Split | Cells |
|---|---|
| all | the whole sample |
| hour_utc | the UTC hour, 0–23 |
| session | fixed UTC hours, no DST shift: Asia [22, 7), London [7, 12), Overlap [12, 16), NewYork [16, 22) |
| sigma240_tercile | V6 |
| weekday_utc | Mon–Sun |
| news_day | V7 |

## V6. σ terciles

- **σ:** σ_ret_240 from `research/squeeze_oco/stage1.state_table` (sigma_compare's method).
  The model is gold's pinned HAR fit `parquet2021_2023`, SHA-256 `dc0eb3c5…`, applied to each
  market's own bars, as breakout_fade B4 did for silver.
- **Which label:** the one at the window's start. It uses only bars before that time.
- **Cut-offs:** the 1/3 and 2/3 quantiles of σ over the market's valid 1h starts. They are
  descriptive cut-offs and use the whole dev period.
- **Missing σ:** starts without a σ (the HAR warm-up at the start of 2016) are in no tercile
  cell, and they are counted.

## V7. News days

A UTC date with at least one timed, High-impact USD row of the repo's frozen usd_tier1 set
(`database/historical_news/DATA_NOTES_events.md`):
- CPI m/m, CPI y/y, Core CPI m/m;
- FOMC Press Conference, FOMC Statement, Federal Funds Rate;
- Non-Farm Employment Change.

The calendar is the clean table of data/macro-calendar. Its anchor check passed (PR #60).

## V8. Multiple comparisons

Benjamini–Hochberg over every cell with a p-value: every market, base, split, cell and k,
in one family. q = 0.05 is used only to mark cells. **EXPLORATORY: no pass/fail.**

## V9. Output

- **Per market:** one table of VR, z and n per cell, and one hour × k heatmap of VR.
- **No price is written.**
- **ATTEMPTS.json** is bumped once, before the run, as a `descriptive_analysis`.
