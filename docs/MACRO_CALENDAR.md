# Macro calendar ingest

Data prep only: no price data, no outcome. The scraped Forex Factory calendars
become one clean UTC table. The table gets an anchor check, a coverage report
and surprise counts. Decisions: `docs/DECISIONS.md`, section
`data/macro-calendar` (MC1–MC15).

```
python tools/data/macro_calendar.py build      # writes the table and docs/macro_calendar_report.json
python tools/data/macro_calendar.py manifest   # prints the manifest entries (JSON)
python -m backend.datafeed.store.manifest check
```

## Files

| Path | In git | What |
|---|---|---|
| `database/historical_news/forex_factory_calendar_<year>.csv`, 2015–2026 | no (licence unclear) | the raw scrape, unchanged; in the manifest with SHA-256, "unused by any study" |
| `database/historical_news/macro_calendar_utc.parquet` | no | **the clean table**, 55,389 rows; in the manifest, "unused by any study" |
| `docs/macro_calendar_report.json` | yes | counts, shares, dates and release times only: no value from any row |
| `tools/data/macro_calendar.py` | yes | the builder |
| `tests/datafeed/test_macro_calendar.py` | yes | made-up rows only |

- **Years:** none of 2015–2026 is missing. 2010–2014 files exist locally too but are out of scope (MC2).
- **Where the raw files were:** the CSVs were found in an untracked `database/historical_news_1/`
  of the main checkout. Copy them into `database/historical_news/` to build.
- **Data guard:** `research/data_guard.py` refuses every one of these files to a study whose committed
  PREREG doesn't name it.
- **Rebuild:** a rebuild from the same CSVs is byte-identical, for the parquet and the report.

## The clean table

One row per calendar row, after the cuts below. The columns:
- `ts_utc`: UTC, or null when the source gives no usable time.
- `time_flag`: why `ts_utc` is null.
- `date_local`, `time_raw`, `source_year`, `source_tz`, `date_rebuilt`.
- `currency`, `event`, `impact`.
- `actual`, `forecast` and `previous`: the raw strings, each with `_num`, `_unit` and `_nonnumeric`.
- `surprise`, `n_prior`.

- **UTC** comes from Date + Time, localized with DST in the file's source time zone.
  - 2015–2023 are **UTC+8**, not Warsaw (MC1).
  - 2024–2026 are Europe/Warsaw.
  - "Combined DateTime" is dropped on read and never used.
  - The weeks when US and EU clock changes differ need no special case: the conversion is
    tz-aware on both sides. The tests cover 2024-03-15 and 2024-11-01.
- **No guessed times.** These rows get `ts_utc` null and a `time_flag`:

  | `time_flag` | Source Time |
  |---|---|
  | `all_day` | "All Day" |
  | `tentative` | "Tentative" |
  | `period_data` | "Oct Data", "Q2 Data", … |
  | `day_n` | "Day 2", … |
  | `other_nontime` | "Sep 27th", "22nd-26th", … |
  | `nonexistent_local` / `ambiguous_local` | a local time that doesn't exist or exists twice at a DST change (none occurred) |
  | `source_time_conflict` | the two listed rows (MC3) |

  2,754 rows are untimed.
- **Cuts:**
  - **Year wrap (MC4):** the scraper's next-January tail at the end of each file, 728 rows. Every
    cut row is matched in the next file, except 8 at the 2023 → 2024 boundary (listed in the report).
  - **Seal (MC6):** rows at or after 2026-10-01, 1,087 rows of the 2026 file. They are cut before any
    value is parsed.
  - **Exact duplicates:** 2 rows.
- **Values:**
  - K, M, B and T are scaled (`175K` → 175000).
  - `%` stays in percent (`0.3%` → 0.3) (MC9).
  - A value that isn't a number gets null and `_nonnumeric`: vote tallies like `0-0-9`, `<0.25%`,
    `Pass`. That's 1,681 actual cells across all events and 0 in the five families.

## Anchor check: PASS

