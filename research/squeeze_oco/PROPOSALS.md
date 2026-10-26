# Q1: options for the OPEN items (for the owner's decision)

**Status:** proposals only. Q1 has not been run. Nothing here looks at an outcome or a forward return, or uses
σ. Every register entry named here stays OPEN (or PROPOSED, for Q1_SCORING) until the owner decides.

**Labels:**
- **OBSERVED:** computed here. The event counts are in `results/q1_event_counts.json`, written by `q1_counts.py`:
  counts of candles and hourly labels per year on the dev period, with no σ and no price.
- **CITED:** from a repo file, with its line.
- **INFERRED:** my reasoning or arithmetic.
- **UNVERIFIED:** can't be checked without computations that are blocked.

**Update 2026-10-26 (owner, decision log #16): the dev period is now 2012-01-01 .. 2020-12-31.** Read the
**2012–2020** column below. The other columns stay as the record of the check that led to the decision; the
counts file was computed before it, at commit `338c180`. Q1_SCORING is now APPROVED (sigma_compare's method,
decision log #18). The other Q1 items wait for the owner.

**One thing first: the data check affects every sample size.** 2007–2011 differ from 2012–2020 (no daily
break, trading on CME holidays and Friday evenings; notebook "Data check: result"), and that decision is open
(DATA_CHECK_ACCEPTED). The counts are given for the whole dev period and for both spans; neither is chosen.

| OBSERVED (`results/q1_event_counts.json`) | 2007–2020 | 2007–2011 | 2012–2020 |
|---|---:|---:|---:|
| complete 15-min candles | 315,661 (:143) | 105,682 (:153) | 209,979 (:162) |
| … of which end on the hour | 78,505 (:144) | 26,164 (:154) | 52,341 (:163) |
| complete 30-min candles | 153,557 (:146) | 49,445 (:156) | 104,112 (:165) |
| … of which end on the hour | 76,762 (:147) | 24,743 (:157) | 52,019 (:166) |
| hourly labels (the grid, before any state) | 84,999 (:148) | 31,289 (:158) | 53,710 (:167) |

A "complete" candle has all its 1-min bars. OBSERVED: 2007 has 16,454 complete 15-min candles of 25,054
(`results/q1_event_counts.json`, `per_year.2007`). INFERRED (arithmetic on that file's `per_year`): the complete
share rises from 65.7 % (2007) to 95.5 % (2011), and is 97.1–99.8 % in 2012–2020.

**A constraint that shapes all the options.**
- CITED: HAR's state exists only at hourly labels (`backend/datafeed/features/vol_block.py:25-31`), and
  EVAL_GRID (APPROVED) is that grid.
- CITED: σ is fitted only for H = 60 and 240; any other H raises (`vol_block.py:37`, `vol_block.py:408`).
- INFERRED:
  - A pattern scored against HAR on the approved grid has to **end at an hourly label** (on the hour, UTC).
    That leaves one run end per hour, whatever the timeframe: about 77–79 thousand candidates (OBSERVED above).
  - **There is no HAR σ for a 15- or 30-minute candle.** "Small relative to σ" needs one of the conversions
    under Q1_SMALL_DEF.

## Q1_TIMEFRAME: candle timeframe (15 or 30 min)

| Option | Pros | Cons | Sample size |
|---|---|---|---|
| **A. 15 min** | More candles per run of a given length, so "a run" can be judged on more evidence; closer to the owner's chart reading | Noisier per candle; each needs a σ conversion further from H = 60 (√(60/15) = 2) | OBSERVED: 78,505 run ends on the hour (2012–2020: 52,341) |
| **B. 30 min** | Fewer, steadier candles; the conversion from σ_60 is smaller (√2) | A run of 3–4 candles spans 1.5–2 h, longer than one hourly row | OBSERVED: 76,762 on the hour (2012–2020: 52,019) |
| **C. Both, reported side by side** | Shows whether the effect depends on the timeframe | Doubles the tests, so a multiplicity correction (BH) and a pre-declared primary | INFERRED: the same candidates twice; the two sets overlap, so not independent |

INFERRED: on the hourly grid the timeframe hardly changes n. It changes what a run of small candles means.

## Q1_SMALL_DEF: a "small" candle, range relative to σ

The range is the candle's high − low. Using |close − open| (the body) instead is a sub-choice, open either way.

| Option | Pros | Cons | Sample size |
|---|---|---|---|
| **A. range / (σ_60 · √(tf/60))**, with σ_60 from the latest hourly label at or before the candle's start | Simple; uses the pinned model only | Assumes √t scaling inside the hour and ignores intraday seasonality within it (INFERRED) | UNVERIFIED: needs σ on the grid (Stage 1 step 2, blocked) |
| **B. range / σ_tf, with σ_tf² = var_60 · P(t, tf) / P(t, 60)**: the model's own seasonal mass (`vol_block.py:33`) shared out over the candle | Respects the 5-minute seasonal profile the model already carries | Still carries HAR's level from 60 to tf minutes; new code to test | UNVERIFIED (as A) |
| **C. No per-candle σ: the run's whole range over 60 minutes / σ_60** (4 × 15 min or 2 × 30 min ending at the label) | No scaling assumption: like for like with H = 60 | It becomes "a quiet hour" rather than "a run of tiny candles"; ties the run length to 60 minutes | INFERRED: at most one per hourly label: ≤ 84,999 (OBSERVED grid size) |

The threshold for "small" is a second choice inside each option:
- **(i) a fixed multiple k:** k is OPEN, with no value in the repo. Its sample size is UNVERIFIED until σ exists.
- **(ii) a quantile q of the ratio:** fitted on training years only, in walk-forward. INFERRED: about q × N
  candles count as small, by construction.

## Q1_RUN_LENGTH: how many small candles count as a run

INFERRED arithmetic, to show orders of magnitude only. With threshold (ii), and *if* small candles were
independent, runs of r end at about N · q^r of the N on-hour candidates. Volatility clusters, so real runs are
more common than this (INFERRED); their true counts are UNVERIFIED until σ exists.

| N · q^r (INFERRED), 15 min, N = 78,505 (2012–2020: 52,341) | r = 2 | r = 3 | r = 4 |
|---|---:|---:|---:|
| q = 0.10 | 785 (523) | 79 (52) | 8 (5) |
| q = 0.20 | 3,140 (2,094) | 628 (419) | 126 (84) |
| q = 1/3 | 8,723 (5,816) | 2,908 (1,939) | 969 (646) |

For 30 minutes the figures are within 3 % of these (INFERRED: N = 76,762 / 52,019).

| Option | Pros | Cons |
|---|---|---|
| **A. r = 2** | Many events (above) | Barely a "run"; close to "one quiet hour" |
| **B. r = 3** | A run in the usual sense; hundreds to thousands of events for q ≥ 0.2 | Below q = 0.2 the events get thin (INFERRED: tens) |
| **C. r = 4 (15 min) or r = 2 (30 min): one hour in each case** | Lines up with the hourly grid and with option C of Q1_SMALL_DEF | At 15 min and q ≤ 0.2, about 100 events or fewer |

## Q1_H: the horizon (60 or 240 min; register HORIZONS)

| Option | Pros | Cons | Sample size |
|---|---|---|---|
| **A. 60 min** | Matches a run measured over about an hour; windows overlap less | Too short for a slow expansion | CITED: 68,040 valid non-crossing rows on CONF 2007–2020 for that study's model (`research/sigma_compare/REPORT.md:17`) |
| **B. 240 min** | Leaves room for an expansion to unfold; the horizon of the levels and target studies | Windows from consecutive hourly labels overlap 4 to 1, so the effective n is smaller (INFERRED); more windows cross a close | CITED: 58,920 (`REPORT.md:18`) |
| **C. Both, 60 primary** | Shows how the effect evolves | Multiplicity (BH, as sigma_compare did: `research/sigma_compare/run_sigma.py:42`) | as A and B |

INFERRED: these CITED counts are for sigma_compare's model on the same years. The pinned model on this grid
should be close, but that is UNVERIFIED until step 4 runs.

## Q1_SCORING: how Q1 is scored against HAR (currently PROPOSED: option A)

| Option | Pros | Cons |
|---|---|---|
| **A. sigma_compare's method.** QLIKE of HAR's h against HAR with the pattern added, on the same valid non-crossing rows; walk-forward folds with a 24 h embargo; Diebold–Mariano with Newey–West lag 8 and BH | The repo's tested method (CITED: `run_sigma.py:41` NW_LAG = 8, `:42` BH_Q, `:402-428` dm and bh). It answers "does the pattern add information beyond HAR?" directly | Needs an augmented HAR fitted per fold, which brings more decisions (fold edges, refit of β only or also c); lag 8 was set for that study's horizons (INFERRED: it should be checked for 240 min) |
| **B. Conditional calibration with the frozen model.** Mean RV/h and RMS z (the CALIB_METHOD metrics, APPROVED) on pattern rows against all other rows; difference tested by a block bootstrap over days | No refit; reads exactly "a larger move than HAR forecasts" (ratio > 1 on pattern rows) | The bootstrap's block length and repetitions would be new OPEN items; a ratio is less powerful than a paired loss test (INFERRED) |
| **C. Regression of ln(RV/h) on a pattern indicator**, with HAC standard errors | One coefficient with a CI, easy to read | Assumes log-scale additivity; rows with RV = 0 are already excluded by `valid` (CITED: `run_sigma.py:178`), which conditions the sample |

INFERRED: every option uses only the valid, non-crossing rows (as CALIB_METHOD does). The pattern's n is the
run count above, not the grid size.

## The decisions needed for Q1 (in order)

1. DATA_CHECK_ACCEPTED: how 2007–2011 (and the 2014/2015 one-minute shift) are treated. This affects every count
   above.
2. Q1_TIMEFRAME and Q1_SMALL_DEF (option and threshold kind).
3. Q1_RUN_LENGTH and Q1_H.
4. Q1_SCORING (A as proposed, or B / C).
5. With those, the true event counts can be computed: still descriptive (Stage 1 step 2 is needed for σ), and
   before any outcome is looked at (`ATTEMPTS.json` stays 0 until then).
