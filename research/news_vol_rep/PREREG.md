# news_vol_rep (Study A replication): pre-registration

**Does Study A's result hold on silver and on EURUSD?** Study A
(`research/news_vol`, PREREG merged as 843b447, N1 approved 2026-11-02)
found that telling gold's hourly HAR volatility forecast about scheduled US
releases lowers its QLIKE out of sample (PASS, −18.6 % at H = 240). This
study runs the same design on XAGUSD and EURUSD. Only the markets change.

**Status.**
- R1–R5 below were APPROVED by the owner on 2026-11-02.
- The rows marked RN are implementation choices Claude made. Their reasons and
  alternatives are in `DECISIONS.md`.
- The commit that merges this file into `main` is the pre-registration.
- Before that merge, nothing here has been computed on a real outcome. Only bar
  open times and calendar timestamps were read (§8).
- The code is in this folder: `rep_params.py`, `rep_data.py`, `rep_core.py`,
  `rep_counts.py` and `rep_run.py`. It runs Study A's `nv.py` and `nv_core.py`
  unchanged, and `rep_params.py` imports Study A's constants instead of copying them.

## 0. Decisions

| id | item | value | status |
|---|---|---|---|
| R1 | Markets and period | **XAGUSD and EURUSD, dev 2016-01-01 .. 2023-12-31**, from their manifest files through `data_guard` and sealing (dev only) | APPROVED (owner, 2026-11-02) |
| R2 | Design | **identical to Study A**: D2–D9 and N1. usd_tier1 events, grouped by minute, FOMC its own type. H = 240 primary, 60 secondary. Hourly origins. Walk-forward refit **per market**, expanding from 2016, yearly test folds 2018–2023. The same Q1, Q2, Q3 and statistics | APPROVED (owner, 2026-11-02) |
| R3 | Excluded windows | windows crossing **each market's daily pause** (metals: the daily break; EURUSD: the 17:00 NY pause) or a weekend | APPROVED (owner, 2026-11-02) |
| R4 | Verdict and multiplicity | **a verdict per market by D7. BH over all tests of both markets, q = 0.05.** The replication **holds if both markets PASS**. Each is reported separately | APPROVED (owner, 2026-11-02) |
| R5 | Events | **US releases only**, as in Study A. ECB and other EUR events are not flagged; they stay in the non-release windows. A limitation, counted in §8 | APPROVED (owner, 2026-11-02) |
| RN1 | EURUSD's calendar | sigma_compare's `build_context` is used **unchanged** for both markets. EURUSD's pause, from its bar times, is **17:00–17:15 NY**, and it opens on **Sunday at 17:15 NY**. For hourly origins, that calendar and the metals one flag **the same windows as crossing (0 differ, §8)**. Every non-crossing window covers the same H minutes, so the sample, RV and P are those of EURUSD's own calendar. **The run refuses if a difference appears** | Claude (RN1) |
| RN2 | Bars | each market's IBKR parquet. **The read is filtered to dev**, so no holdout row is loaded. `sealing.assert_period(market, ts, "dev")` checks every kept row | Claude (RN2) |
| RN3 | BH family | **8 p-values**: Q1, Q2 at H = 240, Q2 at H = 60 and Q3, for each market. Each market's D7 verdict uses those BH p-values. Q1 at H = 60 is reported per market, outside BH (Study A N2) | Claude (RN3) |
| RN4 | Machinery check | Study A's generator, seed and pass rule, run **on each market's real dev bar open times** (timestamps only, no price) instead of the full open-minute grid. **Both markets must pass** | Claude (RN4) |
| RN5 | Seeds | Study A's: 20261101 for the bootstraps and the synthetic check, for both markets | Claude (RN5) |
| RN6 | Power | Study A's analytic power, with **gold's residual sd 0.55** (no silver or EURUSD value is known without an outcome) and **α = 0.05 / 8**, BH's strictest rank | Claude (RN6) |
| RN7 | What was looked at | bar open times (for the pause pattern and the counts) and calendar timestamps, before this file was fixed. No price, no outcome | Claude (RN7) |

## 1. Question and why

- **The question is Study A's**, asked on two other markets. Q1, Q2 (the
  verdict) and Q3 are defined in Study A's PREREG §1 and §6.
- **Why these two:** Study A's D1 named XAGUSD and EURUSD for the replication.
  - Silver is a second metal on the same calendar.
  - EURUSD is a currency pair, where a US release moves the dollar side.
  - A PASS on both would make Study A's gain more than a gold-only fact.

**Honesty notes.**
- **Study A's result is known, and it was strong** (DM z −8.25 at H = 240).
  So the direction here is expected. What is new is whether it survives the
  change of market.
