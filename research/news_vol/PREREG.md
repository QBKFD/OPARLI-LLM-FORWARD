# news_vol (Study A): pre-registration

**Do scheduled US macro releases make gold's hourly HAR volatility forecast
better if the forecast knows they're coming?**

**Status.** D1–D9 below were APPROVED by the owner on 2026-11-01. The rows
marked N are implementation choices Claude made under the owner's
unattended-run rule (take the conservative option and record it). Their reasons
and alternatives are in `DECISIONS.md`. The commit that merges this file into
`main` is the pre-registration. Before that merge, nothing here has been
computed on a real outcome; only counts and timestamps were read (§8). The
code is in this folder: `nv.py`, `nv_core.py`, `nv_data.py`, `nv_synth.py`,
`nv_counts.py` and `nv_run.py`, with constants in `nv_params.py`.

## 0. Decisions

| id | item | value | status |
|---|---|---|---|
| D1 | Market and period | **XAUUSD dev 2016-01-01 .. 2023-12-31.** XAGUSD and EURUSD come later, as separate replications with their own PREREGs | APPROVED (owner, 2026-11-01) |
| D2 | Events | #60's **usd_tier1** set, rows **with a timestamp**. Releases in the same minute are grouped into one event. **FOMC is its own type** | APPROVED (owner, 2026-11-01) |
| D3 | Horizons | **H = 240 primary**, H = 60 secondary | APPROVED (owner, 2026-11-01) |
| D4 | Origins and windows | **hourly forecast origins.** A release window has a grouped event in (t, t+H] (N1). **Windows that cross the daily break or a weekend are excluded** | APPROVED (owner, 2026-11-01) |
| D5 | Arms | both arms are **refit walk-forward**: expanding from 2016, **yearly test folds 2018–2023**. vol-block params and β are fitted per training fold. The pinned models aren't used: their fits overlap dev. **Augmented = HAR + I(data release in window) + I(FOMC in window)** | APPROVED (owner, 2026-11-01) |
| D6 | Q1 | mean log(RV / forecast) in release windows minus non-release windows, out of fold. **Month-block bootstrap, 10,000 resamples, fixed seed, one-sided > 0** | APPROVED (owner, 2026-11-01) |
| D7 | Q2, the verdict | **QLIKE, two-sided Diebold–Mariano with Newey–West** (lag as sigma_compare). **PASS:** augmented lower at H = 240 with BH p < 0.05, and not significantly worse at H = 60. Otherwise **FAIL** | APPROVED (owner, 2026-11-01) |
| D8 | Q3, secondary | the Spearman correlation of log(RV / forecast) with the signed window return, in release windows; bootstrap CI. Reported, not part of the verdict | APPROVED (owner, 2026-11-01) |
| D9 | Multiplicity | **BH over Q1, Q2 (both H) and Q3; q = 0.05** | APPROVED (owner, 2026-11-01) |
| N1 | "In (t, t+H]" | a release at minute e counts for origin t when its reaction bar, the 1-min bar opening at e, closes in (t, t+H]. That means **t ≤ e < t+H**. The window's returns are those of the bars opening in [t, t+H), which together span (t, t+H]. Reading the release instant itself as the point in (t, t+H] differs only for releases on a whole hour. That is FOMC at 14:00 NY: it would flag the quiet window that ends at 14:00 and leave the reaction window unflagged. The literal reading's counts are in §8 | Claude (DECISIONS N1) |
| N2 | Q1 and Q3 horizon | **H = 240** (D3's primary). D9 counts one p for each. Q1 at H = 60 is reported, outside BH | Claude (N2) |
| N3 | Release window for Q1/Q3 | the window holds **any** grouped event (data or FOMC) | Claude (N3) |
| N4 | Training rows | the fold's **non-crossing** windows only (D4 applied to fitting), the same rows for both arms | Claude (N4) |
| N5 | Bootstrap p | Q1: p = (1 + #{diff\* ≤ 0}) / (B + 1). Q3: p = min(1, 2·min(1 + #{ρ\* ≤ 0}, 1 + #{ρ\* ≥ 0}) / (B + 1)). Both: 95 % percentile CI, **seed 20261101**, blocks = UTC calendar month of t | Claude (N5) |
| N6 | DM | d = QLIKE_aug − QLIKE_HAR per row, rows pooled over folds **in time order**, NW Bartlett **lag 8 rows** at both H, as sigma_compare; normal two-sided p | Claude (N6) |
| N7 | Grouping | the type of a minute is FOMC if any of its rows is an FOMC row, otherwise data. The FOMC press conference (14:30 NY) is a separate FOMC event | Claude (N7) |
| N8 | Unscheduled FOMC | 2020-03-03 (10:00 NY) and 2020-03-15 (17:00 NY Sunday, market closed) stay in the set, as D2 says. The first is a look-ahead for a forecaster; it is one event | Claude (N8) |

## 1. Question and why

The HAR forecast on (L, S_hd, T) (`research/sigma_compare`, which replaced the
plug-in) knows nothing about the calendar. A CPI, NFP or FOMC release is known
in advance and moves gold. If the forecast is told a release is coming, does it
improve out of sample?

- **Q1** asks whether HAR is biased low in release windows.
- **Q2**, the verdict, asks whether adding release indicators lowers QLIKE.
- **Q3** asks whether the vol surprise in release windows leans with the
  window's direction.

**Honesty notes.**
- **Gold dev 2016–2023 is not unseen.** It was read by `pullback_structure`
  and `variance_ratio`. The parquet 2021–2023 part is the DEV of the vol
  rounds and of `sigma_compare`, and 2016–2020 lies inside their CONF.
  (L, S_hd, T) and HAR were chosen on this data.
- **It is already known that releases spike 1-min ranges.**
  `database/historical_news/DATA_NOTES_events.md`, Stage 3, shows the ratio at
  minute 0 is about 4–10× for 2020–2025. **Q1's direction is close to known.**
  The open question is Q2: whether the indicators beat HAR out of sample, net
  of what L, S_hd and T already see.
- The independent test of any gain would be XAGUSD and EURUSD (D1, later),
  or the sealed data.

## 2. Data and seals

- **Bars:** `database/ohlcv_data/XAUUSD.txt`.
  - It is read through the audit_long cache, as `variance_ratio` and
    `pullback_structure` read it. UTC.
  - Every read goes through `data_guard.assert_may_read(..., "news_vol")` and
    `manifest.verify`.
  - Rows are kept only in [2016-01-01, 2024-01-01), with
    `sealing.assert_period("XAUUSD", ts, "dev")`.
- **Calendar:** `database/historical_news/macro_calendar_utc.parquet`, the clean
  table of PR #60, merged as 0988ca3. Its anchor check passed.
  - SHA-256 is pinned: `dfde970cdcd56b5782e9bc2c4f4cf2c9f6b49e9f05ee380d5b89ef8b0bdad82d`.
  - The manifest marks it "unused by any study", so it is read through
    `data_guard`, which this file allows by naming it.
  - Only timestamps and event names are used. **No actual, forecast or
    surprise value is read.**
- **Seals:** nothing from 2024-01-01 is read, and nothing from 2026-10-01.
  Every window ends by 2024-01-01. Rows and windows are asserted against both.

## 3. Events (D2, N7, N8)

- **The set:** rows with currency USD, impact High, and event in {CPI m/m,
  CPI y/y, Core CPI m/m, Non-Farm Employment Change} (type **data**) or in
  {FOMC Statement, Federal Funds Rate, FOMC Press Conference} (type **FOMC**).
- **Timing:** `ts_utc` must be present and in dev. Untimed rows are excluded
  and counted (§8).
- **Grouping:** rows are grouped by their minute.

## 4. Origins, windows and the target

As `research/sigma_compare`, through its `build_context`, reused unchanged.

- **Origins t:** the hourly close-time labels of `vol_block` (whole hours with
  at least one bar in the hour before).
- **Window W(t, H):** the first H market-open minutes from t. The calendar is
  Sun 18:00 to Fri 17:00 New York, without the daily 17:00–18:00 break.
- **Crossing:** a window that spans a closed minute crosses. It is excluded
  from training and testing (D4, N4).
- **RV:** the sum of squared 1-min log returns of the bars opening in W(t, H),
  with the 5-minute gap rule.
- **Validity:** a window is valid with ≥ 90 % of its H minutes with a return,
  RV > 0, and an end by 2024-01-01.
- **Indicators:** I_data(t, H) and I_FOMC(t, H) are 1 when a grouped event of
  that type falls in the window (N1).

## 5. Arms and folds (D5)

- **Test folds:** the calendar years 2018, …, 2023.
- **Fitting:** for test year [s_k, e_k):
  - **VolParams_k** = `vol_block.fit`(bars in [2016-01-01, s_k − 24 h));
  - L, S_hd and T come from `vol_block.transform`(VolParams_k, all bars). It is
    causal: a label uses only bars before it;
  - P_k(t, H) is the seasonal mass of the window under VolParams_k's profile.
- **Training rows:** t in [2016-01-01, s_k − 24 h], window end ≤ s_k, valid,
  non-crossing, with L, S_hd and T finite.
- **HAR:** OLS of ln(RV / P_k) on [1, L, S_hd, T], with Duan smearing
  c = mean exp(residual).
  - Forecast h = P_k · exp(x·β) · c.
- **Augmented:** the same, on [1, L, S_hd, T, I_data, I_FOMC].
- **Test rows:** labels in [s_k, e_k): valid, non-crossing, features finite,
  both forecasts finite and > 0. **The same rows serve both arms.**

## 6. Questions, multiplicity and verdict

- **Q1 (D6, N2, N3, N5).**
  - y = log(RV / h_HAR) on test rows at H = 240.
  - The statistic is mean(y | release) − mean(y | no release).
  - It uses a month-block bootstrap: 10,000 resamples, seed 20261101, one-sided
    > 0.
- **Q2 (D7, N6).**
  - d = QLIKE(RV, h_aug) − QLIKE(RV, h_HAR), with QLIKE = RV/h − ln(RV/h) − 1.
  - It is a DM test at H = 240 and at H = 60: NW lag 8, two-sided.
- **Q3 (D8, N2, N5).**
  - On the H = 240 release rows: Spearman ρ between y and the signed window
    return, log(last close in W) − log(close before t).
  - Month-block bootstrap CI, with a two-sided p.
- **BH (D9):** over the four p-values Q1, Q2 at H = 240, Q2 at H = 60 and Q3,
  with q = 0.05.
- **Verdict (D7), literal, no override:**
  - **PASS** if mean d < 0 at H = 240 with BH p < 0.05, **and not** (mean
    d > 0 at H = 60 with BH p < 0.05).
  - **FAIL** otherwise.
  - Q1 and Q3 are reported with their BH p and don't change the verdict.
- **Reported, outside the verdict:**
  - QLIKE of each arm;
  - Q1 at H = 60;
  - the coefficients on I_data and I_FOMC per fold;
  - the per-fold mean d;
  - the row counts.

## 7. Machinery check (before the run, from the merged commit)

The same code runs on **synthetic 1-min bars** (`nv_synth.py`). The bars use the
real event timestamps (timestamps only) and no real price. They lie on the
market-open minutes of 2015-12-25 .. 2024-01-01, with the same folds.

- **Per-minute variance:**
  - 1e-8 × (1 + 1.5·exp(−((UTC hour − 14) / 2.5)²)) × exp(x_hour);
  - x_hour is an hourly AR(1): φ 0.98, innovation sd 0.20;
  - in the **effect** world, the 30 minutes from each grouped event's minute
    are × 4;
  - in the **null** world there is no multiplier;
  - one seed, 20261101, so the two worlds share every random draw.
- **Pass, all of these:**
  - **effect:** Q1 > 0 with p < 0.05;
  - **effect:** Q1's estimate within ×0.5–×2 of the generator's true
    difference (computed from its variance path);
  - **effect:** augmented QLIKE lower with DM p < 0.05 at H = 240 and at H = 60;
  - **null:** DM p > 0.05 at both H.
- **If any fails:** step 1 stops, with a report and no amendment. A smaller
  version runs in `test_news_vol.py`.

## 8. Counts and power (no outcome)

`results/counts.md` and `results/counts.json` (`nv_counts.py`) hold the counts:
bar times and calendar timestamps only, no price column. The summary is below.

- **Events in dev:**
  - 539 timed usd_tier1 rows give **310 release minutes**: 192 data and 118 FOMC.
  - No minute mixes the two types.
  - Each year has 24 data minutes and 12–18 FOMC minutes.
  - **2 rows are untimed** and excluded: the two CPI y/y rows with a source time
    conflict (#60 MC3). Their CPI m/m siblings keep each release in the set.
- **Test folds 2018–2023, by timestamps (the run may drop a few more rows):**

  | H | rows a year | release rows | data | FOMC | both | literal (t, t+H] reading, any |
  |---|---|---|---|---|---|---|
  | 240 | 4,850–4,886 | **703** | 560 | 143 | 0 | 749 |
  | 60 | 5,615–5,662 | **188** | 140 | 48 | 0 | 230 |

  - **FOMC at H = 240 gets only 3 windows per meeting** (t = 11:00, 12:00 and
    13:00 New York): the 14:00 window crosses the daily break, which D4
    excludes.
  - **At H = 60 FOMC has one window**, 14:00, holding both the statement and
    the press conference.
- **Power (rough, analytic, `results/counts.md`):**
  - **Release dates are the cluster:** 185 in the test folds.
  - The residual sd of log(RV/h) is taken as 0.55, sigma_compare's HAR
    log-MSE (seen data).
  - **Q1's minimum detectable difference** at 80 % power and α = 0.0125
    (BH's strictest rank of 4) is **0.126 log points, about +13 % variance**.
  - **Q2 power** with an oracle augmented arm is 0.97 at a log bias of 0.1 in
    release windows, and ≈ 1 from 0.2. This is optimistic: lognormal
    residuals, no estimation noise.
  - **What that means:** a release effect of the size Stage 3 of the calendar
    notes shows (minute-0 ranges 4–10×) should be detectable. A FAIL would
    mean HAR's L, S_hd and T already carry it, or that it is too small to
    matter at 60–240 minutes.
- **Machinery, in development:**
  - The section 7 check ran once on the full synthetic period while the code
    was written (N10), and passed.
  - Q1 came out at 0.88× the generator's true difference. The effect was found
    at both H; the null showed no gain (DM p 0.79 at H = 240, 0.23 at H = 60).
  - The formal check runs from the merged commit.

## 9. The run

- **ATTEMPTS.json** is bumped **before** the run (`outcome_analyses` 0 → 1).
- **One run** (`nv_run.py`) from a commit at or after the merge of this file.
- **No rerun with changed settings.** A bug fix means one rerun, with both
  documented.
- **Outputs:** `results/run.json` (every number above), `results/coefs.csv`
  and `REPORT.md` with the verdict first. Aggregates only: no price, no
  per-row output.
- **Results:** on branch `research/news-vol-results`, PR, not merged.