Rule: every timed USD CPI and NFP row at **08:30 New York**, every FOMC Statement and Federal Funds
Rate row at **14:00 New York**. The two unscheduled 2020 meetings are allowlisted, and the check fails
if either is missing. Any mismatch exits 1 and writes nothing.

| Event | Rows checked |
|---|---|
| CPI m/m | 139 |
| CPI y/y | 138 |
| Core CPI m/m | 139 |
| Core CPI y/y | 32 |
| Non-Farm Employment Change | 140 |
| FOMC Statement | 95 |
| Federal Funds Rate | 95 |
| **Total** | **778 rows, 0 mismatches** |

- **Allowlisted, both found:** 2020-03-03 10:00 NY and 2020-03-15 17:00 NY.
- **Under Europe/Warsaw for every file**, the check would fail with 64–66 mismatches in each of
  2015–2023 and 0 in 2024–2026 (MC1).

## Coverage

Releases per year (UTC year; timed rows):

| Year | CPI m/m | CPI y/y | Core CPI m/m | Core CPI y/y | NFP | FOMC statement | Fed funds rate | Core PCE m/m | Claims |
|---|---|---|---|---|---|---|---|---|---|
| 2015 | 12 | 12 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2016 | 12 | 12 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2017 | 12 | 12 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2018 | 12 | 12 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2019 | 12 | 12 | 12 | 0 | 12 | 8 | 8 | 11 | 52 |
| 2020 | 12 | 12 | 12 | 0 | 12 | 9 | 9 | 12 | 53 |
| 2021 | 12 | 11 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2022 | 12 | 11 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2023 | 12 | 12 | 12 | 0 | 12 | 8 | 8 | 12 | 52 |
| 2024 | 12 | 12 | 12 | 12 | 12 | 8 | 8 | 12 | 52 |
| 2025 | 10 | 11 | 10 | 11 | 11 | 8 | 8 | 10 | 47 |
| 2026 (to 09-30) | 9 | 9 | 9 | 9 | 9 | 6 | 6 | 10 | 38 |

FOMC 2020: 7 scheduled meetings were held (the March 17–18 meeting was replaced by the 15 March one),
plus the two unscheduled meetings.

Quality, 2015 – 2026-09:

| Family | Actual filled | Forecast filled | After the scrape | Untimed at source | Duplicates | Revised "previous" | Missing at source | Parse failures |
|---|---|---|---|---|---|---|---|---|
| CPI (4 events) | 436/436 | 436/436 | 12 | 2 | 0 | 3 | 6 | 0 |
| NFP | 137/137 | 137/137 | 3 | 1 | 0 | 134 | 1 | 0 |
| FOMC | 92/184 | 90/184 | 6 | 0 | 0 | — | 0 | 0 |
| Core PCE m/m | 135/135 | 135/135 | 4 | 2 | 0 | 29 | 3 | 0 |
| Claims | 591/591 | 590/591 | 15 | 6 | 0 | 420 | 7 weeks | 0 |

The columns:
- **Actual filled, forecast filled:** over the rows released before the scrape. FOMC: the Statement
  rows carry no value; only the rate rows do.
- **After the scrape (MC7):** the 2026 file was scraped around **2026-06-12**, its last actual. Its
  later rows are a schedule, with no values.
- **Revised "previous":** rows whose `previous` differs from the prior release's `actual`. These are
  the source's revisions.
- **Duplicates:** the same `ts_utc` and event more than once.

**Missing at the source**, counted apart from parse failures (MC8):
- **CPI:** the 2025 shutdown. m/m and core m/m: none between 2025-10-24 and 2026-01-13 (2 releases).
  CPI y/y and core y/y: none between 2025-10-24 and 2025-12-18 (1). The source has no m/m for
  Nov 2025, in line with BLS.
- **NFP:** none between 2025-09-05 and 2025-11-20 (1). The October data is an untimed row,
  "Oct Data", on 2025-12-16.
