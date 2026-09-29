# Data audit: XAUUSD.txt 2024-01-01 .. 2026-01-30 (counts only)

Written by `audit_2024.py` **before** the PREREG commit. It computes no outcome, return or event statistic. Rows with file date 2023-11-01 .. 2026-01-30 were read (Nov/Dec 2023 for warm-up and the first PDH). The study cut-off (nothing after 2026-01-30) and the global seal (2026-10-01) are asserted. The source file has no rows after January 2026.

- Rows read: 796,271. As stored: monotonic True, duplicate timestamps 0, bad OHLC rows 0. Range 2023-11-01 00:00:00 .. 2026-01-30 21:59:00.

## Coverage per month (test period)

| month | rows | trading days | median bars/day | share days >= 80 % | gaps 5-60 min |
|---|---:|---:|---:|---:|---:|
| 2024-01 | 30,268 | 23 | 1380 | 0.96 | 0 |
| 2024-02 | 28,808 | 22 | 1380 | 0.95 | 1 |
| 2024-03 | 27,649 | 21 | 1379 | 0.95 | 0 |
| 2024-04 | 30,359 | 23 | 1380 | 0.96 | 0 |
| 2024-05 | 31,463 | 23 | 1380 | 1.00 | 0 |
| 2024-06 | 27,447 | 21 | 1380 | 0.95 | 0 |
| 2024-07 | 31,587 | 24 | 1380 | 0.96 | 0 |
| 2024-08 | 30,240 | 22 | 1380 | 1.00 | 0 |
| 2024-09 | 28,945 | 22 | 1380 | 0.95 | 0 |
| 2024-10 | 31,671 | 24 | 1380 | 0.96 | 0 |
| 2024-11 | 28,572 | 21 | 1380 | 1.00 | 0 |
| 2024-12 | 28,709 | 21 | 1380 | 1.00 | 1 |
| 2025-01 | 30,197 | 22 | 1380 | 1.00 | 0 |
| 2025-02 | 27,441 | 20 | 1380 | 1.00 | 0 |
| 2025-03 | 29,092 | 22 | 1380 | 0.95 | 1 |
| 2025-04 | 28,948 | 22 | 1380 | 0.95 | 1 |
| 2025-05 | 30,087 | 22 | 1380 | 1.00 | 0 |
| 2025-06 | 28,949 | 22 | 1380 | 0.95 | 0 |
| 2025-07 | 31,499 | 24 | 1380 | 0.96 | 0 |
| 2025-08 | 28,971 | 22 | 1380 | 0.95 | 1 |
| 2025-09 | 30,209 | 23 | 1380 | 0.96 | 0 |
| 2025-10 | 31,619 | 23 | 1380 | 1.00 | 0 |
| 2025-11 | 27,298 | 21 | 1380 | 0.95 | 1 |
| 2025-12 | 30,062 | 22 | 1380 | 1.00 | 1 |
| 2026-01 | 28,828 | 21 | 1380 | 1.00 | 0 |

## Timezone check (hourly range profile vs the UTC 2021-2023 reference; break hour)

| half-year | DST shift / corr | DST break hour (bars) | STD shift / corr | STD break hour (bars) |
|---|---|---|---|---|
| 2024H1 | +0 / 0.97 | 21 UTC (0) | +0 / 0.99 | 22 UTC (0) |
| 2024H2 | +0 / 0.96 | 21 UTC (0) | +0 / 0.93 | 22 UTC (0) |
| 2025H1 | +0 / 0.86 | 21 UTC (0) | +0 / 0.97 | 22 UTC (0) |
| 2025H2 | +0 / 0.93 | 21 UTC (0) | +0 / 0.87 | 22 UTC (0) |
| 2026H1 | – | – | +1 / 0.71 | 22 UTC (0) |

Expected for UTC: shift 0 in both seasons, break at 21 UTC (US DST) / 22 UTC (US standard), i.e. 17:00 New York.

### January 2026 (flagged above: range-profile shift +1, corr 0.71)

One month of hourly range profile is noisy, so the exact session edges (timestamps
only) were checked. In **January 2026 every session resumes at 23:00 UTC** (20 of
20) **and the last bar before the break is at 21:59 UTC** (19 of 20; one early
close at 19:28). That is 18:00 / 17:00 New York in winter, identical to December
2025 (23:00 on 19 of 21, 21:59 on 20 of 21). **Conclusion: UTC throughout
2024-01 .. 2026-01; no shift applied.** The weak January profile match is noise
from one volatile month, not a timezone change. The source's timezone change is
dated 2026-02-01, and the file has no rows after January 2026.
