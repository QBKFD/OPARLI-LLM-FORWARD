# news_vol: counts (no outcome)

Bars: 2,830,597 XAUUSD dev 1-min bar times (manifest sha256 `dc2d30cdee56…`). Calendar: `database/historical_news/macro_calendar_utc.parquet` (sha256 `dfde970cdcd5…`, PR #60 merged as 0988ca3).

- usd_tier1 rows with a timestamp, dev: 539; grouped into 310 release minutes; minutes mixing FOMC and data: 0.
- usd_tier1 rows without a timestamp on dev dates (excluded): 2 {'source_time_conflict': 2}.
- Rows per release minute: {'1': 168, '2': 55, '3': 87}.
- Unscheduled FOMC actions in the set: ['2020-03-03 15:00:00+00:00', '2020-03-15 21:00:00+00:00'].

## Release minutes per type and year

| type | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|
| FOMC | 12 | 12 | 12 | 16 | 18 | 16 | 16 | 16 |
| data | 24 | 24 | 24 | 24 | 24 | 24 | 24 | 24 |

## Hourly origins, H = 240 (non-crossing windows valid by timestamps)

| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 5926 | 1009 | 4886 | 96 | 24 | 120 | 0 | 4766 | 128 |
| 2017 | 5912 | 1003 | 4880 | 92 | 24 | 116 | 0 | 4764 | 124 |
| 2018 | 5906 | 1004 | 4872 | 96 | 24 | 120 | 0 | 4752 | 128 |
| 2019 | 5893 | 1005 | 4850 | 96 | 21 | 117 | 0 | 4733 | 124 |
| 2020 | 5926 | 1006 | 4886 | 88 | 26 | 114 | 0 | 4772 | 121 |
| 2021 | 5907 | 1005 | 4875 | 92 | 24 | 116 | 0 | 4759 | 124 |
| 2022 | 5915 | 1013 | 4877 | 96 | 24 | 120 | 0 | 4757 | 128 |
| 2023 | 5890 | 1007 | 4856 | 92 | 24 | 116 | 0 | 4740 | 124 |

## Hourly origins, H = 60 (non-crossing windows valid by timestamps)

| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 5926 | 251 | 5659 | 24 | 8 | 32 | 0 | 5627 | 36 |
| 2017 | 5912 | 250 | 5650 | 23 | 8 | 31 | 0 | 5619 | 35 |
| 2018 | 5906 | 250 | 5641 | 24 | 8 | 32 | 0 | 5609 | 36 |
| 2019 | 5893 | 250 | 5615 | 24 | 7 | 31 | 0 | 5584 | 38 |
| 2020 | 5926 | 251 | 5662 | 22 | 9 | 31 | 0 | 5631 | 38 |
| 2021 | 5907 | 251 | 5648 | 23 | 8 | 31 | 0 | 5617 | 39 |
| 2022 | 5915 | 250 | 5650 | 24 | 8 | 32 | 0 | 5618 | 40 |
| 2023 | 5890 | 249 | 5623 | 23 | 8 | 31 | 0 | 5592 | 39 |

## Power (rough, analytic; test folds 2018–2023)

One cluster per release date (its overlapping windows, and an FOMC statement with its press conference, count once); residual sd of log(RV/h) 0.55 (sigma_compare's HAR log-MSE, seen data). Q2 assumes an oracle augmented arm and lognormal residuals, so it is optimistic: fatter tails and the estimation noise of two extra coefficients lower it.

| H | release rows | release dates | other rows (eff.) | Q1 MDE, 80 %, α 0.0125 | δ | Q1 power | Q2 power (oracle) |
|---|---|---|---|---|---|---|---|
| 240 | 703 | 185 | 7128 | 0.126 | 0.1 | 0.579 | 0.974 |
| | | | | | 0.2 | 0.996 | 0.998 |
| | | | | | 0.3 | 1.0 | 1.0 |
| | | | | | 0.5 | 1.0 | 1.0 |
| 60 | 188 | 185 | 33651 | 0.125 | 0.1 | 0.589 | 0.974 |
| | | | | | 0.2 | 0.996 | 0.998 |
| | | | | | 0.3 | 1.0 | 1.0 |
| | | | | | 0.5 | 1.0 | 1.0 |
