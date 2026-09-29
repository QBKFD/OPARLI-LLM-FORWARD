# Pre-registration: volatility block, round 2

Committed **before any model result** was computed, and never edited afterwards.
Later changes go in "Amendments (exploratory)" in REPORT.md. Research only.
Nothing in `regime_classifier/` or `backend/` is changed.

## Question

Round 1 (`research/vol_features/`, commit e01529d) found that the 13 existing 1H
volatility features beat 3 proposed features at 4h only through time-of-day
information, and that HAR (log RV from 1-min returns) beats all 13 at both
horizons. For a regime classifier, time-of-day content is contamination. Round 2
tests a deseasonalized, 1-min-based block (C) and confirms it on data from a
different era.

## Data

- **Development (DEV):** `database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet`.
  Bars 2021-01-01 to 2023-12-31 are analysed, with 2020 as warm-up. Round 1
  already saw these years, so every DEV result is labelled DEVELOPMENT.
- **Confirmation (CONF, PRIMARY):** `database/ohlcv_data/XAUUSD.txt`, restricted
  to the usable years up to and including 2020 under the task's rule, applied in
  `research/vol_features/DATA_AUDIT_LONG.md` (computed before this file; the
  audit contains no model results). **Usable years: 2007–2020 (14 years).** The
  file is UTC, so no shift is applied. For 2020–2023 it matches the clean parquet
  close for close.
- **Seal:** no timestamp >= 2024-01-01 anywhere. The CONF loader also enforces
  < 2021-01-01, and every CONF target window must end <= 2021-01-01. Asserts run
  on the loaders, bars, targets and model samples.
- 1-min timestamps are bar open times. 1H bars are labelled by close time t and
  hold the 1-min bars with open in [t − 60 min, t).
- **Gaps:** a 1-min return is dropped if its bar follows the previous 1-min bar
  by more than 5 minutes. No interpolation.

## Seasonal profile (per fold, training window only)

- Buckets: (US-DST flag, 5-minute bucket of the UTC day), 2 × 288 = 576. The
  DST flag is whether New York observes DST at the 1-min bar's UTC open time.
  The bucket comes from the 1-min bar's open time.
- profile[b] = mean of squared (non-dropped) 1-min log returns whose 1-min bars
  open in [analysis start, test start − 24h) and fall in bucket b. That is the
  same span as the fold's training rows. Buckets with fewer than 50 such returns
  get the median of the other buckets. Then the profile is floored at its 1st
  percentile across the 576 buckets.
- r_ds = r / sqrt(profile[bucket]). Each fold uses its own profile for all of its
  features and targets, both training and test rows.

## Candidate block C (at each 1H bar close t)

- dsRV_n(t) = sum of r_ds² over the 1-min bars inside the last n 1H bar rows
  (trading time, as round 1's HAR), for n = 1, 24, 120. All n bars must be
  present. NaN if the sum is 0.
- **L** = log dsRV_24. **S** = log dsRV_1 − log dsRV_24. **T** = log dsRV_24 −
  log dsRV_120.
- **W** = wick60 from round 1: Σ₆₀(H − L − |C − O|) / Σ₆₀(H − L) over 1H bars,
  all 60 bars required, NaN if the denominator is 0.
- L, S and T are an invertible linear map of the 3 deseasonalized HAR log-RVs, so
  they give an identical OLS fit.

## Targets (deseasonalized, strictly after t)

- y4_ds = log Σ r_ds² over 1-min bars opening in [t, t + 240 min), i.e. returns
  realised in (t, t + 240]. y24_ds is the same with 1440 minutes.
- NaN if fewer than 90 % of the window's minutes have a non-dropped return, if
  the sum is 0, or if the window ends after the period's seal.

## Models

OLS with intercept on z-scored regressors. Mean and std come from the training
rows only.

- **M1:** the 13 existing volatility features, computed with the repo's own
  functions on the 1H bars (as round 1: `atr, atr_ratio, atr_percentile,
  bb_width, bb_width_percentile, parkinson_vol, rv_15, rv_60, rv_240,
  rv_ratio_short_long, rv_ratio_mid_long, rv_ratio_short_mid, rv_adjusted`).
- **M1H:** M1 + 23 UTC hour-of-day dummies (the bar's label hour; hour 0 is the
  baseline).
- **MC:** L, S, T, W.  **MC3:** L, S, T.  **MCH:** MC + the 23 hour dummies.
- **Same rows for every model:** per period and target, rows where every
  regressor and the target are non-missing. Missingness doesn't depend on the
  profile, which the run asserts. Row counts are reported.

## Folds

- **DEV:** training rows are 2021-01-01 <= t <= test start − 24h. Tests are the 8
  quarters 2022Q1 to 2023Q4. The first training window is 2021.
- **CONF:** training rows are 2007-01-01 <= t <= test start − 24h. The first 24
  months (2007–2008) train only. Tests are 24 half-years, 2009H1 to 2020H2.
- **Eras (CONF):** 2001–2007 (training only, no test blocks), 2008–2012 (tests
  2009H1 to 2012H2), 2013–2020 (tests 2013H1 to 2020H2).

## Metrics and inference

- **Pooled OOS R²** = 1 − Σ(y − ŷ)² / Σ(y − ȳ_train,k)². Also per fold and per era.
- **QLIKE** on the variance scale: h = exp(ŷ + s²/2), with s² the training
  residual variance. QLIKE = e^y/h − ln(e^y/h) − 1.
- **DM** on the pooled squared-error differential d = e²_A − e²_B, with rows in
  time order. Newey–West Bartlett lag 8 (y4) and 48 (y24) rows. One-sided,
  normal p-value.
- **HAC t (for R5):** OLS coefficient t-statistics with a Newey–West (Bartlett)
  covariance, same lags, computed on the training rows of each fold.
- **η² (clock):** share of a feature's variance explained by the UTC hour of the
  bar label, computed on each training window.

## Decision rules (judged on CONF; also reported for DEV and per era)

- **R1 replacement:** R²(MC) >= R²(M1H) − 0.01 on BOTH targets, AND DM does not
  show M1H significantly better (one-sided p < 0.05) on either target.
- **R2 no clock:** R²(MCH) − R²(MC) <= 0.01 on both targets, AND the η² of each
  of L, S, T, W is < 0.05 in every training window.
- **R3 wick:** W is kept if MC beats MC3 significantly (one-sided p < 0.05) on at
  least one target, and MC3 does not beat MC significantly on the other.
  Otherwise the block is L, S, T.
- **R4 distinctness (reported):** pairwise Spearman among L, S, T, W in every
  training window. Flag any pair with |ρ| > 0.5 in any window.
- **R5 stability (on MC):** for each coefficient and target, among the folds
  where |HAC t| > 2, the sign agrees in at least 80 % of them. A coefficient that
  is never significant is reported as "no signal", which is not a failure. R5
  passes if no coefficient fails.
- **Recommended block:** if R1 and R2 pass, use C with W decided by R3 (L, S, T,
  plus W if R3 keeps it). If R1 fails, keep the 13 existing features (status
  quo). If R1 passes and R2 fails, the block is not recommended (clock
  contamination) and the status quo stays.

## Determinism

No randomness in the study. Tests use numpy seed 12345.