- **These dev periods aren't unseen.**
  - Silver dev was read by `breakout_fade` and `variance_ratio`.
  - EURUSD dev was read by `variance_ratio`.
  - Neither study looked at release windows or at HAR forecasts.
- **HAR's features (L, S_hd, T) were chosen on gold.** Here they are refit per
  market and per fold (R2), but their definition isn't re-chosen.

## 2. Data and seals

- **Bars, one file per market:**
  - XAGUSD: `database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet`
  - EURUSD: `database/ohlcv_data/EURUSD_ibkr_1min_20160101_20260930.parquet`
  - Every read goes through `data_guard.assert_may_read(..., "news_vol_rep")`
    (this file names both) and `manifest.verify`.
  - The read is filtered to [2016-01-01, 2024-01-01) (RN2). Each kept row is
    checked with `sealing.assert_period(market, ts, "dev")`.
- **Calendar:** `database/historical_news/macro_calendar_utc.parquet`, Study A's
  file. Its SHA-256 is pinned:
  `dfde970cdcd56b5782e9bc2c4f4cf2c9f6b49e9f05ee380d5b89ef8b0bdad82d`.
  - Only timestamps, currencies, impact and event names are used. **No
    actual, forecast or surprise value is read.**
- **Seals:** nothing from 2024-01-01 is read, and nothing from 2026-10-01.
  Every window ends by 2024-01-01. Rows and windows are asserted against both.

## 3. Events (R2, R5)

- **As Study A §3:**
  - USD, High impact, event in {CPI m/m, CPI y/y, Core CPI m/m, Non-Farm
    Employment Change} (type **data**) or {FOMC Statement, Federal Funds Rate,
    FOMC Press Conference} (type **FOMC**);
  - timed rows only, grouped by minute;
  - the type of a minute is FOMC if any of its rows is.
- **The same event set serves both markets.**
- **EUR events (R5):** EUR High-impact rows are counted (§8) and never flagged.

## 4. Origins, windows and the target (R2, R3, RN1)

- **As Study A §4**, through `build_context`, unchanged:
  - hourly origins;
  - the window is the first H open minutes from t;
  - RV is the sum of squared 1-min log returns, with the 5-minute gap rule;
  - a window is valid with ≥ 90 % coverage, RV > 0 and an end by 2024-01-01;
  - **crossing windows are excluded** from training and testing.
- **N1 (approved):** a release at minute e is in origin t's window when
  t ≤ e < t+H.
- **Each market's pause (R3):**
  - **XAGUSD:** the daily break is 17:00–18:00 NY, as gold's. The bar times
    have **no bar** in it.
  - **EURUSD:** the bar times show a pause **[17:00, 17:15) NY** on every
    weekday, and a Sunday open at 17:15 NY. RN1 explains why the metals
    calendar excludes exactly the windows that cross it.

## 5. Arms and folds (R2)

As Study A §5, **per market**:
- **Folds:** test years 2018–2023, training expanding from 2016-01-01 to 24 h
  before each test year.
- **Per fold:** `vol_block.fit` on the fold's training bars, then HAR on
  [1, L, S_hd, T] and the augmented arm [1, L, S_hd, T, I_data, I_FOMC]. Both
  are OLS on ln(RV / P), with Duan smearing.
- **Rows:** the same test rows serve both arms.

## 6. Questions, multiplicity and verdict (R4, RN3)

- **Per market, as Study A §6:**
  - **Q1:** mean log(RV / h_HAR), release minus non-release windows, at
    H = 240. Month-block bootstrap: 10,000 resamples, one-sided > 0.
  - **Q2:** QLIKE of the augmented arm minus HAR, as a two-sided DM test at
    H = 240 and at H = 60, NW lag 8.
  - **Q3:** Spearman ρ of log(RV / h_HAR) with the signed window return, on the
    H = 240 release rows. Bootstrap, two-sided.
- **BH:** over the **8** p-values (4 per market), q = 0.05.
- **Verdict per market (D7), literal, no override:**
  - **PASS** if mean d < 0 at H = 240 with BH p < 0.05, **and not** (mean
    d > 0 at H = 60 with BH p < 0.05);
  - **FAIL** otherwise.
- **The replication holds if both markets PASS.** Each market's verdict is
  reported on its own either way.
- **Reported, outside the verdict, per market:**
  - QLIKE of each arm;
  - Q1 at H = 60;
  - the coefficients on I_data and I_FOMC per fold;
  - mean d per fold and by window type;
  - the row counts.

## 7. Machinery check (before the run, from the merged commit)

