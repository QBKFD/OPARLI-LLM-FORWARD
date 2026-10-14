# Levels diagnostic (2007–2023): report

**DIAGNOSTIC / EXPLORATORY ONLY.** The data has been seen. Plan:
[DIAGNOSTIC_PLAN.md](DIAGNOSTIC_PLAN.md) (4e36123, addenda a851caf and a3a955c, each
committed before the run it governs). Decisions: [DECISIONS.md](DECISIONS.md). Raw output:
[results/diag_results.json](results/diag_results.json). No timestamp >= 2024-01-01
was read (2024–26 is spent; the loader asserts it), and nothing >= 2026-10-01.

## Status: STOPPED again at machinery validation, now with the calendar block bootstrap. Q1–Q5 not run.

Plan addendum 2 (a3a955c) replaced the real-day bootstrap with a **circular
moving-block bootstrap over calendar time** (each real *and* placebo event
weighted by its own day, 5,000 draws). The gate was: continue only if block
length 120 passes A/A for NEAR and FAR. **It failed (NEAR coverage 0.885),** so
Q1–Q5 were not run, and the levels_confirm CI correction was not applied (§3).

## 1. Validation, all block lengths (corrected design, 2007–2023; 4,379 days, 8,294 real levels × 20 placebos)

| Bootstrap | A/A NEAR mean / coverage | A/A FAR mean / coverage | inject +10 pp: recovered / coverage | inject −10 pp: recovered / coverage | gate |
|---|---|---|---|---|---|
| real-day clusters (first run) | +0.41 pp / **0.905** | +0.44 pp / 0.935 | +9.93 / 0.975 | −9.97 / 0.960 | fail |
| block 60 | +0.41 / **0.900** | +0.44 / 0.940 | +9.93 / 0.970 | −9.97 / 0.950 | (fail) |
| **block 120 (primary)** | +0.41 / **0.885** | +0.44 / 0.940 | +9.93 / 0.960 | −9.97 / 0.960 | **FAIL** |
| block 240 | +0.41 / **0.875** | +0.44 / **0.925** | +9.93 / 0.935 | −9.97 / 0.955 | (fail) |

Criteria: A/A mean within ±1 pp AND coverage of 0 within 93–97 %, for NEAR and
FAR. The means pass everywhere; **NEAR coverage fails under every bootstrap** and
gets *worse* with longer blocks. Injected ±10 pp effects are recovered to within
0.1 pp at every block length.

## 2. What the failure is (investigation, `aa_investigation.py`; placebo-only A/A data, block 120, 200 reps)

| | NEAR | FAR |
|---|---:|---:|
| pooled estimator (used so far): mean A/A effect | +0.41 pp | +0.44 pp |
| **group-balanced estimator: mean A/A effect** | **+0.001 pp** | −0.26 pp |
| SD of A/A estimates across reps | 1.28 pp | 1.51 pp |
| mean bootstrap SE | 1.10 pp | 1.46 pp |
| **SE ÷ SD** | **0.86** | 0.97 |

- **Calendar dependence is not the cause.** Resampling calendar blocks changes
  nothing (coverage 0.875–0.905 across schemes). The shared-placebo-day
  hypothesis from the first report is **not supported.**
- **Small estimator bias:** the pooled placebo rate gives more weight to groups
  with many placebo breaks. A group-balanced estimator (each real level against
  its *own* placebos' mean, then averaged) removes the NEAR bias completely.
- **SE about 14 % too small for NEAR** under every scheme. A plausible reason is
  specific to the A/A construction: picking which placebo is "real" also changes
  which groups are paired, a variance source that the real design (where the
  real level is fixed) doesn't have in the same form. This hasn't been shown.

## 3. Consequence for `research/levels_confirm` (not changed)

The correction step needed a validated bootstrap, and none passed, so the
confirm report is untouched and 2024–26 was not reread. The rough size of the
problem: inflating its SE by 1/0.86 widens the NEAR CI [+4.0, +18.5] to about
[+2.9, +19.7]. It still excludes 0, and the **verdict (REJECTED, from the
point estimate +11.3 pp) doesn't depend on the CI.** The pooled-estimator bias
(about +0.4 pp) is small relative to +11.3 pp.

## 4. Options (owner's decision; nothing done)

1. **Switch to the group-balanced estimator** (A/A bias ≈ 0) **and** calibrate
   its SE, e.g. by resampling real levels with their own placebos (group
   bootstrap) inside calendar blocks. Re-validate with the same A/A / injection
   gate before any Q1–Q5.
2. Accept an empirical SE inflation of about 1/0.86 ≈ 1.16 for NEAR (ad hoc;
   it needs its own validation).
3. Redesign the A/A so the pseudo-real choice doesn't change the pairing set
   (pair every placebo with every group), to test whether the failure is an
   artifact of the A/A test itself.

## 5. Hypotheses worth testing on fresh data (EURUSD, or gold from 2026-10-01)

Unchanged from the first report. **Each now needs an inference method that passes the A/A gate (option 1 above), and the group-balanced estimator.**

These are based only on results already committed (the confirmatory test and its
declared dry run), not on Q1–Q5.

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
