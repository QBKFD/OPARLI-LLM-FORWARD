# Decisions log, round 2

Entries 1–16 were written before any model result. Later entries say when they
were made.

## Before any model result

1. **Branch base.** `research/vol-features-r2` was created from
   `research/vol-features` (e01529d), so round 1's code (wick60, the existing
   feature block, OLS/DM helpers) is reused by import, tested and unchanged.

2. **Round 1 REPORT.md was found emptied in the working tree** (an uncommitted
   edit that isn't mine). The committed version in e01529d is intact. The edit is
   left alone, is not committed, and round 1 was read from git.

3. **Data files changed since round 1.** `XAUUSD_COMPLETE*.csv`,
   `xauusd_5year_1min.csv` and `xauusd_gap_filled.csv` are no longer in
   `database/ohlcv_data/`. The long-history file is `XAUUSD.txt` (2003-05 to
   2026), the only one reaching back before 2020. `GC_FUTURES_1MIN.csv` starts
   in 2025-12.

4. **Sealing and the long file.** The file is text, so it has to be scanned in
   full. Rows are filtered to file date < 2024-01-01 at read time and cached in a
   gitignored parquet. Two things touched post-2023 lines without reading prices:
   a first `awk` count of rows per year (it printed 2024, 2025 and 2026 row counts)
   and `wc -l`. No 2024+ value entered any computation.

5. **Where the audit is written.** The task names
   `research/vol_features/DATA_AUDIT_LONG.md`, so it goes there. The script is
   `research/vol_features_r2/audit_long.py`.

6. **Timezone identification (U1).** The task says "via the hourly range profile".
   Per year and US-DST season, the Tue–Thu mean (H − L)/C per hour is correlated
   with the same profile from the known-UTC parquet (2021–2023) at every circular
   shift. The year passes if the best shift is the same in both seasons and the
   correlation is >= 0.8 (the 0.8 threshold is my choice). Hours with no bars
   (the break in the reference) are left out. A first version of the audit had a
   NaN bug here, which was fixed before any use. The break-hour location is also
   reported as a cross-check. Result: shift 0 (UTC) for every year. 2003–2005
   have correlations of 0.74–0.79, but they fail coverage anyway, so the threshold
   choice doesn't change the usable years.

7. **Expected minutes (U2):** 1380 per New York trading date (18:00 → 17:00 NY),
   Mon–Fri session dates, coverage capped at 1. 2008–2011 have more than 1380
   because the break hour was only partly closed then. Holidays count as
   trading days with low coverage, which the 10 % allowance absorbs.

8. **Stale runs (U3):** runs of consecutive 1-min bars with O, H, L and C all
   identical to the previous bar, longer than 30 bars. None were found in any
   year.

9. **Confirmation data source.** CONF uses `XAUUSD.txt` for 2007–2020. DEV uses
   the clean parquet, as round 1 did. The two are identical where they overlap.
   2020 is warm-up in DEV and a test year in CONF. The analyses are separate, so
   nothing leaks.

10. **CONF has no warm-up year.** 2006 fails the usability rule, so it isn't used
    as warm-up either. The first weeks of 2007 are lost to rolling windows (NaN
    rows are dropped).

11. **dsRV windows in trading time.** "The last n hours of 1-min data" means the
    last n 1H bar rows, as in round 1's HAR, so that L, S and T reparametrise
    that HAR exactly. Clock hours would put a weekend inside 120h on every
    Monday.

12. **Profile details:** the span is minutes up to the last training bar; buckets
    with fewer than 50 returns get the median; the floor is the 1st percentile
    across buckets. None of these are in the task text.

13. **R2 η² threshold** must hold in every training window (the strictest reading
    of "(training windows)").

14. **R5 is judged on MC** (L, S, T, W), both targets. MC3 is reported alongside.

15. **Mapping the rules to a recommendation** (the task doesn't define it) is
    written in PREREG: R1 and R2 must both pass for C to replace the 13.

16. **Confirmation seal.** CONF data is loaded only below 2021-01-01, and CONF
    target windows must end by then, so confirmation targets never use DEV-period
    data.

## During and after the run
