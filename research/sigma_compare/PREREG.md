# Pre-registration: which sigma forecast for risk/ and scoring?

Committed **before any result**. Any change from the owner's review is
committed before code first runs on real data; after that, this file is never
edited. Deviations go to "Amendments" in REPORT.md. Research only: nothing
outside `research/sigma_compare/` changes, apart from step 3's proposal (which
is a proposal, not a change).

## Question

`risk/` and scoring take a sigma over the next H minutes. Today that sigma is
the plug-in from `datafeed.features.vol_block.sigma_over`, which was an
engineering decision. Does a fitted forecast do significantly better?

## Honesty note (repeated in the report)

- **CONF 2007–2020 is not independent.** L, S and T were selected on it (vol
  rounds 2 and 3). Round 3 found there that OLS on (L, S, T) forecasts
  deseasonalized log variance well at 4h and 24h, which favours candidate (b),
  at least on log-scale MSE. How (b) compares with the plug-in on QLIKE has not
  been looked at.
- **The plug-in has been checked on DEV's test years.** Its calibration on the
  parquet for 2022–2023 was looked at in `tools/sigma_calibration`, so the DEV
  results for (a) are not new.
- The independent test is the sealed data (2026-10-01 onwards), at the very end.

## Data and seal

- **CONFIRMATION (primary, the verdict is judged here):** `XAUUSD.txt` 2007–2020
  via `research/vol_features_r3` `load_conf` (UTC, < 2021-01-01).
  - The data audit (`research/vol_features/DATA_AUDIT_LONG.md`) shows the daily
    break was partly traded in 2007–2011, at 31–40 bars per hour.
  - Open minutes are therefore defined by the fixed calendar below, never by
    which bars exist.
- **DEVELOPMENT (reported only):** the clean parquet, 2021–2023 with 2020 as
  warm-up, via `research/vol_features` `load_minutes`. 2020–2023 match
  `XAUUSD.txt` close for close.
- **Seal:**
  - Every loader calls `research/sealing.py` `assert_before_global_seal` (no
    timestamp at or after 2026-10-01).
  - Every loader also asserts < 2024-01-01; CONF additionally asserts
    < 2021-01-01.
  - Every target and path window ends at or before its period end (CONF
    2021-01-01, DEV 2024-01-01).
  - Asserts run on the loaders, the frames and the samples. A test checks that
    they fire.
  - Nothing from 2024-01 to 2026-01 (spent) or from 2026-10 onwards (sealed) is
    read.
- **Gap rule (research):** a 1-min return is dropped if its bar opens more than
  5 minutes after the previous bar.

## Forecast times and windows

- **Forecast times t:** the hourly labels of `vol_block.transform` (1H rows
  labelled by close time), i.e. whole hours that have at least one bar in the
  hour before.
- **Market-open minute:** u is open if `datafeed.ingest.xauusd_bars._market_open(u)`
  is True. That is Sun 18:00 to Fri 17:00 New York, without the daily
  17:00–18:00 break. Holidays are not modelled.
- **Window W(t, H):** the first H market-open minutes u >= t (minute opens), for
  H in {60, 240}. Its end E is the last minute in W plus 1 minute.
- **Crossing windows:** a window crosses a close if [t, E) contains a closed
  minute (the daily break or the weekend), i.e. E − t > H minutes.
  - **Non-crossing windows** are the verdict sample.
  - **Crossing windows** are reported separately with every metric and are not
    part of the verdict.
- **Seasonal mass:** P_k(t, H) = Σ profile_k[bucket(u)] over u in W(t, H),
  using fold k's profile.

## Target

- **RV(t, H)** = Σ r² over the non-dropped 1-min log returns of the bars opening
  in W(t, H).
- **Raw variance:** RV is raw (not deseasonalized), in log-return units, so it
  is directly comparable with sigma_ret².
- **Validity:** RV is valid only if at least 90 % of the H minutes have such a
  return, RV > 0, and E <= the period end.
- **Missing minutes:** they lower RV slightly. That affects every candidate
  equally (same rows, same windows).

