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

## 3. The counts (step 2) and the rule's choice (step 3)

**Labels:**
- **OBSERVED:** `results/q1_run_counts.json`, written by `q1_runs.py`, which `01_vol_state.ipynb` "Q1 setup" recomputes.
- **CITED:** file:line.
- **INFERRED:** my arithmetic or reasoning.

The span is the dev period 2012–2020. There are 52,929 grid labels with a state, 209,979 complete 15-min candles and
207,114 scored candles (OBSERVED). 23 candles contain a minute the market calendar counts as closed: their seasonal
mass leaves that minute out (OBSERVED).

**Runs at grid labels, totals 2012–2020** (OBSERVED; all of them in 2013–2020, see reading 3):

| (q, r) | runs: the rule's count | … window doesn't cross a close, H = 60 | … H = 240 | sliding runs (overlapping) | spaced ≥ 240 min apart (greedy) |
|---|---:|---:|---:|---:|---:|
| **0.20, 4** | **510** | 497 | 328 | 1,385 | 494 |
| 0.25, 4 | 919 | 890 | 612 | 2,519 | 867 |
| 0.20, 3 | 1,180 | 1,127 | 904 | 3,843 | 1,094 |
| 0.25, 3 | 1,897 | 1,804 | 1,448 | 6,295 | 1,696 |

**Per year, q = 0.20, r = 4** (OBSERVED): runs, then their non-crossing count at H = 240.

| | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| runs | 0 | 74 | 67 | 104 | 60 | 48 | 60 | 61 | 36 |
| non-crossing, H = 240 | 0 | 46 | 33 | 72 | 45 | 38 | 34 | 36 | 24 |

The per-year tables for the other three pairs are in the JSON (`per_year`) and the notebook.

**Thresholds** (OBSERVED: the score below which a candle counts as small, by year scored):
- q = 0.20: 0.834–0.865;
- q = 0.25: 0.901–0.933.

They are stable across the years. The yearly median score is 1.204–1.289 (OBSERVED, `score_quantiles_per_year`).

