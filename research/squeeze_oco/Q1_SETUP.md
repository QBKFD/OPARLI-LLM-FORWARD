# Q1 setup: the owner's decisions, Claude's readings, then the counts

**Status:** setup only. No outcome, no forward return, no RV is read. `ATTEMPTS.json` stays at 0.

## 1. The owner's Q1 decisions (2026-10-26)

| id | decision |
|---|---|
| Q1_TIMEFRAME | 15 min |
| Q1_SMALL_DEF | option B (range / σ_tf, σ_tf from the model's own seasonal mass for that slot), range = high − low |
| Q1_THRESHOLD_KIND | quantile (the smallest q share of scores), fitted on training years only, walk-forward; never on the year being scored |
| Q1_SMALL_Q, Q1_RUN_LENGTH | by a rule decided before any counts: r = 4; q = the strictest of 0.20 and 0.25 giving at least 300 non-overlapping runs in 2012–2020. If neither does, r = 3 with the same q rule. If that also fails, stop and report: too little data. The non-chosen q values are descriptive sensitivity only, never tested. Runs end at an hourly label and cover the hour before it |
| Q1_H | 240 primary, 60 secondary, BH across the two |
| Q1_SCORING | A (sigma_compare's method: QLIKE of HAR vs HAR + pattern indicator, walk-forward folds with embargo, Diebold–Mariano, BH). Option B (conditional calibration on pattern rows) descriptive only, no test. Option C not used |

## 2. Claude's readings, written down before any count (owner to confirm)

This file and `q1.py` are committed before `q1_runs.py` is run, so the order is on record.

1. **σ_tf (option B).** The state is the HAR state of the latest hourly label at or before the candle's start, by
   `vol_block.state_at`'s rule (the anchor PROPOSALS.md gave for option A).
   - σ_tf² = var_60 × P_candle / P_60, where:
     - var_60 is HAR's variance over the next 60 open minutes from the candle's start (`sigma_over`, H = 60);
     - P_60 is the model's seasonal mass over those minutes;
     - P_candle is the same profile summed over the candle's open minutes.
   - The level factor cancels, so σ_tf² = P_candle × exp(β_60 · [1, L, S_hd, T]) × c_60.
   - The score is range / (close_ref × σ_tf), with close_ref the close of the last bar before the candle's start
     (`vol_block`'s close_ref).
   - Tested: `test_q1.py`, including equality with `state_at` + `sigma_over` on the pinned model.
2. **What the threshold is fitted on.**
   - The candles: all complete 15-min candles with a score, starting in the training years.
   - The threshold: the q-quantile, numpy's default (linear interpolation). "Small" means score ≤ threshold.
   - The window: walk-forward by calendar year and expanding from DEV_START, as run_sigma's folds are
     (`research/sigma_compare/run_sigma.py:227`, train = everything before the test start minus the embargo).
   - Year Y's threshold uses 2012 .. Y−1.
3. **Consequence of 2 (INFERRED): 2012 has no threshold**, so it contributes no runs. The runs counted are those in
   2013–2020, inside the rule's span 2012–2020.
4. **The threshold a run uses** is that of its label's year.
5. **"Non-overlapping" means sharing no candle.**
   - Runs end at hourly labels and r × 15 ≤ 60, so runs at different labels never share a candle. Every counted
     run is non-overlapping by construction.
   - "Overlapping", reported for comparison only: sliding runs ending at any 15-min boundary, which do share
     candles.
   - Overlap of the forward H-windows is a different question. It is reported under clustering: a greedy count of
     runs spaced at least 240 or 60 minutes apart. If the rule would choose differently under that reading, I
     report it and don't apply it.
6. **r = 3 covers the last 45 minutes of the hour** before the label: the three candles ending at it.
7. **The rule counts runs at the labels of EVAL_GRID**: labels with a state, as `stage1.state_table` gives them,
   since Q1 is scored there.
   - Also reported: the subsets whose 60- or 240-minute window doesn't cross a close. That is a calendar fact; no
     return is read.
   - Not reported: the "valid" subset, because validity uses the forward window's RV (> 0) and its coverage.