## Folds (walk-forward, as the vol_features rounds)

- **CONF:**
  - Expanding training window from 2007-01-01.
  - 24 half-year tests, 2009H1 to 2020H2.
  - Eras: 2008–12 (tests 2009H1 to 2012H2) and 2013–20.
- **DEV:**
  - Training from 2021-01-01, with features using 2020 as warm-up.
  - 8 quarterly tests, 2022Q1 to 2023Q4.
- **Fold k, test block [s_k, e_k):**
  - **Fitted state:** `VolParams_k = vol_block.fit(` 1-min bars in
    [period start, s_k − 24h) `)`. Profile, S_hd hour means, L, S_hd and T all
    come from VolParams_k, via `vol_block.transform` over the whole period
    series (causal).
  - **Training rows:** labels t with period start <= t <= s_k − 24h, **and**
    window end E <= s_k. So no training target reaches the test block, even
    across a weekend.
  - **Test rows:** labels t in [s_k, e_k).
  - Everything a candidate fits is fit on fold k's training rows only.

## Candidates (each gives a variance forecast h(t, H) for the window W(t, H))

### (a) Plug-in (baseline)

h_a = exp(L_k(t)) × P_k(t, H).

- This is `sigma_over`'s formula on the window's minutes.
- For non-crossing windows it **equals** `sigma_over(VolParams_k,
  state_at(t), H).sigma_ret²`. Step 1 asserts this on sampled rows.
- No fitted coefficients, no bias correction.

### (b) HAR on L, S_hd, T

- Per fold and H, fit OLS with intercept: y = ln(RV / P_k) on
  [1, L, S_hd, T] over fold k's training rows (crossing and non-crossing).
- **Bias correction back to variance (Duan smearing):**
  c_k = mean of exp(residual) over those training rows.
- **Forecast:** h_b = P_k × exp(β̂·x) × c_k.
- The plug-in is the special case β = (0, 1, 0, 0) with c = 1.

### (c) EWMA on deseasonalized 1-min returns

- **Recursion:** v ← λ·v + (1 − λ)·r²/profile_k[bucket], applied once per
  non-dropped 1-min return, in time order, over the whole loaded series.
  - Initial value: v = 1 at the first return.
  - v(t) is the state after the returns of all bars opening before t.
- **Forecast:** h_c = v(t) × P_k(t, H).
- **λ = 2^(−1/m)**, with half-life m (in returns) from the grid {30, 60, 120,
  240, 480, 960, 1920, 3840, 7680, 15360}.
- **Choosing λ:** per fold and H, take the λ with the lowest mean QLIKE on fold
  k's training rows.
  - Training rows within 60 days of the first loaded bar are excluded
    (initialisation).
  - Ties go to the longer half-life.

## Rows

- **Same rows for every candidate:** per period, H and window type, the test
  rows where L, S_hd and T are finite, RV is valid, and all three forecasts are
  finite and > 0.
- Missing L, S or T doesn't depend on the profile; this is asserted, as in
  round 3.
- Row counts are reported.

## Metrics (per candidate, H, window type; pooled over test rows, also per fold and per era)

- **QLIKE (primary):** RV/h − ln(RV/h) − 1, the mean over rows. Lower is better.
- **MSE on log variance:** mean of (ln RV − ln h)², computed on the same h. A
  variance forecast pays the Jensen gap here, which is why it is secondary.
- **Calibration ratio RV/h with spread:**
  - per row: mean, median, 10th and 90th percentile of RV/h;
  - pooled: Σ RV / Σ h.

## Path metric (reported only, part of the verdict table)

- **Inputs:** σ_px = close_ref × √h, where close_ref is the close of the last
  bar opening before t.
- **Touch rates:** for k in {0.5, 1, 1.5, 2}:
  - P(up) = share of rows where the max high of the window's bars is
    >= close_ref + k·σ_px;
  - P(down) = share of rows where the min low is <= close_ref − k·σ_px.
- **Normal-theory reference:** driftless Brownian motion with variance h,
  continuous monitoring: 2·(1 − Φ(k)) = 0.617, 0.317, 0.134, 0.046.