**Study A's §7 check, per market, on that market's real dev bar open times**
(RN4). No real price is used.

- **The generator:**
  - per-minute variance 1e-8 × (1 + 1.5·exp(−((UTC hour − 14) / 2.5)²)) ×
    exp(x_hour);
  - x_hour is an hourly AR(1): φ 0.98, innovation sd 0.20;
  - in the **effect** world, the 30 minutes from each grouped event's minute
    are × 4;
  - in the **null** world there is no multiplier;
  - one seed, 20261101, so the two worlds share every random draw.
- **Pass, each market, all of these:**
  - **effect:** Q1 > 0 with p < 0.05;
  - **effect:** Q1's estimate within ×0.5–×2 of the generator's true
    difference;
  - **effect:** augmented QLIKE lower with DM p < 0.05 at H = 240 and at H = 60;
  - **null:** DM p > 0.05 at both H.
- **If any check fails in either market,** the study stops, with a report and
  no amendment. A smaller version runs in `test_news_vol_rep.py`.

## 8. Counts and power (no outcome)

`results/counts.md` and `results/counts.json` (`rep_counts.py`) hold the counts:
bar open times and calendar timestamps only, no price column.

- **Events in dev, both markets:**
  - **310 release minutes:** 192 data and 118 FOMC. These are Study A's.
  - 2 rows are untimed and excluded (#60 MC3).
- **Bars in dev:**
  - **XAGUSD:** 2,832,852, from 2016-01-04 to 2023-12-29 20:59 UTC. None falls
    in the 17:00–18:00 NY break.
  - **EURUSD:** 2,951,562 over the same span. 74,159 weekday bars fall in the
    17:00 NY hour, all from 17:15 on. **Its pause is [17:00, 17:15) NY.**
- **RN1's check, EURUSD:** of the hourly labels, 8,280 cross at H = 240 and
  2,061 at H = 60, under both calendars. **0 differ.**
- **Test folds 2018–2023, by timestamps** (the run may drop a few more rows):

  | market | H | rows a year | release rows | data | FOMC | both | literal (t, t+H] reading, any |
  |---|---|---|---|---|---|---|---|
  | XAGUSD | 240 | 4,852–4,889 | **710** | 564 | 146 | 0 | 757 |
  | XAGUSD | 60 | 5,618–5,660 | **190** | 141 | 49 | 0 | 233 |
  | EURUSD | 240 | 5,151–5,198 | **723** | 576 | 147 | 0 | 770 |
  | EURUSD | 60 | 5,925–5,978 | **194** | 144 | 50 | 0 | 237 |

  Study A's gold had 703 and 188.
- **EUR events (R5, not flagged):**
  - 412 EUR High-impact release minutes in dev.
  - In the test rows at H = 240, **767 non-release windows hold an EUR event**
    (2.5 % of EURUSD's rows), and 35 hold both an EUR event and a US release.
  - At H = 60: 225 and 6.
  - If EUR events raise EURUSD's vol, these windows push the "no release"
    group up. That works against Q1 and Q2 for EURUSD.
- **Power (rough, analytic, RN6):**
  - **Release dates are the cluster:** 187 (XAGUSD) and 191 (EURUSD) in the
    test folds.
  - **Q1's minimum detectable difference**, at 80 % power and α = 0.05/8, is
    **0.136 (XAGUSD) and 0.135 (EURUSD) log points**. Gold's Q1 was +1.04.
  - **Q2 power** with an oracle augmented arm is ≈ 0.96 at a log bias of 0.1 in
    release windows, and ≈ 1 from 0.2. This is optimistic: lognormal
    residuals, no estimation noise.
- **Machinery check, a development run (RN4):**
  - It ran once, on the full period with real bar times and event times,
    while the code was written. It passed in both markets.
  - Q1 came out at 0.85× the generator's true difference in each.
  - The null showed no gain:
    - XAGUSD: DM p 0.71 at H = 240 and 0.89 at H = 60;
    - EURUSD: 0.73 and 0.16.
  - The formal check runs from the merged commit.

## 9. The run

- **ATTEMPTS.json** is bumped **before** the run (`outcome_analyses` 0 → 1).
- **One run** (`rep_run.py run`) reads both markets, from a commit at or after
  the merge of this file.
- **No rerun with changed settings.** A bug fix means one rerun, with both
  documented.
- **Outputs:** `results/run.json` (every number above), `results/coefs.csv`
  and `REPORT.md`, verdicts first. Aggregates only: no price, no per-row
  output.
- **Results:** on branch `research/news-vol-rep-results`, PR, not merged.
