# variance_ratio: report

## 1. Verdict: EXPLORATORY, descriptive, no pass/fail

**What was run:** Lo–MacKinlay variance ratios on dev 2016–2023 for XAUUSD, XAGUSD, EURUSD and
USDJPY. It ran once, attempt 1 (`ATTEMPTS.json`, kind `descriptive_analysis`), under the plan in
`PREREG.md` (V1–V9), which was committed before any read (b2f4d7f). Results are in `results/`.

**Overall:**
- **At the 1-hour base, no market departs from a random walk over the whole sample.** For
  k = 2, 4 and 8 hours, VR is between 0.978 and 1.036, with |z| ≤ 1.68.
- **Of the 438 cells with a p-value, 5 have a BH-adjusted p below 0.05.**
  - 2 are 1-hour cells: gold and silver, windows starting at **20:00 UTC, k = 2**, both
    **VR < 1**: XAUUSD 0.79, z −3.51, n 676; XAGUSD 0.73, z −3.51, n 681.
  - 3 are the 5-min check on the whole sample: **XAGUSD k = 2 h (0.863) and k = 4 h (0.865),
    EURUSD k = 2 h (0.930)**. All are below 1: mean reversion measured on 5-min returns that
    the 1-hour base doesn't show.

**Not estimable:**
- **k = 24 hours: in no market.** Every 24-hour window crosses a daily pause of more than
  15 minutes: the metals' daily break, and a 16-minute IBKR pause in FX at 17:00 New York
  (1,652 times in dev). The plan (V4, D4) excludes those returns.
- **k = 8 hours in the New York session**, for the same reason.
- This is the literal rule. A trading-time rule that joins the closes on either side of the
  scheduled pause would make k = 24 estimable, but it needs an amendment and a new attempt (§5).

## 2. Data and checks

- **Bars:**
  - every file went through `data_guard.assert_may_read(..., "variance_ratio")` and `manifest.verify`;
  - every bar was checked by `sealing.assert_period(market, ts, "dev")`;
  - the hashes are in `results/summary.json`;
  - no row outside [2016-01-01, 2024-01-01) was kept.
- **σ_240:** gold's pinned HAR `parquet2021_2023` on each market's bars, with the label at the
  window start. 115–186 valid starts per market have no state (the warm-up in January 2016) and
  sit in no tercile.
- **News days:** data/macro-calendar, PR #60, commit **12f65c6**, its anchor check PASS.
  The parquet's SHA-256 is `dfde970c…`, as pinned. That gives 539 High-impact usd_tier1 rows
  on 251 dev days.

**Base returns, 1 hour (every slot of the dev grid gets one reason):**

| Market | Valid | Weekend | Daily break | Holiday | Gap | Short day | Seam | Windows k = 2/4/8/24 h |
|---|---|---|---|---|---|---|---|---|
| XAUUSD | 45,119 | 20,482 | 3,754 | 505 | 261 | 6 | 0 | 43,054 / 38,926 / 30,674 / 0 |
| XAGUSD | 45,131 | 20,482 | 3,754 | 549 | 211 | 0 | 0 | 43,070 / 38,948 / 30,704 / 0 |
| EURUSD | 47,626 | 20,482 | 1,703 | 263 | 45 | 8 | 0 | 45,553 / 41,407 / 33,115 / 0 |
| USDJPY | 47,629 | 20,482 | 1,701 | 263 | 44 | 8 | 0 | 45,556 / 41,410 / 33,118 / 0 |

- **5-min:** the counts per reason are in `results/summary.json`.
- **Seam:** the gold seam (2026-02-01) is outside dev, so its count is 0, as expected.

## 3. Whole sample

Each cell is VR / z / n (windows). `*` marks a BH-adjusted p below 0.05, over all 438 cells.

