# news_vol_rep: counts (no outcome)

Calendar: `database/historical_news/macro_calendar_utc.parquet` (sha256 `dfde970cdcd5…`, PR #60 merged as 0988ca3), the same file and events as Study A.

- usd_tier1 rows with a timestamp, dev: 539; grouped into 310 release minutes; minutes mixing FOMC and data: 0.
- usd_tier1 rows without a timestamp on dev dates (excluded): 2 {'source_time_conflict': 2}.
- Rows per release minute: {'1': 168, '2': 55, '3': 87}.
- EUR High-impact release minutes in dev (R5, counted only, not flagged): 412 {'2016': 69, '2017': 54, '2018': 41, '2019': 63, '2020': 70, '2021': 33, '2022': 37, '2023': 45}.

## Release minutes per type and year

| type | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|---|---|
| FOMC | 12 | 12 | 12 | 16 | 18 | 16 | 16 | 16 |
| data | 24 | 24 | 24 | 24 | 24 | 24 | 24 | 24 |

## XAGUSD

Bars: 2,832,852 dev 1-min bar times, 2016-01-04 00:00:00+00:00 .. 2023-12-29 20:59:00+00:00 (manifest sha256 `49ceaa3994fb…`).

- Bars in the metals calendar's closed minutes: 0 (weekday bars in the 17:00 NY hour: 0); in its own calendar's: 0.

### XAGUSD, hourly origins, H = 240 (non-crossing windows valid by timestamps)

| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 5919 | 1020 | 4887 | 96 | 24 | 120 | 0 | 4767 | 128 |
| 2017 | 5890 | 1010 | 4862 | 92 | 24 | 116 | 0 | 4746 | 124 |
| 2018 | 5914 | 1014 | 4880 | 96 | 24 | 120 | 0 | 4760 | 128 |
| 2019 | 5881 | 1001 | 4852 | 96 | 24 | 120 | 0 | 4732 | 128 |
| 2020 | 5925 | 1008 | 4888 | 92 | 26 | 118 | 0 | 4770 | 125 |
| 2021 | 5921 | 1020 | 4889 | 92 | 24 | 116 | 0 | 4773 | 124 |
| 2022 | 5892 | 1011 | 4856 | 96 | 24 | 120 | 0 | 4736 | 128 |
| 2023 | 5891 | 1009 | 4855 | 92 | 24 | 116 | 0 | 4739 | 124 |

### XAGUSD, hourly origins, H = 60 (non-crossing windows valid by timestamps)

| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 5919 | 255 | 5659 | 24 | 8 | 32 | 0 | 5627 | 36 |
| 2017 | 5890 | 251 | 5631 | 23 | 8 | 31 | 0 | 5600 | 35 |
| 2018 | 5914 | 253 | 5651 | 24 | 8 | 32 | 0 | 5619 | 36 |
| 2019 | 5881 | 250 | 5618 | 24 | 8 | 32 | 0 | 5586 | 40 |
| 2020 | 5925 | 251 | 5659 | 23 | 9 | 32 | 0 | 5627 | 39 |
| 2021 | 5921 | 255 | 5660 | 23 | 8 | 31 | 0 | 5629 | 39 |
| 2022 | 5892 | 249 | 5627 | 24 | 8 | 32 | 0 | 5595 | 40 |
| 2023 | 5891 | 247 | 5626 | 23 | 8 | 31 | 0 | 5595 | 39 |

### XAGUSD, EUR High-impact events in test rows 2018–2023 (R5; not flagged)

| H | test rows | with an EUR event | of those, no US release | with a US release too |
|---|---|---|---|---|
| 240 | 29220 | 802 | 767 | 35 |
| 60 | 33841 | 231 | 225 | 6 |

## EURUSD

Bars: 2,951,562 dev 1-min bar times, 2016-01-04 00:00:00+00:00 .. 2023-12-29 20:59:00+00:00 (manifest sha256 `6402e702a71b…`).

- Bars in the metals calendar's closed minutes: 92,204 (weekday bars in the 17:00 NY hour: 74,159); in its own calendar's: 0.
- R3, crossing flags of the hourly labels under the metals calendar (build_context's) and EURUSD's own (pause 17:00–17:15 NY, Sunday open 17:15): H = 240: 8,280 vs 8,280 crossing, 0 differ; H = 60: 2,061 vs 2,061 crossing, 0 differ.

### EURUSD, hourly origins, H = 240 (non-crossing windows valid by timestamps)

| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 6209 | 1034 | 5173 | 96 | 24 | 120 | 0 | 5053 | 128 |
| 2017 | 6194 | 1030 | 5158 | 96 | 24 | 120 | 0 | 5038 | 128 |
| 2018 | 6207 | 1034 | 5171 | 96 | 24 | 120 | 0 | 5051 | 128 |
| 2019 | 6208 | 1034 | 5172 | 96 | 24 | 120 | 0 | 5052 | 128 |
| 2020 | 6234 | 1038 | 5194 | 96 | 27 | 123 | 0 | 5071 | 130 |
| 2021 | 6238 | 1040 | 5198 | 96 | 24 | 120 | 0 | 5078 | 128 |
| 2022 | 6235 | 1040 | 5195 | 96 | 24 | 120 | 0 | 5075 | 128 |
| 2023 | 6183 | 1030 | 5151 | 96 | 24 | 120 | 0 | 5031 | 128 |

### EURUSD, hourly origins, H = 60 (non-crossing windows valid by timestamps)

| year | origins | crossing | rows | data | FOMC | any | both | none | literal (t, t+H] any |
|---|---|---|---|---|---|---|---|---|---|
| 2016 | 6209 | 257 | 5950 | 24 | 8 | 32 | 0 | 5918 | 36 |
| 2017 | 6194 | 256 | 5935 | 24 | 8 | 32 | 0 | 5903 | 36 |
| 2018 | 6207 | 257 | 5948 | 24 | 8 | 32 | 0 | 5916 | 36 |
| 2019 | 6208 | 257 | 5949 | 24 | 8 | 32 | 0 | 5917 | 40 |
| 2020 | 6234 | 258 | 5974 | 24 | 10 | 34 | 0 | 5940 | 41 |
| 2021 | 6238 | 260 | 5978 | 24 | 8 | 32 | 0 | 5946 | 40 |
| 2022 | 6235 | 260 | 5975 | 24 | 8 | 32 | 0 | 5943 | 40 |
| 2023 | 6183 | 256 | 5925 | 24 | 8 | 32 | 0 | 5893 | 40 |

### EURUSD, EUR High-impact events in test rows 2018–2023 (R5; not flagged)

| H | test rows | with an EUR event | of those, no US release | with a US release too |
|---|---|---|---|---|
| 240 | 31081 | 802 | 767 | 35 |
| 60 | 35749 | 231 | 225 | 6 |

## Power (rough, analytic; test folds 2018–2023)

As Study A: one cluster per release date; residual sd of log(RV/h) 0.55, gold's HAR log-MSE (no value is known for silver or EURUSD). α is BH's strictest rank of the 8 tests (R4), 0.05/8. Q2 assumes an oracle augmented arm and lognormal residuals, so it is optimistic.

| market | H | release rows (data, FOMC) | release dates | other rows (eff.) | Q1 MDE, 80 % | δ | Q1 power | Q2 power (oracle) |
|---|---|---|---|---|---|---|---|---|
| XAGUSD | 240 | 710 (564, 146) | 187 | 7128 | 0.136 | 0.1 | 0.483 | 0.956 |
| | | | | | | 0.2 | 0.992 | 0.996 |
| | | | | | | 0.3 | 1.0 | 1.0 |
| | | | | | | 0.5 | 1.0 | 1.0 |
| XAGUSD | 60 | 190 (141, 49) | 187 | 33651 | 0.135 | 0.1 | 0.493 | 0.956 |
| | | | | | | 0.2 | 0.993 | 0.996 |
| | | | | | | 0.3 | 1.0 | 1.0 |
| | | | | | | 0.5 | 1.0 | 1.0 |
| EURUSD | 240 | 723 (576, 147) | 191 | 7590 | 0.135 | 0.1 | 0.494 | 0.96 |
| | | | | | | 0.2 | 0.993 | 0.997 |
| | | | | | | 0.3 | 1.0 | 1.0 |
| | | | | | | 0.5 | 1.0 | 1.0 |
| EURUSD | 60 | 194 (144, 50) | 191 | 35555 | 0.133 | 0.1 | 0.503 | 0.96 |
| | | | | | | 0.2 | 0.994 | 0.997 |
| | | | | | | 0.3 | 1.0 | 1.0 |
| | | | | | | 0.5 | 1.0 | 1.0 |
