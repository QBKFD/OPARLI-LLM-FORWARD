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

## 3. The counts (step 2) and the rule's choice (step 3): pooled threshold, SUPERSEDED

**Superseded by section 8.** The owner changed Q1_THRESHOLD_KIND to a per-New-York-hour quantile before any outcome
(decision log #31). This section stays as the record that led to the change; its JSON is at commit `bacf336`.

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

## 6. Still OPEN before the Q1 test (first round, SUPERSEDED by section 10)

| id | what is open |
|---|---|
| Q1_NW_LAG_240, Q1_NW_LAG_60 | PROPOSED above |
| Q1_FOLDS | the walk-forward folds of the test: edges, first test block, embargo. Q1_SCORING names folds with an embargo. sigma_compare used a 24 h embargo (CITED `run_sigma.py:40`) and, on CONF, 24 half-year test blocks 2009H1–2020H2 with training expanding from 2007-01-01 (CITED `research/sigma_compare/PREREG.md:83-86`, `run_sigma.py:57`). Here the data start in 2012 and the runs in 2013 |
| Q1_AUG_FIT | what "HAR + the pattern indicator" refits per fold: the pinned VolParams kept or refitted; β and c refitted together with the indicator, or the indicator's coefficient alone on top of the pinned β |
| (readings) | decision log #27 (`Q1_SETUP.md` section 2): owner to confirm |
| (section 4.1) | whether the pooled threshold stays, given the clock-time concentration (no change made) |

## 7. The owner's decisions, second round (2026-10-26)

| id | decision |
|---|---|
| (readings) | Section 2 confirmed: non-overlapping = sharing no candle; no threshold in 2012 (decision log #30) |
| Q1_THRESHOLD_KIND | **Changed before any outcome.** "Small" = score below the q-quantile of scores **at the same hour**, the hour in America/New_York time, fitted on past years only, walk-forward. Reason: with the pooled quantile, the runs concentrated at 19/20/23/00 UTC, a time-of-day artefact around the daily break (section 4.1). Decision log #31 |
| Q1_SMALL_Q, Q1_RUN_LENGTH | Recount on 2012–2020 with this definition and apply the same rule (#32) |
| Q1_NW_LAG_240, Q1_NW_LAG_60 | 8 at both H, APPROVED (#33) |
| Q1_AUG_FIT | Pinned HAR β fixed; per fold, fit only the pattern indicator's coefficient on past data (#35) |
| Q1_FOLDS | Expanding window; test year by year 2014–2020; 24 h embargo. Exact edges PROPOSED in section 9 (#34) |

**How the hour is read** (INFERRED from the definition; the code does exactly this, and it is tested):
- A candle's hour is the clock hour of its **start** in New York time.
- The four candles of a run share one New York hour: a run covers one UTC hour, and New York is a whole number of
  hours from UTC.
- Year Y's threshold for hour h uses only candles that started at hour h in 2012 .. Y−1.

## 8. The recount with the per-hour threshold (step 2) and the rule's choice (step 3)

OBSERVED: `results/q1_run_counts.json` (rewritten; the pooled version is at `bacf336`), recomputed by the notebook.
Counts only: no forward return, RV or outcome.

**Totals 2012–2020**, runs at grid labels (all in 2013–2020; 2012 has no threshold):

| (q, r) | runs: the rule's count | 60-min window doesn't cross a close | 240-min window doesn't cross a close | sliding runs (overlapping) |
|---|---:|---:|---:|---:|
| **0.20, 4** | **349** | 344 | 303 | 1,077 |
| 0.25, 4 | 697 | 687 | 605 | 2,125 |
| 0.20, 3 | 977 | 950 | 873 | 3,288 |
| 0.25, 3 | 1,662 | 1,614 | 1,480 | 5,599 |

**The rule's result: Q1_RUN_LENGTH = 4, Q1_SMALL_Q = 0.20, n = 349 ≥ 300.**
- Recorded as APPROVED-BY-RULE (decision log #37). It supersedes #28 (pooled: 510).
- OBSERVED: every other reading chooses the same pair:
  - non-crossing at H = 240: 303;
  - non-crossing at H = 60: 344;
  - spaced ≥ 240 min: 329;
  - spaced ≥ 60 min: 349.
- Descriptive sensitivity only, never tested: q = 0.25, r = 4 (697 runs).

**Per year, q = 0.20, r = 4** (OBSERVED: runs, then those with a non-crossing 240-min window):

| | 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| runs | 0 | 66 | 54 | 70 | 41 | 28 | 26 | 38 | 26 |
| non-crossing, H = 240 | 0 | 58 | 48 | 56 | 38 | 27 | 24 | 31 | 21 |

**Per New York hour of the run's candles, q = 0.20, r = 4** (OBSERVED: runs, and non-crossing at 240 min in brackets):

| NY hour | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| runs | 10 (10) | 7 (7) | 9 (9) | 19 (19) | 28 (28) | 19 (19) | 19 (19) | 22 (22) | 16 (16) | 11 (11) | 31 (31) | 23 (23) |

| NY hour | 12 | 13 | 14 | 15 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| runs | 25 (25) | 18 (0) | 9 (0) | 14 (0) | 5 (0) | — | 3 (3) | 14 (14) | 12 (12) | 11 (11) | 12 (12) | 12 (12) |

- INFERRED: NY hour 17 is the daily break, so it has no candles.
- The 46 runs at NY hours 13–16 all have a 240-minute window that crosses the 17:00 New York close. That is why 303
  of 349 runs remain non-crossing at H = 240 (INFERRED arithmetic: 349 − 46 = 303).
- The other three (q, r) pairs per NY hour are in the JSON (`per_ny_hour`) and the notebook.

**How clustered, q = 0.20, r = 4** (OBSERVED):
- **By clock time, no longer concentrated.** The busiest NY hour has 31 of 349 runs (8.9 %, INFERRED). Under the
  pooled threshold, 59 % of runs fell in four UTC hours (section 3).
- **In time:**
  - 1.4 % of runs directly follow a run at the previous label; the longest chain is 2 h;
  - 304 trade dates have runs, 86.8 % of them with one run (at most 3);
  - the gap between consecutive runs has a median of 129 h and a 5 % quantile of 3 h.
- **Thresholds** (OBSERVED, q = 0.20): 0.486–1.043 across years × NY hours (184 cells).

## 9. Proposals (PROPOSED): the fold edges and how γ is estimated

**Q1_FOLDS, exact edges** (owner: expanding, test year by year 2014–2020, 24 h embargo):
- **Test block k** = [Y-01-01 00:00, (Y+1)-01-01 00:00) UTC, for Y = 2014, …, 2020: 7 folds.
- **Training rows of fold k:** labels t with 2013-01-01 00:00 ≤ t ≤ s_k − 24 h, **and** a window end E ≤ s_k. This is
  sigma_compare's rule (CITED `research/sigma_compare/PREREG.md:95-97`), so no training window reaches the test block.
- **Why training starts in 2013:**
  - 2012 has no threshold, so no pattern indicator, and γ can't be fitted there (confirmed reading 3).
  - 2012 still provides the 2013 thresholds.
- **A run's indicator** uses the thresholds of its label's year, fitted on earlier years only. A training row in year
  y ≤ Y − 1 therefore uses only years before y.
- **Pooled:** per H, the test rows of the 7 folds concatenated in time order for the DM test. sigma_compare did the
  same: "pooled over test rows" (CITED `PREREG.md:145`).

**Q1_AUG_ESTIMATOR** (new, PROPOSED). How γ is fitted per fold and H, with h_aug = h_HAR × exp(γ × I_run):
- **γ = ln(mean(RV / h_HAR)) over the fold's training rows with I_run = 1.** INFERRED:
  - With h_HAR fixed, this is the γ that minimises training QLIKE. QLIKE is the loss the test scores, and
    sigma_compare chose its λ by training QLIKE (CITED `PREREG.md:130`).
  - It is also option B's ratio, mean RV/h on pattern rows, turned into a coefficient.
- **Rows:** the valid, finite, non-crossing training rows: the population the test scores.
- **Alternatives** (INFERRED):
  - OLS of ln(RV / h_HAR) on I_run, which estimates a log-scale mean and needs its own smearing;
  - also using crossing training rows, as sigma_compare's HAR fit did (CITED `PREREG.md:114-115`).
- INFERRED: d_t is 0 on every row with I_run = 0, so the DM statistic comes from the pattern rows only.

## 10. For the owner before the test (the first outcome analysis)

1. **Fewer pattern rows in the test than in the rule's count.**
   - The rule counts 2012–2020, but the test years are 2014–2020. There, q = 0.20 / r = 4 gives (OBSERVED,
     `per_year`):
     - 283 runs;
     - 279 with a non-crossing 60-min window;
     - **245 with a non-crossing 240-min window** (the primary H).
   - The 66 runs of 2013 are training only.
   - The test's n is at most these, since "valid" (forward coverage) isn't read here (UNVERIFIED how much lower).
   - Nothing was changed: the rule is satisfied as written (349 ≥ 300).
2. **Runs per year fall** from 66–70 (2013, 2015) to 26–28 (2017, 2018, 2020) (OBSERVED).
   - INFERRED: the first folds' γ is fitted on few pattern rows. Fold 2014 trains on 2013 only: at most 58
     non-crossing pattern rows at H = 240.
3. **Still PROPOSED:** Q1_FOLDS (exact edges) and Q1_AUG_ESTIMATOR (above).
4. **Nothing else is OPEN for Q1.** The register's other OPEN items are Q2's.

## 11. The Q1 test: run once (ATTEMPTS = 1)

**Before the run.**
- The owner approved Q1_FOLDS and Q1_AUG_ESTIMATOR as proposed, accepted the sample as it is, and fixed
  Q1_READING_RULE (decision log #38–#39).
- Claude's reading of the Q2 gate's γ (Q1_GATE_GAMMA, PROPOSED, #40):
  - at H = 240;
  - "pooled" and "per test year" mean the estimator applied to the test rows with a run (out of sample);
  - the gate is also reported with the fold γ.
- The code, the register and that reading were committed before the run (`279ab4b`, ATTEMPTS 0).
- `q1_test_run.py` refuses to start unless ATTEMPTS is 0, and increments it before reading any RV. It ran once.
  `results/q1_test.json` is its output; the notebook shows that file and doesn't rerun the test.

**Labels.**
- OBSERVED: `results/q1_test.json`.
- CITED: file:line.
- INFERRED: my reasoning.

### Verdict (Q1_READING_RULE)

| | H = 240 (primary) | H = 60 |
|---|---:|---:|
| test rows: valid, non-crossing, 2014–2020 | 34,022 | 39,415 |
| … with a run (pattern rows) | 225 | 277 |
| QLIKE HAR | 0.19033 | 0.24612 |
| QLIKE HAR + pattern | 0.19022 | 0.24609 |
| DM stat (two-sided; > 0 means HAR + pattern is lower) | +0.827 | +0.319 |
| p raw | 0.408 | 0.749 |
| p BH (H = 240 and 60) | 0.749 | 0.749 |
| significant after BH (q 0.05) | **no** | no |

- **"Pattern adds information": NO** (OBSERVED: H = 240 is not significant after BH).
- **Q2 proceeds: NO.**
  - Under Claude's reading (γ out of sample): pooled γ is 0.242 > 0, and 5 of 7 test years have γ > 0. That meets
    the γ conditions, but the DM condition fails.
  - Under the fold-γ reading: 7 of 7 are > 0, and the DM condition fails the same way.
  - So the PROPOSED reading doesn't change the outcome.
- INFERRED: n is 225 pattern rows at H = 240. The result is "not established", not a demonstrated absence of an
  effect.

### γ per fold and pooled (OBSERVED)

| fold (test year) | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | pooled |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| H = 240, γ fitted on the fold's training rows | 0.070 | 0.223 | 0.330 | 0.248 | 0.224 | 0.217 | 0.226 | — |
| … training pattern rows | 55 | 99 | 150 | 184 | 210 | 231 | 260 | — |
| H = 240, γ in the test year (out of sample) | 0.387 | 0.494 | −0.211 | 0.041 | 0.139 | 0.296 | −0.018 | **0.242** |
| … test pattern rows | 44 | 52 | 33 | 26 | 21 | 29 | 20 | 225 |
| H = 60, γ fitted on the fold's training rows | 0.223 | 0.203 | 0.227 | 0.176 | 0.156 | 0.145 | 0.161 | — |
| H = 60, γ in the test year (out of sample) | 0.179 | 0.263 | −0.088 | −0.025 | 0.032 | 0.272 | −0.188 | 0.119 |
| … test pattern rows | 53 | 67 | 40 | 28 | 26 | 38 | 25 | 277 |

### Descriptives (no test, OBSERVED)

- **Conditional calibration of HAR on the test rows** (Q1_SCORING option B):

  | | mean RV/h | RMS z | QLIKE | n |
  |---|---:|---:|---:|---:|
  | H = 240, pattern rows | 1.274 | 1.082 | 0.3815 | 225 |
  | H = 240, other rows | 0.979 | 0.951 | 0.1891 | 33,797 |
  | H = 60, pattern rows | 1.126 | 1.052 | 0.3273 | 277 |
  | H = 60, other rows | 1.027 | 0.960 | 0.2455 | 39,138 |

- **QLIKE on the pattern rows only:** at H = 240 it falls from 0.3815 (HAR) to 0.3647 (HAR + pattern); at H = 60, from
  0.3273 to 0.3230.
- **q = 0.25 sensitivity** (not chosen by the rule):
  - H = 240: 463 pattern rows, DM +1.095, p 0.274, pooled γ 0.179;
  - H = 60: 559 pattern rows, DM +0.197, p 0.844, pooled γ 0.099.
- **Per New York hour:** 3 to 23 pattern rows per hour (in `q1_test.json` and the notebook). These are too few to
  read. One example: NY 18 has 3 rows, with mean RV/h 4.91 at H = 240.
- **Per year:** the table above. Mean RV/h on the pattern rows at H = 240 is 0.81–1.64 across the test years.

### Reading (INFERRED, not evidence)

- **Direction:** after a run of small candles, realized variance tended to exceed HAR's forecast (γ > 0 in every
  fold, mean RV/h 1.27 on the pattern rows at H = 240). That is the direction the owner's observation suggests.
- **Size versus noise:**
  - The pattern rows are 0.66 % of the test rows, so the change in pooled QLIKE is about 1e−4.
  - The test-year γ has a negative sign in 2016 and 2020.
  - On this sample the pattern's improvement can't be told apart from noise.
- **No further looks.** By the rule, Q1 is answered: the pattern does not add information beyond HAR, and Q2 is not
  pursued. Any further analysis of these data would be a new attempt, for the owner to decide.
