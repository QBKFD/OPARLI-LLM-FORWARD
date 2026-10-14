# Volatility block: decision

Status: **locked for the regime model**, as an engineering decision. The
confirmed and unconfirmed parts are kept apart below.

## Confirmed (pre-registered, out of era)

Rounds 2 and 3 (`research/vol_features_r2/`, `research/vol_features_r3/`)
confirmed on XAUUSD 2007–2020, which the development work never used:

- **L, S and T beat the 13 existing volatility features** at forecasting
  deseasonalized future variance. Out-of-sample R², round 3: **0.73 vs 0.52**
  (4h) and **0.80 vs 0.55** (24h), against the 13 features *with* hour dummies
  added. L, S and T win **24/24 half-years** on both horizons (DM +28.2 / +12.5;
  the 13 features are never better).
- **Coefficient signs are stable:** L +, S +, T − in every fold, significant
  (|HAC t| > 2) in 24/24 folds for both targets.
- The block is scale-free. It held up through the 2011 gold spike, where the
  dollar-scaled `atr` family broke down (4h R² −0.75).

## Not confirmed

- **R2 (no clock) failed in round 3.** Hour dummies still added **0.0119** R² at 4h
  (limit 0.01). That comes largely from the *target's* remaining log-scale clock:
  on the level scale the clock is about 0 (η² ≤ 0.007), on the log scale it is
  0.015–0.017. Hour-demeaning S alone left the gain in place (round 2 E3).
- **S keeps η² 0.12 in 2021–2023** (development), which is above 0.05.

## Engineering decision (not tested by a pre-registered study)

**Use L, S_hd and T**, where S_hd = S minus the training-window mean of S for
each UTC hour. This removes S's hour-of-day mean by construction (η² = 0 on the
training window). The out-of-sample effect of S_hd was only measured
exploratorily in round 2 (R² +0.002, significantly better than S). **Final
validation happens with the whole regime system on the sealed 2024+ data.**
Implementation: `research/vol_block/vol_block.py` (`VolBlock.fit` /
`VolBlock.transform`), definitions exactly as in round 3.

## Lesson for later studies

R2 is redesigned. The single test "R²(block + hours) − R²(block) ≤ 0.01" mixed
feature clock and target clock. Future studies use:
1. **Per-feature η²** (hour-of-day share of each feature's variance, maximum over
   training windows), with a threshold per feature.
2. **A relative dummy test:** the hour-dummy gain of the new block must not
   exceed that of the old block, so both are judged against the same target.
