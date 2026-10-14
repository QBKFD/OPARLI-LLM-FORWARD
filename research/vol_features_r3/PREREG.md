# Pre-registration: volatility block, round 3

Committed **before any model result**, and never edited afterwards. Research
only. This is a mechanical fix of round 2 (`research/vol_features_r2/`, commits
08e612a / acfa967). Everything not listed under "Changes" is exactly as in
`research/vol_features_r2/PREREG.md`: the seasonal profile, the gap rule, the
folds, the embargo, the metrics, DM, HAC, η² and the seal.

## Why

Round 2 failed R2 (no clock) because S had η² 0.153. Its exploratory analysis
traced this to a count artefact: S summed squared returns, and the bars next to
the daily break have fewer minutes. The target kept a small clock (η² 0.016) for
the same reason.

## Changes vs round 2

1. **Per-minute means instead of sums.** mRV_n(t) = mean of r_ds² over the
   *available* (non-dropped) 1-min returns in the last n 1H bar rows, as in
   round 2's windows. All n bar rows must exist, and the number of available
   minutes must be at least 50 % of 60·n, otherwise NaN.
   - L = log mRV_24
   - S = log mRV_1 − log mRV_24
   - T = log mRV_24 − log mRV_120
2. **Targets use means too.** y4_ds = log(mean of r_ds² over the available 1-min
   returns realised in (t, t + 240 min]), and y24_ds the same over 1440 minutes.
   The 90 % coverage rule and the window-end seal stay.
3. **W is removed.** The candidate block is MC = (L, S, T). The HAR equivalence
   test compares against a HAR built from the same log mRV terms.
4. **Models:** M1, M1H, MC = (L, S, T), MCH = MC + 23 hour dummies. MC3 and R3
   are dropped.
5. **Rules R1, R2, R4 and R5 are unchanged** (same thresholds), applied to MC =
   (L, S, T). R2 also **reports** (not pass/fail) the η² of all 13 existing
   features and the hour-dummy gain for M1 (R²(M1H) − R²(M1)).
6. **Recommendation:** R1 pass and R2 pass → recommend (L, S, T). Any other
   outcome → report which rule failed and why. No override.

## Data

- **CONFIRMATION:** `XAUUSD.txt` 2007–2020, with the same folds as round 2 (train
  2007–08, tests 2009H1 to 2020H2, eras 2008–12 and 2013–20). **This is the
  second use of this period, for a mechanical fix only.**
- **UNSEEN CONFIRMATION:** none. Before this file was written, the repository was
  searched for a 1-min file of another instrument. The only other 1-min file is
  `GC_FUTURES_1MIN.csv`, which is gold futures and starts 2025-12-16, after the
  seal, so it is not eligible.
- **DEVELOPMENT:** the clean parquet, 2021–2023 (2020 warm-up), same folds as
  round 2.
- **SEALED:** no timestamp >= 2024-01-01 anywhere. CONF stays below 2021-01-01.
  Asserts are as in round 2.

## Rules (judged on CONFIRMATION, also reported for DEV and per era)

- **R1:** R²(MC) >= R²(M1H) − 0.01 on both targets, AND DM does not show M1H
  significantly better (one-sided 5 %) on either.
- **R2:** R²(MCH) − R²(MC) <= 0.01 on both targets, AND the η² of each of L, S
  and T is < 0.05 in every training window.
- **R4 (reported):** pairwise Spearman among L, S and T in every training
  window. Flag |ρ| > 0.5.
- **R5:** for each MC coefficient and target, among the folds with |HAC t| > 2,
  the sign agrees in at least 80 % of them. Never significant = "no signal", not
  a failure.