- **Core PCE:**
  - 2018-12-21 → 2019-03-01, the 2019 shutdown; February's data is an untimed row, "Feb Data", on
    2019-04-29;
  - 2025-09-26 → 2025-12-05;
  - 2025-12-05 → 2026-01-22, with an untimed "Oct Data" row on that date.
- **Claims:** no release in the 7 New York weeks from 2025-09-28 to 2025-11-15. The delayed weeks
  appear as 6 untimed rows on 2025-11-18 and 11-20, with Time "Sep 27th" … "Nov 8th".

**Untimed at source** means the row is there but has no clock time: the annotations above, plus the
two MC3 rows. **Parse failures:** 0. That covers a date that doesn't parse and a non-numeric value in
the five families. Lowercase `k` (3 claims rows) is parsed.

**Full coverage back to 2015:**
- **FOMC: yes.** 8 statements in every year, 9 in 2020; every rate decision is there.
- **NFP: yes, apart from the source.** Every month from 2015-01-09 on, except the one release lost in
  the 2025 shutdown.
- **CPI: yes for CPI m/m, CPI y/y and Core CPI m/m, apart from the source.** The gaps are the 2025
  shutdown, plus the two CPI y/y rows of MC3, which are there but untimed. **Core CPI y/y starts only
  in 2024-01**: the source doesn't list it before then.
- **2026:** values only up to the scrape, about 2026-06-12, for every family.

**Other:**
- **File boundary:** the files leave 2023-12-31 16:00 – 23:00 UTC (a Sunday) uncovered. One file
  ends in UTC+8 and the next starts in Warsaw time.

## Surprises (counts only)

`surprise = actual − forecast` for the USD series CPI m/m, CPI y/y, Core CPI m/m, Core CPI y/y, NFP,
Core PCE m/m and Unemployment Claims. Each event name is its own series. `n_prior` is the number of
earlier surprises of the same series, with a strictly earlier `ts_utc`: no look-ahead, and rows at the
same time don't count each other. **No z-score is computed:** N isn't picked. **FOMC rows get none**:
that will come from rate futures.

| Series | Rows | With a surprise | z-eligible if N = 12 | N = 24 | N = 36 | N = 60 | First eligible at N = 24 |
|---|---|---|---|---|---|---|---|
| CPI m/m | 139 | 136 | 124 | 112 | 100 | 76 | 2017-01-18 |
| CPI y/y | 138 | 135 | 123 | 111 | 99 | 75 | 2017-01-18 |
| Core CPI m/m | 139 | 136 | 124 | 112 | 100 | 76 | 2017-01-18 |
| Core CPI y/y | 32 | 29 | 17 | 5 | 0 | 0 | 2026-02-13 |
| NFP | 140 | 137 | 125 | 113 | 101 | 77 | 2017-01-06 |
| Core PCE m/m | 139 | 135 | 123 | 111 | 99 | 75 | 2017-01-30 |
| Unemployment Claims | 606 | 590 | 578 | 566 | 554 | 530 | 2015-06-25 |
| FOMC (no surprise, by rule) | 190 | 0 | — | — | — | — | — |

Rows without a surprise are the ones after the scrape (no actual), plus one claims row with no forecast.

**Proposal (owner decides): N = 24 for every series.**
- **Why 24:** an SD from 24 surprises has a relative standard error of about 1/√(2·23) ≈ 15 %. N = 12
  gives about 21 %. N = 24 keeps 2017 onward for the monthly series (≈ 112 eligible each) and almost
  all of claims.
- **Not fixed by the prompt, also the owner's to decide:**
  1. **Trailing SD:** expanding over all past surprises, or a rolling window, e.g. the last 60.
     - An expanding SD carries the 2020 claims and NFP surprises forever. Those are orders of magnitude
       above normal, as is public knowledge.
     - A rolling window forgets them after N.
     - No SD was computed here.
  2. **Core CPI y/y:** with N = 24 it has 5 eligible rows. Drop it, or let it use Core CPI m/m's
     history? It is a different series.
