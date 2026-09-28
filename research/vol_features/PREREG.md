# Pre-registration: volatility feature reduction for the regime classifier

Written and committed **before any result was computed** (commit message
`prereg: vol feature reduction`). This file is never edited afterwards. Later
changes go in "Amendments (exploratory)" in REPORT.md.

Research only. Nothing in `regime_classifier/` or `backend/` is modified.

## Question

`regime_classifier/regime_features.py` has 13 volatility features. They are
highly redundant, and after standardisation each gets an equal vote in the jump
model's distance, so volatility dominates by feature count. Do 3 pre-chosen
features, one per concept (level, term structure, shape), carry the same
information?

## Data and sealing

- `database/ohlcv_data/XAUUSD_1min_UTC_clean.parquet` (1-min XAUUSD, UTC; the
  timestamp is the 1-min bar's open time).
- Only rows with timestamp < 2024-01-01 00:00 UTC are loaded. 2020 is warm-up
  for rolling windows only. Analysis period: bars labelled 2021-01-01 to
  2023-12-31 (label < 2024-01-01).
- An assert fails if any row with timestamp >= 2024-01-01 reaches the analysis
  (applied to the loaded 1-min frame, the 1H bars, and every model frame).

## Bars and gap exclusion

- 1H bars resampled from 1-min: a bar labelled t (its close time, UTC) holds the
  1-min bars with open time in [t - 60 min, t). O = first open, H = max high,
  L = min low, C = last close. Hours with no 1-min data produce no bar.
- A 1-min bar is **gap-flagged** if its timestamp is more than 5 minutes after
  the previous 1-min timestamp (the first row is flagged).
- 1-min log return r_i = ln(C_i / C_{i-1}), realised at open_i + 1 min. Dropped
  (not interpolated) if bar i is gap-flagged.
- 1H log return r_t = ln(C_t / C_{t-1h}). Dropped if the 1H bar t contains any
  gap-flagged 1-min bar or the previous bar is not at t - 1h.

## Pre-registered reduced set (3 features)

All windows count 1H bar rows (trading time).

- **LEVEL** `log_gk60 = log(mean over the last 60 1H bars of sigma2_GK)`, where
  `sigma2_GK = 0.5*(ln(H/L))^2 - (2*ln2 - 1)*(ln(C/O))^2` per bar, clipped
  below at 1e-12 before the mean. Needs all 60 bars.
- **TERM STRUCTURE** `log_rv_ratio = log(rv_15 / rv_240)`, where
  `rv_n = sqrt(sum of squared 1H log returns over the last n bars)`, matching the
  existing definition: dropped returns are skipped, min_periods =
  max(5, n // 2) non-missing returns. NaN if either rv is 0 or missing.
- **SHAPE** `wick60 = sum_{last 60}(H - L - |C - O|) / sum_{last 60}(H - L)`.
  Needs all 60 bars. NaN if the denominator is 0.

## Comparison set (M1)

All 13 volatility features in the "Volatility features" block of
`RegimeFeatureEngine.compute()`: `atr, atr_ratio, atr_percentile, bb_width,
bb_width_percentile, parkinson_vol, rv_15, rv_60, rv_240, rv_ratio_short_long,
rv_ratio_mid_long, rv_ratio_short_mid, rv_adjusted`. They are computed with the
repository's own functions (imported without modifying the package), default
`RegimeConfig`, applied to the 1H OHLC bars exactly as the classifier receives
them. The existing code has no gap handling, and it is evaluated as it is.
(The task text says "~11" and lists 12; the code block also contains
`atr_percentile`, so all 13 are used.)

## Targets (from 1-min data, strictly after bar t)

- `y4(t) = log(sum of r_i^2 for 1-min bars with open time in [t, t + 240 min))`,
  that is returns realised in (t, t + 240 min].
- `y24(t)`: the same with 1440 minutes.
- Gap-flagged returns are dropped. The target is NaN if fewer than 90 % of the
  window's minutes have a non-dropped return, or if the sum is 0. (This removes
  windows spanning the weekend, and for y4 also the daily break. A 24h window
  always contains the ~60-minute daily break, which is 95.8 % coverage, so y24
  keeps those.)

## Models

OLS with intercept on z-scored features. Mean and std (ddof = 0) come from each
training window only.

- **M0 HAR** (Corsi 2009 style): log RV over the last 1, 24 and 120 1H bars,
  where RV is the sum of squared 1-min returns in those bars (gap-dropped). Needs
  all bars present. NaN if the sum is 0.
- **M1**: the 13 existing volatility features.
- **M2**: the 3 pre-registered features.
- **M3**: HAR + the 3 features.
- **M4**: HAR + the 13 features.

**Common sample:** for each target, the rows where every feature of every model
and the target are non-missing (the intersection). All models use the same rows.
Row counts are reported.

## Walk-forward

- Test blocks: calendar quarters 2022Q1 to 2023Q4 (8 folds), rows with t in
  [quarter start, quarter end).
- Training window k (expanding): rows with 2021-01-01 <= t <= quarter start -
  24h. That is a 24h embargo for both targets. Fold 1's training window is
  2021-01-01 to 2021-12-31 00:00.
- No hyperparameters, so no inner validation.

## Metrics

- **Pooled OOS R²** = 1 - Σ(y - ŷ)² / Σ(y - ȳ_train,k)² over all test rows,
  where ȳ_train,k is fold k's training-mean forecast. Per-fold values too.
- **QLIKE** on the variance scale: RV = exp(y) and forecast
  h = exp(ŷ + s²/2), with s² the training residual variance (ddof = p + 1,
  a log-normal bias correction). QLIKE = RV/h - ln(RV/h) - 1, averaged. The
  training-mean benchmark uses the training variance of y as s². Pooled and per
  fold.

## Inference

Diebold–Mariano test on pooled squared-error loss differentials (log scale),
with test rows concatenated in time order. d_t = e²_A,t - e²_B,t. Newey–West
Bartlett long-run variance with lag 8 (y4) and 48 (y24) rows. DM = mean(d) /
sqrt(LRV / T), with a standard-normal p-value. One-sided in the direction each
rule specifies.

## Pre-registered decision rules

- **D1 (redundancy):** M2 is ACCEPTED as a replacement for M1 if pooled OOS
  R²(M2) >= R²(M1) - 0.01 for BOTH targets, AND the DM test does not show M1
  significantly better at 5 % one-sided (H1: E[e²_M1 - e²_M2] < 0) for either
  target.
- **D2 (distinctness):** on the initial training window (2021), the pairwise
  |Spearman ρ| among the 3 features must be < 0.5. Any pair above 0.5 flags the
  SHAPE feature for removal (keep LEVEL and TERM). Rows: all 16 features
  non-missing.
- **D3 (added value over HAR):** report whether M3 beats M0 (DM one-sided, 5 %,
  per target). Informative, not pass/fail.
- **D4 (stability):** D2 holds in at least 7 of the 8 training windows, AND for
  each target the sign of each of the 3 M2 coefficients is the same in at least
  7 of 8 folds. It passes only if all of these hold.

## Step 1: correlation structure

On each training window and on the full 2021–2023 period, using rows where all
16 features (13 existing + 3 new) are non-missing:

- Spearman correlation matrix.
- Average-linkage hierarchical clustering on 1 - |ρ| (scipy), flat clusters at
  distance 0.2 (|ρ| > 0.8).
- Heatmap ordered by the clustering, plus a dendrogram (PNG).
- Whether the clusters match level / term structure / shape. A feature whose
  |ρ| with every other feature is < 0.5 counts as "correlated with nothing" and
  is investigated.

## Step 3: stability

Cluster co-membership frequency across the 8 training windows, and D2 and D4
per window.

## Determinism

No randomness in the study itself. The truncation test uses numpy seed 12345.
