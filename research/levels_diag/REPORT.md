# Levels diagnostic (2007–2023): report

**DIAGNOSTIC / EXPLORATORY ONLY.** The data has been seen. Plan:
[DIAGNOSTIC_PLAN.md](DIAGNOSTIC_PLAN.md) (4e36123, plus the addendum a851caf, both
committed before any run). Decisions: [DECISIONS.md](DECISIONS.md). Raw output:
[results/diag_results.json](results/diag_results.json). No timestamp >= 2024-01-01
was read (2024–26 is spent; the loader asserts it), and nothing >= 2026-10-01.

## Status: STOPPED at machinery validation. Q1–Q5 not run, as the plan requires.

The plan (addendum item 6) says: if the A/A test fails, stop and report the bias
instead of the diagnostic results. It failed for NEAR levels, so **no Q1–Q5
number exists**. The code refuses to compute them after a failed A/A.

## 1. Machinery validation (corrected design, 2007–2023: 4,379 days, 8,294 real levels × 20 placebos, none short)

| Check | Result | Criterion | |
|---|---|---|---|
| **A/A, NEAR** (200 reps) | mean effect **+0.41 pp** (SD 1.27), **95 % CIs contain 0 in 90.5 %** | mean within ±1 pp, coverage 93–97 % | **FAIL** (coverage) |
| A/A, FAR (200 reps) | mean +0.44 pp (SD 1.51), coverage 93.5 % | same | pass (at the edge) |
| Injection +10 pp (2021–23, 352 real NEAR breaks, 200 reps) | recovered **+9.93 pp** (SD 2.57), bias −0.07 pp, CI coverage 97.5 % | – | fine |
| Injection −10 pp | recovered **−9.97 pp** (SD 2.38), bias +0.03 pp, CI coverage 96.0 % | – | fine |

**Reading:**
- The **point estimator is unbiased.** A/A means are about 0, and injected ±10 pp
  effects are recovered to within 0.1 pp.
- The **confidence intervals are too narrow** under the null for NEAR levels.
  90.5 % actual coverage for a nominal 95 % interval means the bootstrap SE is
  understated by roughly 15–20 %.

## 2. Where the bias comes from (counts only, `bias_check.py`)

The bootstrap resamples **real trading days** and treats them as independent
clusters, each carrying its placebos. But the placebo days (±60 trading days,
drawn with replacement) are **shared by many clusters**:

| | placebo units | mean real-day clusters per placebo day | max | share of placebo units on days shared by >= 5 clusters |
|---|---:|---:|---:|---:|
| NEAR | 61,180 | **12.4** | 33 | 99.5 % |
| FAR | 104,700 | 21.1 | 40 | 99.99 % |

One calendar day's price path enters about 12 "independent" clusters at once, so
their outcomes are correlated and the day-level resampling understates the
variance. In the A/A test *both* sides are placebo-day events, which is the
worst case. In the real design the real events sit on their own day, so only
the placebo side is affected. Real tests are probably less miscalibrated than
the A/A figure, but the direction of the error is the same: too-narrow CIs.

## 3. Consequence for the confirmatory test (`research/levels_confirm`)

That test used the same bootstrap, so its CI is probably somewhat too narrow.
**Its verdict doesn't depend on it:** REJECTED followed from the *point estimate*
being >= 0 (+11.3 pp), not from the CI. Widening the reported CI [+4.0, +18.5] by
20 % gives roughly [+2.8, +19.8], which still excludes 0. So "near-open breaks
continued *more*, not less" survives. That statement is approximate, not a
recomputed CI.

## 4. Proposed fix (not run; the owner's decision)

Resample **calendar time**, not real days. A moving-block bootstrap over
contiguous blocks of about 120 trading days (longer than the ±60-day placebo
window), where each block carries every real *and* placebo event whose day falls
in it, or a two-way (real-day × placebo-day) cluster bootstrap. The fix is valid
only if it passes the same A/A (coverage 93–97 %) and injection checks. Then
Q1–Q5 can be run as planned.

## 5. Hypotheses worth testing on fresh data (EURUSD, or gold from 2026-10-01)

These are based only on results already committed (the confirmatory test and its
declared dry run), not on Q1–Q5. **Each needs the fixed bootstrap from §4.**

1. **Near-open PDH/PDL breaks carry momentum.** For levels with 0.1 <= d <= 0.5
   σ_d, the continuation rate (b = 0.3 σ_d, 240 min) is **higher** than for
   day-shifted, distance-matched placebos by **>= +5 pp**. Evidence so far: +6.2 pp
   (2021–23, dry run), +11.3 pp (2024–26, confirmatory, not its pre-registered
   direction).
2. **The momentum is specific to near-open levels:** NEAR minus FAR effect
   **>= +5 pp** (2024–26: +7.4 pp, CI crossing 0).
3. **Touch reaction (H1) has no effect at real levels:** |effect| < 3 pp for NEAR
   and FAR (2024–26: −2.2 and −3.5 pp, CIs cross 0). A useful null to confirm
   before anyone builds on "levels hold".