**The rule's result: Q1_RUN_LENGTH = 4, Q1_SMALL_Q = 0.20, n = 510 ≥ 300.**
- Recorded in the register as APPROVED-BY-RULE (decision log #28).
- The other readings of "non-overlapping" choose the same pair (OBSERVED, `for_comparison_only`):
  - non-crossing at H = 240: 328;
  - non-crossing at H = 60: 497;
  - spaced ≥ 240 min: 494;
  - spaced ≥ 60 min: 510.
- Descriptive sensitivity only, never tested: q = 0.25, r = 4 (919 runs).

**How clustered the runs are, q = 0.20, r = 4** (OBSERVED):
- Little in time:
  - only 0.79 % of runs directly follow a run at the previous label, and the longest chain is 2 hours;
  - the 510 runs fall on 445 trade dates, 87.2 % of which have one run (at most 4);
  - the gap between consecutive runs has a median of 89 h, a 5 % quantile of 4.4 h and a 1 % quantile of 2 h.
- **A lot by clock time:** 303 of the 510 runs (59 %, INFERRED arithmetic) have their label at 19, 20, 23 or 00 UTC
  (120, 44, 68 and 71 runs; OBSERVED `runs_per_utc_hour_of_label`).
- INFERRED: the independence figure in PROPOSALS.md (84 for 2012–2020) undercounts by about 6×, because small
  candles cluster within the hour.

## 4. Things the owner should see before the test (OBSERVED, with INFERRED reading)

1. **The score isn't flat across the day.**
   - OBSERVED (`score_quantiles_per_utc_hour_of_start`): the median score is 1.14–1.40 in most UTC hours, but
     1.006 for candles starting at 18:xx, 0.894 at 22:xx and 0.990 at 23:xx.
   - Those hours feed the run labels at 19, 23 and 00 UTC.
   - INFERRED: under option B, a run partly selects clock times: the hour after the daily reopen, and 18:00 UTC.
   - Two causes are possible; both are UNVERIFIED:
     - the pinned profile's mean r² in those buckets is high relative to a typical candle's range (a few large
       returns at the reopen or around the COMEX settlement);
     - the profile was fitted on 2021–2023 and applied to 2012–2020.
   - HAR's P(t, H) carries the same profile, so the test still asks "beyond HAR". But the pattern indicator is
     partly an hour-of-day indicator.
   - Nothing was changed: Q1_THRESHOLD_KIND (pooled quantile) is approved as is. The owner may want to decide whether
     that stays.
2. **H = 240 has few non-crossing pattern rows.**
   - 328 of the 510 runs (64 %) have a 240-minute window that doesn't cross a close (OBSERVED). INFERRED: the runs
     at 19 and 20 UTC sit just before the daily break.
   - The primary test scores valid, non-crossing rows only. Validity needs the forward window's coverage, which
     isn't read here, so the test's n at H = 240 is ≤ 328 (UNVERIFIED how much lower).
   - At H = 60 it is ≤ 497.
3. **2012 is training only** (reading 3). It gives the 2013 threshold and no runs.

## 5. Proposal: the Newey–West lags (step 4, PROPOSED)

The DM test runs on the loss differential d_t of HAR vs HAR + the pattern indicator, over the valid, non-crossing
grid rows in time order. Lags count rows (CITED `research/vol_features/DECISIONS.md:98-99`).

**Window overlap.**
- CITED `research/sigma_compare/PREREG.md:171-172`: "H = 240 windows overlap 3 neighbours, and lag 8 matches
  round 3's 4h choice".
- INFERRED: at H = 60, windows from consecutive hourly labels don't overlap (0 neighbours). Any serial correlation
  in d comes from volatility clustering, not from overlap.

**Precedents.**
- sigma_compare: lag 8 for both H (CITED `run_sigma.py:41`, `PREREG.md:171`, `:250`). This is the method
  Q1_SCORING approved.
- vol_features: 8 rows for the 4 h target and 48 for the 24 h target (CITED `research/vol_features/PREREG.md:115-116`).
  INFERRED: that is about 2 × the window in hours.

**Specific to Q1 (INFERRED).**
- If Q1_AUG_FIT adds only the indicator's coefficient on top of the pinned β, d_t differs from 0 only on the
  ≤ 328 / 497 pattern rows.
- Those are rarely neighbours: the 5 % quantile of the gap is 4.4 h, and 0.79 % are adjacent (OBSERVED above).
- If β and c are refitted, d_t is non-zero on every row.
- Either way, the overlap at H = 240 is 3 rows.

| id | proposed | reasoning (INFERRED) | alternative |
|---|---:|---|---|
| Q1_NW_LAG_240 | **8** | ≥ the 3 overlapping neighbours, with room for volatility clustering; equal to the approved method's value and to vol_features' 4 h choice | none from the repo |
| Q1_NW_LAG_60 | **8** | no overlap, so the lag only covers clustering; equal to sigma_compare's value for both H. With about 50 thousand rows (OBSERVED: 50,516 valid non-crossing rows at H = 60 in Stage 1 step 4; the test blocks hold fewer), a longer Bartlett window costs little (INFERRED) | 2 (vol_features' about 2 × the window in hours) |

The autocorrelation of d itself would show which lag fits. It is an outcome, so it isn't looked at here. The owner
could approve reporting it as a descriptive diagnostic after the test, without changing the lag.

## 6. Still OPEN before the Q1 test (the first outcome analysis)

| id | what is open |
|---|---|
| Q1_NW_LAG_240, Q1_NW_LAG_60 | PROPOSED above |
| Q1_FOLDS | the walk-forward folds of the test: edges, first test block, embargo. Q1_SCORING names folds with an embargo. sigma_compare used a 24 h embargo (CITED `run_sigma.py:40`) and, on CONF, 24 half-year test blocks 2009H1–2020H2 with training expanding from 2007-01-01 (CITED `research/sigma_compare/PREREG.md:83-86`, `run_sigma.py:57`). Here the data start in 2012 and the runs in 2013 |
| Q1_AUG_FIT | what "HAR + the pattern indicator" refits per fold: the pinned VolParams kept or refitted; β and c refitted together with the indicator, or the indicator's coefficient alone on top of the pinned β |
| (readings) | decision log #27 (`Q1_SETUP.md` section 2): owner to confirm |
| (section 4.1) | whether the pooled threshold stays, given the clock-time concentration (no change made) |
