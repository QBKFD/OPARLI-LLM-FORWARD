# Pre-registration: levels confirmation test (untouched 2024+ data)

Committed **before any outcome, return or event statistic on 2024+ data** was
computed (only the counts audit `DATA_AUDIT_2024.md`, commit 5b7edad, came
before). Never edited afterwards. Research only; this spends the previously
sealed period by decision of the owner (see `research/SEALING.md`).

## Hypothesis (plain)

When gold breaks yesterday's high or low, and that level was close to where the
day opened, the breakout fails more often than a breakout of a meaningless
price. It is worst in Asia and late in the day.

## Data

- XAUUSD 1-min, `database/ohlcv_data/XAUUSD.txt`, UTC (confirmed by the audit:
  range profile, and session edges at 23:00 / 21:59 UTC in winter). **Test
  period: trading dates 2024-01-02 .. 2026-01-30 inclusive.** Nothing after
  2026-01-30 is used (the loader asserts it). Rows from 2023-11-01 are read only
  for the first PDH and the volatility warm-up. The global seal (2026-10-01) is
  asserted too.
- Day and week boundaries are 17:00 America/New_York (as in `research/levels`).
  Days with < 80 % of 1380 minutes are excluded, and their high/low isn't used as
  a level.

## Volatility unit

`research/vol_block` `VolBlock` is **fit once on 1-min data 2007-01-01 ..
2023-12-31**, then applied to 2024+ with **no refit**. σ_d = price · sqrt(exp(L)
· p̄ · 1440), exactly as in `research/levels` (p̄ = the fitted profile's mean per
US-DST state, L from the last completed 1H bar, forward-filled if missing).

## Levels, eligibility, distance

- PDH and PDL only (the previous completed trading day). Eligibility and first
  event per level per day as in `research/levels`: approach side at the day start
  by >= 0.1 σ_d, gap-throughs excluded.
- **Distance** d = side · (level − day open) / σ_d0, with σ_d0 = |level| ·
  f(first minute of the day) (the `research/levels` convention; eligible levels
  have d >= 0.1). **NEAR: 0.1 <= d <= 0.5. FAR: d > 0.5.**

## Event and outcome (code reused unchanged from `research/levels`)

- **BREAK** = the first 5-min close beyond the level by >= 0.1 σ_d
  (`levels.first_break`, via `levels.run_unit`).
- **CONTINUED** = first passage from the break close, b = 0.3 σ_d (entry-price
  σ_d), 240 min: price moves b further beyond before moving b back toward the
  level (`levels.first_passage`; same-bar double hits count as not continued;
  timeouts count as not continued).

## Placebos (distance-matched, day-shifted)

- For each real eligible level (side s, distance d, day D): **20 placebo days**,
  each drawn uniformly (with replacement) from the non-excluded test-period
  trading days whose index in that calendar is within ±60 of D, **excluding D**.
- On placebo day P the placebo level is p = open_P / (1 − s · d · f_P), where f_P =
  f(first minute of P). This places it **on the same side at exactly distance d
  in σ_d units of day P** (s · (p − open_P) / (|p| · f_P) = d, the same σ
  convention as the real level).
- A draw is rejected and redrawn (a new day) if p lies within 0.15 σ_d(P) of P's
  real PDH or PDL, or fails eligibility on P (up to 1000 draws per placebo).
- Placebos run through the identical `levels.run_unit` code. Seed:
  `numpy.random.default_rng(20260130)`, drawn in the order (date, PDH then PDL).

## Effect and inference

- **Effect** = CONTINUED rate(real) − CONTINUED rate(placebo events attached to
  those real levels), where those real levels are the ones with a real BREAK
  event, within the NEAR (or FAR) set.
- **Bootstrap:** resample real trading days D (each carries its real levels and
  their attached placebos), **10,000 draws**, seed `numpy.random.default_rng(20260131)`.
  One-sided p for effect < 0: (#{draws >= 0} + 1) / (B + 1). 95 % CI = the 2.5 /
  97.5 percentiles. SE = the SD of the bootstrap effects.
- **MDE** (80 % power, one-sided α = 0.05) = (1.6449 + 0.8416) · SE, in pp.

## MAIN CLAIM (the only pass/fail)

For NEAR levels: **effect <= −5 pp AND one-sided p < 0.05.**
- **SUPPORTED:** both conditions met.
- **REJECTED:** point estimate >= 0, OR (p >= 0.05 AND MDE <= 10 pp).
- **INCONCLUSIVE:** p >= 0.05 AND MDE > 10 pp.
- **SMALLER THAN CLAIMED** (the task's mapping leaves this case open, so it is
  added here before any data): p < 0.05 but −5 pp < effect < 0. The main claim
  as stated is not supported. The result is reported as a significant but
  smaller effect.

## Specificity check (reported with the verdict)

The FAR effect (with CI), and near − far with a one-sided bootstrap p (for
near − far < 0, using the same draws). If the main claim is SUPPORTED but p(near
− far < 0) >= 0.10, the conclusion is: "PDH/PDL breaks fail generally; the
near-open part is not supported."

## Side claims (evaluated ONLY if the main claim is SUPPORTED)

NEAR events are split by **each event's own break time** (UTC hour of the break
close): Asia [00, 07), London/NY [07, 17), Late [17, 24). Cell effect = real
rate − placebo rate within the cell (placebo events attached to NEAR real levels
with a break). Two claims: Asia − London/NY < 0, and Late − London/NY < 0, with
one-sided bootstrap p on the same draws and **Holm correction across the two**,
each needing an adjusted p < 0.05.

## Figure (reported)

Mean path in σ_d units after the break (−30 .. +240 min, signed "further beyond"
= +), real NEAR vs the band of the 20 placebo-index means.

## Exploratory (cannot change the verdict)

TOUCH (H1) and SWEEP-RECLAIM (H3) for NEAR / FAR with the same placebos, the
BREAK barrier/horizon grid (b ∈ {0.2, 0.3, 0.5}, H ∈ {60, 240}), and the
comparison with the 2007–2020 exploratory estimate (nearest quintiles −16 to
−17 pp) as context, not evidence.

## Run discipline

- **Declared dry run:** before the confirmatory run, the identical pipeline runs
  once on an already-seen pseudo-test period (trading dates 2021-01-04 ..
  2023-06-30, `VolBlock` fit on data < 2021-01-01), only to find bugs. Its numbers
  are development output, not evidence, and are reported as such.
- **Then the confirmatory run happens ONCE** on 2024-01-02 .. 2026-01-30. No
  reruns with changed settings. If a bug is found afterwards, it is fixed, the
  test is rerun once, and both results are documented.