- Crossing windows include the gap at the reopen in their highs and lows.

## Inference

- **Diebold–Mariano:** each challenger X in {b, c} vs the plug-in, on
  d = QLIKE_X − QLIKE_a.
  - Rows are pooled in time order.
  - Newey–West (Bartlett) long-run variance, lag 8 rows for both H. H = 240
    windows overlap 3 neighbours, and lag 8 matches round 3's 4h choice.
  - One-sided normal p-value for "X better" (`research/vol_features` `dm_test`).
- **Benjamini–Hochberg:** across the 4 p-values (2 horizons × 2 challengers),
  q = 0.05, on CONF non-crossing rows only.
- **Also reported:** DM (b) vs (c), and DM on log-MSE. Neither is part of the
  rule.

## Decision rule (CONF, non-crossing windows; literal, no override)

- **Significant at H:** X beats the plug-in significantly at H if BH rejects
  H0 for (X, H), which requires mean d < 0.
- **Replacement:** X replaces the plug-in **only if** it is significant at
  **both** H = 60 and H = 240.
- **Neither qualifies:** the plug-in stays (ties go to the plug-in).
- **Both qualify:** the one with the lower mean of its two pooled QLIKEs
  (H = 60 and H = 240) is chosen.
- **Outside the rule:** DEV, the eras, crossing windows, log-MSE, calibration
  and the path metric are reported and can't change the verdict.

## Exploratory (declared now)

- HAR coefficients and smearing factors per fold.
- The chosen λ per fold.
- Per-fold QLIKE tables.

Anything added later is labelled exploratory in the report.

## Out of scope

- **Calendar effects** (news, holidays). This is a follow-up once a calendar
  source exists.
- **Choosing k or H**, and **whether crossing trades are allowed** (the agents
  decide that).

## Step 1 checks (before any real-data run)

- **No look-ahead:** perturbing or appending bars at or after t leaves every
  candidate's forecast at t unchanged.
- **Fold separation:**
  - no training row's window reaches its test block;
  - VolParams_k sees only bars before s_k − 24h;
  - λ and β use training rows only.
- **Plug-in identity:** h_a equals `sigma_over` on non-crossing windows (rel.
  1e-9).
- **Helpers:**
  - the EWMA recursion equals a naive loop;
  - the window builder skips closed minutes;
  - the seal asserts fire.
- **Inference check:** on synthetic data with a known variance path, the true
  variance beats the plug-in and DM detects it.

## Step 3 (only if a challenger wins)

- **Refit:** fit the winner on all pre-2024 data, i.e. `XAUUSD.txt` 2007–2023
  (the parquet is the same data from 2020).
- **Save:** coefficients and λ go to versioned JSON with a SHA-256.
- **Propose, don't apply,** the change to `sigma_over`.
- **If no challenger wins:** REPORT.md documents that the plug-in stays.

## Determinism

There is no randomness in the study. Tests use numpy seed 12345.

## Choices made here that the task left open (for the owner's review)

1. **Primary period:** CONF 2007–2020, with DEV reported only. This follows the
   vol_features rounds.
2. **Open minutes** come from `_market_open`, and the window is the next H open
   minutes. On non-crossing windows (the verdict sample) this makes (a)
   exactly `sigma_over`.
3. **Crossing windows** are excluded from the verdict and reported on their own.
4. **(b) target:** the regression target is ln(RV / P), deseasonalized by the
   window's seasonal mass, so the plug-in is nested in it. Bias correction is
   Duan smearing (no normality assumption).
5. **(c) details:** the EWMA updates per return, starts at v = 1, uses the
   half-life grid above, and chooses λ by training QLIKE per fold and H.
6. **Training windows:** HAR and λ are fit on crossing and non-crossing
   training rows together, as one production model would be.
7. **Tests:** Newey–West lag 8 for both horizons; BH q = 0.05, one-sided.
8. **Both challengers qualify:** the lower mean QLIKE over the two horizons
   wins.