| Market | Base | k = 2 h | k = 4 h | k = 8 h | k = 24 h |
|---|---|---|---|---|---|
| XAUUSD | 1h | 1.002 / +0.21 / 43,054 | 1.021 / +1.11 / 38,926 | 1.029 / +1.01 / 30,674 | n/a (n 0) |
| XAUUSD | 5min | 0.958 / −2.40 / 516,708 | 0.977 / −0.99 / 467,064 | 0.988 / −0.37 / 367,910 | n/a (n 0) |
| XAGUSD | 1h | 0.981 / −1.68 / 43,070 | 0.985 / −0.69 / 38,948 | 0.995 / −0.14 / 30,704 | n/a (n 0) |
| XAGUSD | 5min | 0.863* / −6.60 / 517,107 | 0.865* / −4.84 / 467,643 | 0.866 / −3.34 / 368,715 | n/a (n 0) |
| EURUSD | 1h | 0.998 / −0.26 / 45,553 | 0.999 / −0.03 / 41,407 | 0.978 / −0.85 / 33,115 | n/a (n 0) |
| EURUSD | 5min | 0.930* / −4.69 / 540,456 | 0.938 / −3.00 / 490,704 | 0.924 / −2.66 / 391,200 | n/a (n 0) |
| USDJPY | 1h | 1.014 / +1.24 / 45,556 | 1.034 / +1.58 / 41,410 | 1.036 / +1.07 / 33,118 | n/a (n 0) |
| USDJPY | 5min | 0.978 / −0.82 / 540,490 | 1.002 / +0.06 / 490,738 | 1.014 / +0.35 / 391,234 | n/a (n 0) |

**Reading, exploratory:**
- At the 5-min base, silver and EURUSD show VR well below 1 at k = 2–8 h. The 1-hour base
  sees none of it. That fits negative autocorrelation inside the hour, at the 5-min scale.
- These are MIDPOINT bars, so this isn't trade bid-ask bounce, but quote noise can still do
  it. This analysis can't tell which.

## 4. Splits

- **Every cell:** `results/tables.md` has one table per market, with VR / z / n for every
  base, split, cell and k. `results/vr_cells.csv` has the same, with p and BH p.
- **Heatmaps** of hour × k, VR on the 1-hour base, are in `results/heatmap_<market>.png`.

What stands out (none of it BH-significant except the 20:00 UTC cells):
- **Hour:** in the metals, windows that end at the daily break lean below 1:
  - XAUUSD: 18:00 k = 4 at 0.80 (z −2.56), 19:00 k = 2 at 0.91 (z −1.81);
  - XAGUSD: 18:00 k = 4 at 0.86 (z −1.89);
  - the 20:00 k = 2 cells of both metals survive BH.
  - Windows starting at 20:00 UTC with k = 2 exist only when 21:00 UTC is a trading hour,
    i.e. mostly in US winter time.
- **σ tercile:**
  - USDJPY leans above 1 at k = 8 in the low and mid terciles (1.083, z +2.90; 1.114, z +3.18;
    BH 0.14 and 0.08);
  - XAGUSD leans below 1 in the mid tercile (k = 2: 0.965, z −3.16, BH 0.08);
  - EURUSD leans below 1 in the low tercile at k = 2 and 4.
- **News days:** nothing stands out. The news cells reach |z| 1.48 at most, the no-news cells
  1.87.
- **Weekday:** nothing passes BH. The largest is XAGUSD Thursday, k = 2, at z −3.07 (BH 0.09).

## 5. For the owner

1. **k = 24 h is empty.** If you want it, an amendment could define returns in trading time:
   the close before a scheduled pause (the metals' daily break, FX's 16-minute pause) joined
   to the next close. That would be a new attempt (2).
2. **The 15-minute gap limit sits one minute under FX's daily pause.** The same amendment
   could either treat that pause as scheduled, or raise the limit.
3. **The 5-min check disagrees with the 1-hour base for silver and EURUSD.** If it matters,
   the next step is the 5-min base by split, as a new descriptive attempt.
4. **The plan isn't merged to main** (DECISIONS D2). This is descriptive and EXPLORATORY,
   with no hypothesis and no pass/fail, so nothing here may be cited as a confirmed effect.
