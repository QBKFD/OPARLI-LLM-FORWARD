# Pre-registration: trend feature study

Committed **before any model result**, and never edited afterwards. Research
only. Nothing in `regime_classifier/` or `backend/` changes.

## Question

A regime model needs *direction-neutral* trend strength. Do trend features
predict whether price will move cleanly (a straight line) or chop over the next
4h / 24h?

## Prior

The R² values are expected to be **small (well below 0.05)**. Future directional
efficiency is much harder to predict than volatility. A small but significant
and stable effect is a meaningful result, and a clean "no information" is also a
valid result.

## Data and sampling

- **CONFIRMATION (primary):** `database/ohlcv_data/XAUUSD.txt`, 2007–2020 (UTC;
  usable under `research/vol_features/DATA_AUDIT_LONG.md`). This is the **first
  use of this period for the trend question**, though the volatility rounds used
  it. Folds as in vol round 2: training rows 2007-01-01 <= t <= test start − 24h
  (expanding), 24 half-year tests from 2009H1 to 2020H2. Eras: 2008–12 and
  2013–20.
- **DEVELOPMENT:** the clean parquet 2021–2023 (2020 warm-up), 8 quarterly
  tests 2022Q1–2023Q4, training from 2021-01-01, 24h embargo.
- **SEALED:** no timestamp >= 2024-01-01 anywhere. CONF stays below 2021-01-01.
  Asserts run on the loaders, bars, frames, target windows and model samples, and
  a test checks that the assert fires.
- **Decision rows:** 1H bars labelled by close time t (1-min bars opening in
  [t − 1h, t)).
- **5-min returns:** 5-min bucket [b, b + 5 min) of 1-min open times. P_b =
  close of the last 1-min bar in the bucket. R_b = ln(P_b / P_{b−5}) is valid only
  if bucket b − 5 has data and no 1-min bar in bucket b opens more than 5 minutes
  after the previous 1-min bar (gap-excluded, no interpolation). The same rule
  applies to features and targets.

## Candidate block MT (trend strength in units of its own noise)

- Z_n(t) = |Σ R_b| / sqrt(Σ R_b²) over the valid returns of the buckets inside
  the last n 1H bar rows (trading time, as the vol block), n = 4, 24, 120. All n
  rows must exist, and the valid returns must number at least 90 % of 12·n,
  otherwise NaN. NaN if Σ R² = 0. Gap-excluded returns are left out of the
  numerator and the denominator alike.

## Benchmark block M1+ (8 features)

`adx, adx_percentile, di_bias, ema_slope, price_vwap_dist`, computed with the
repo's own `RegimeFeatureEngine` methods on the 1H OHLC bars (no volume column,
so the "VWAP" is equal-weighted, as for IBKR spot and as in vol round 1), plus
`|di_bias|, |ema_slope|, |price_vwap_dist|`.

## Control block MV

L, S_hd and T from `research/vol_block/vol_block.py`. `VolBlock.fit` runs on each
fold's training 1-min data (period start to test start − 24h), and `transform`
on the full 1-min series.

## Targets (strictly after t)

- E4(t) = |Σ R_b| / Σ |R_b| over the valid returns of buckets with b in
  [t, t + 240 min), i.e. realised in (t, t + 240]. E24 is the same with 1440
  minutes. The value lies in [0, 1].
- NaN if the valid returns number fewer than 90 % of h / 5, if Σ|R| = 0, or if
  the window ends after the period's seal.

## Models

OLS with intercept on z-scored regressors, scaler fit on the training rows.
**MV, M1+, MT, MV+M1+, MV+MT, M1+H, MTH, MV+MTH** (H = + 23 UTC hour dummies).
All models use the same rows (the intersection of every regressor and the
target), per period and target. Row counts are reported.

## Metrics and inference

- Pooled OOS R² against each fold's training mean, and MAE. Pooled, per era and
  per fold.
- DM on squared errors, pooled in time order, Newey–West lag 8 (E4) / 48 (E24),
  one-sided normal p.
- HAC t-statistics (Newey–West, same lags) on each fold's training rows.
- η² = the hour-of-day share of a feature's variance, on each training window.

## Decision rules (judged on CONFIRMATION; also reported per era and for DEV)

- **R0 information:** MV+MT beats MV (DM p < 0.05) on at least one target. The
  same is reported for MV+M1+ vs MV. If neither block passes R0, the
  recommendation is "no trend block in the regime model".
- **R1 replacement:** R²(MV+MT) >= R²(MV+M1+) − 0.005 on both targets, AND DM
  does not show MV+M1+ significantly better (p < 0.05) on either.
- **R2 clock:** (a) η² < 0.05 for each of Z4, Z24, Z120 (maximum over training
  windows); AND (b) R²(MTH) − R²(MT) <= R²(M1+H) − R²(M1+) on both targets.
- **R4 distinctness (report only):** pairwise Spearman among Z4, Z24, Z120, and
  between each Z and L, S_hd, T, per training window. Flag |ρ| > 0.5.
- **R5 stability:** on the MV+MT model, for each coefficient and target, among the
  folds with |HAC t| > 2, the sign agrees in >= 80 % of them. Never significant
  = "no signal".
- **Mapping:** R0 (MT) + R1 + R2 pass → recommend MT, dropping any MT feature
  that is "no signal" in R5 on both targets. A feature whose R5 status is FAIL
  (unstable sign) is dropped too. R0 fails for both blocks → recommend no trend
  block. Anything else → report which rule failed, no override.

## Exploratory (declared now, not a decision rule)

Does signed direction predict the sign of the next 24h return? One test: in
CONF, walk-forward OLS of sign(next-24h log return) on [di_bias, signed Z24],
same folds. Pooled OOS hit rate of sign(forecast) against 50 %, with a
Newey–West (lag 48) t-test on the hit indicator.

## Determinism

No randomness in the study. Tests use numpy seed 12345.
