# FOMC surprises (San Francisco Fed): the downloaded files

Downloaded 2026-11-08 15:12 UTC, on the owner's go-ahead of the same day, for Study B (`research/news_surprise`,
a draft). **Registered "unused by any study"** in `backend/datafeed/store/manifest.json`, with source, date,
SHA-256 and terms. `research/data_guard.py` therefore refuses them to every study until a committed PREREG names
them. **No surprise value has been read:** only sheet names, column headers, row counts and the date columns,
to fill in the manifest.

## The files (local only, `database/fomc_surprises/`, git-ignored)

| File | What | Coverage (date column) | Rows from 2026-10-01 (sealed) |
|---|---|---|---|
| `USMPD.xlsx` | The US Monetary Policy Event-Study Database: per FOMC statement, press conference, "monetary event" (both) and minutes, the date and time and the change in money-market futures (MP1, MP2, FF1–6, ED1–8), OIS, Treasuries, TIPS, S&P 500, DXY, EURUSD, USDJPY. Statements and minutes: 30-min windows; press conferences: 70 min. Units: percentage points. Last update 30 October 2026 | statements 1994-02-04 .. 2026-10-28 (280); press conferences from 2011-04-27 (96); minutes from 2001 (207) | 1 per sheet |
| `monetary-policy-surprises.zip` | `mps.csv`: per meeting the surprise for the statement (STMT), press conference (PC) and monetary event (ME), Acosta et al. (2025) method, normalized to move the 1-year Treasury yield one-for-one (**positive = hawkish**). Also `mps_minutes.csv`, `y1.csv` (daily 1-year yield), and the R code (`mps.R`, `gss.R`) | 1994-02-04 .. 2026-10-28 (280 meetings) | mps.csv 1, mps_minutes.csv 1, y1.csv 16 |
| `monetary-policy-surprises-data.xlsx` | Bauer and Swanson (2023): per meeting MPS (first principal component of 30-min money-market futures changes) and MPS_ORTH (MPS cleaned of six predictors), with the inputs, an "Unscheduled" flag and the monthly series | FOMC (update 2023): 1988-02-04 .. 2023-12-13 (361 meetings) | 0 |

**Meetings per year, 2012–2023:** 102 statement rows: 8 a year, plus 1 in 2019 and 5 in 2020. Presumably those 6
are the unscheduled actions; the files' Unscheduled flag (not read yet) will confirm. Both sources cover 2012–2023 in full. USMPD gives each statement's
time, including the pre-2013 12:30 and 14:15 NY times.

## Terms of use

- The data pages state no licence. The site policy (frbsf.org/site-policies, "Appropriate Use", read 2026-11-08)
  allows reproduction "without limitation as to number" if it is "not distributed for the purpose of private
  gain" and is "appropriately credited to the Federal Reserve Bank of San Francisco". Third-party copyrighted
  material needs its owner's permission (USMPD is built from LSEG Tick History).
- **So:** the files stay local and are never committed; results that use them credit the Bank and cite:
  - Acosta, Ajello, Bauer, Loria and Miranda-Agrippino (2025), "Financial Market Effects of FOMC Communication:
    Evidence from a New Event-Study Database", FRBSF Working Paper 2025-30;
  - Bauer and Swanson (2023), "A Reassessment of Monetary Policy Surprises and High-Frequency Identification",
    NBER Macroeconomics Annual.

## Sealed rows

Rows dated from 2026-10-01 (the 2026-10-28 meeting, the 2026-10-08 minutes, 16 daily yields) are in the files as
downloaded, as the 2026 Forex Factory CSV keeps its sealed rows (MC6). A loader must drop them by date before it
parses any value. Only their count was taken.
