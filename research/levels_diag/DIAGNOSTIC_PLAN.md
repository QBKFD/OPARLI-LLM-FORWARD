# Diagnostic plan: levels study rerun with the corrected placebo design

**EXPLORATORY / DIAGNOSTIC ONLY.** 2007–2023 has been seen (`research/levels`,
`research/levels_confirm` dry run), so nothing here is confirmatory. It explains
the reversal between the 2007–2020 exploratory result and the 2024–26
confirmatory result, and it proposes hypotheses for fresh data. Committed before
anything is run.

## Data and seal

- `XAUUSD.txt` (pre-2024 cache), trading dates **2007-01-01 .. 2023-12-31**.
  Rows from 2006 are used only for the first PDH and the volatility fit.
- **Loader asserts:** nothing >= 2024-01-01 (2024–2026 was spent by
  `research/levels_confirm` and is not reread), and nothing >= 2026-10-01
  globally.

## Method (reused unchanged)

- `research/levels_confirm/confirm.py` functions, imported and not modified:
  `build_units` (day-shifted, distance-matched placebos, ±60 trading days, 20 per
  real level, seed 20260130), `placebo_level`, `effect_draws`, `summarize`,
  `boot_W`, `sigma_single_fit`. Events and outcomes come through
  `research/levels` `run_unit` (BREAK = first 5-min close >= 0.1 σ_d beyond;
  CONTINUED = b = 0.3 σ_d further before b back, 240 min; timeout = not
  continued). The placebo window spans the whole 2007–2023 calendar of
  non-excluded trading days.
- **Volatility unit:** `VolBlock` refit **yearly on data before each year**
  (expanding): events in year Y use a fit on 1-min data 2006-01-01 .. Y-01-01.
  2007 therefore uses 2006 only. 2006 fails the long audit's coverage rule, but
  it has ~333k rows, enough for the 576-bucket profile. Only past data is ever
  used.
- **NEAR** 0.1 <= d <= 0.5 σ_d, **FAR** d > 0.5 (as in PREREG of levels_confirm).
- **Inference:** block bootstrap over real trading days (placebos attached to
  their real day), 10,000 draws. Each cell gets its own draws over the real days
  present in that cell. **Two-sided** 95 % percentile CIs, and two-sided p =
  2 · min(share of draws <= 0, share >= 0).
- **Old design for comparison:** the `research/levels` same-day placebo design
  (`levels.build_units`, DAILY family only, seed 20240101), run under the **same**
  σ unit and the same real levels, so only the placebo design differs. The
  published numbers from `research/levels` are also quoted.

## What is reported (exactly)

1. **Q1 (was it the design?):** NEAR and FAR BREAK-continuation effects for
   2007–2020 with the corrected placebos, next to (a) the old same-day design
   rerun on the same levels and σ unit, split NEAR / FAR, and (b) the published
   `research/levels` estimates (DAILY H2 −7.3 pp overall; distance quintiles
   from `exploratory_distance.json`). Two-sided CIs.
2. **Q2 (era / trend dependence):**
   - the NEAR effect per calendar year 2007–2023 with 95 % CIs, as a figure (old
     design shown as a second series);
   - NEAR PDH vs NEAR PDL (2007–2023 and 2007–2020);
   - NEAR split by the sign of the prior 20-day gold return, ln(close of D−1 /
     close of D−21) over trading days, as a property of the real level's day
     (its placebos inherit the class). Also the 2 × 2 of PDH/PDL × up/down,
     i.e. breaks with vs against the prior trend.
3. **Q3 (other hypotheses, corrected design):** H1 touch reaction (fill $0.10)
   and H3 sweep-reclaim reversal, NEAR and FAR, 2007–2020 (2021–2023 alongside),
   two-sided.
4. **Q4 (direct artifact check):** the old same-day design and the corrected
   design on **2021–2023**, BREAK NEAR / FAR, side by side, plus the mean
   distance of the paired placebo levels from their own day's open in each
   design.

Then REPORT.md (at most ~1.5 pages): answers to Q1–Q4 with numbers and figures,
and "Hypotheses worth testing on fresh data (EURUSD or data from 2026-10-01)",
each stated with a direction and a minimum effect size. Everything is labelled
diagnostic.
