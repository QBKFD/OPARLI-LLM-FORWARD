# Pre-registration (DRAFT): do GEX levels behave differently from matched placebo levels near monthly expiry?

**Status: DRAFT, not pre-registered.**
- It becomes the PREREG once the owner settles the open decisions (§2) and it
  is committed **before any data is read for this test**: no forward
  snapshot used as data, and no historical open interest.
- After that it is never edited; deviations go to "Amendments" in REPORT.md.
- No data was read to write this draft. The sample sizes in §9 come from
  stated assumptions, not from data.

**Why this test exists:** `research/gex_feasibility/` (pre-registered verdict
INCONCLUSIVE, gate G5). The owner decided after seeing its report (post hoc,
2026-10-22) to build this test: R with IBKR's own gamma is 8.20 %, above 5 %
under every sensitivity. R is an unsigned upper bound, so it says an effect is
plausible, not that it exists.

## 1. Question and mechanism

Option dealers delta-hedge. Where dealers are long gamma, their hedging sells
rallies and buys dips, which damps moves near heavily held strikes. Where they
are short gamma, it amplifies moves. Gamma is largest near the money and grows
as expiry approaches, so the mechanism makes **testable predictions**:

1. **Pinning:** price touching a high-open-interest strike of the front OG
   expiry turns back more often than at matched placebo levels.
2. **It concentrates near expiry:** the effect is larger in the last trading
   days before the monthly expiry than earlier in the cycle. An effect that
   doesn't concentrate near expiry isn't gamma.
3. **It is symmetric:** pinning works from above and from below. An effect on
   one side only is trend or drift, not hedging.
4. **The zero-gamma level** (under a stated sign convention, §4.2) is a
   boundary, not a pin: price falling through it from above enters short-gamma
   territory, so breaks continue more often than at placebos.

## 2. Open decisions (owner)

| # | Decision | Options | Draft default |
|---|---|---|---|
| D1 | **Data plan** (§3) | **A:** forward daily snapshots from now on (slow: §9). **B:** historical CME end-of-day OG open interest and settlements on the seen periods (CONF 2007–2020, DEV 2021–2023), if they can be obtained. **A + B:** B first (development and test), then A as an out-of-sample forward check. | **A + B**, if B is available. Plan B's availability and cost aren't verified. |
| D2 | **The seal** (plan A) | Plan A reads post-2026-10-01 **prices** around levels, which `research/SEALING.md` reserves for the final strategy test. That needs an owner decision: carve out a GEX forward-test track (the data become "spent" for GEX), or use plan B only. | Owner's call. Nothing in plan A runs until then. |
| D3 | **Price series for outcomes** | **(a)** 1-min bars of the front OG expiry's underlying GC future, so levels and prices are on the same instrument (the datafeed records XAUUSD only today; GC bars would be new, live, recorded). **(b)** XAUUSD (the scanner's series), with each level mapped by the basis measured at the snapshot: `level − (F − S)`. | **(a)**: no basis error. (b) only if (a) can't be recorded. |
| D4 | **Window W** (trading days up to and including the front expiry day) | 5 or 10 | **10**: needs about half the expiry cycles (§9). The 5-day result is a sensitivity. |
| D5 | **Smallest effect worth finding, Δ** (difference in reject rate, real − placebo) | 10, 15 or 20 percentage points | **15 pp** (the earlier levels studies saw ±7–11 pp on price-only levels; a GEX effect worth trading should be larger) |
| D6 | **Snapshot automation** (plan A) | Daily at 05:00 UTC by cron on the host, with the probe (`tools/options_probe.py`, front expiry plus the next). The live IBKR account must be logged out at that time (error 10197), or use a second IBKR username with its own COMEX subscription. | A second username (the 10197 cut is otherwise a daily risk). |

## 3. Data

**Levels:** one options snapshot per trade date d, taken at about 05:00 UTC
(the probe; open interest as of d − 1, inferred as in the feasibility
study). Levels are fixed for trade date d from the snapshot and never use
anything after it.

**Prices:** 1-min bars per D3, from the snapshot time to the end of trade
date d's session (17:00 New York).

**σ:** the HAR σ of the price change over 60 minutes (`sigma_px_60`), computed
at each touch from bars before it. It uses the locked model
(`backend.datafeed.features.vol_block` `load_sigma_model` and `sigma_over`), as
`backend/agents/features.py` computes it.

## 4. Levels (per trade date d)

**Front expiry:** the nearest OG monthly expiry not yet expired at the
snapshot. On its expiry day it is still the front (the snapshot precedes the
13:30 New York expiry); the next trade date rolls to the following expiry.

**Band:** strikes with `|ln(K / F)| ≤ 2 · σ_ATM · √T_eff`, where
- F is the front expiry's underlying (as in the feasibility study);
- σ_ATM is the IV of the strike nearest F;
- `T_eff = max(T, 1 / 365)`.

Far strikes can't be reached within a day, and far-OTM "lottery" positions
dominate the raw open-interest ranking: in the feasibility snapshot, the top
two strikes by open interest were 20,000 and 15,000, with gold near 4,080.

### 4.1 L-OI: top open-interest strikes

The 3 strikes in the band with the highest open interest (calls + puts). A tie
goes to the strike nearer F.

### 4.2 L-ZG: the zero-gamma level

- **Signed GEX** at a hypothetical price S:
  `GEX(S) = Σ sign_i · Γ_i(S) · OI_i · 100 · S² · 0.01`, over every front-expiry
  contract with open interest.
- `Γ_i(S)` is Black-76 with r = 0 and each contract's snapshot IV held fixed
  ("sticky strike").
- **Sign convention:** **+1 for calls, −1 for puts.** Dealers are assumed long
  calls and short puts, the common retail convention.
- **The level:** evaluated on a $1 grid over `[0.90 F, 1.10 F]`. L-ZG is the
  sign change nearest F, linearly interpolated; with none, there is no L-ZG
  that day.
- **The convention is an assumption.** Public data shows open interest, not
  who holds which side. H3 tests the level under this convention only; no
  other convention is tried.

### 4.3 Placebos

**For L-OI (5 placebos per real level):** strikes of the same front expiry
- on the same side of the price `P0` at the snapshot;
- of the same **roundness class** (the largest of 1000, 500, 100, 50, 25, 10, 5
  that divides K);
- with distance `|K' − P0|` within ±25 % of the real level's `|K − P0|`;
- with open interest **below the band's median**;
- more than $5 from any real level.

They are drawn at random with seed = the trade date (YYYYMMDD). If fewer than
5 qualify, the roundness class is relaxed one step (recorded). A real level
with no placebo at all is left out of the comparison (counted).

**This controls for round numbers.** Strikes are round prices, and round
numbers can attract price on their own. The placebos are equally round, at
the same distance, but held lightly.

**For L-ZG (5 placebos):** prices drawn uniformly on the same side of P0,
within ±25 % of its distance, with seed = the trade date.

## 5. Events and outcome

- **Touch:** after the snapshot, the first 1-min bar whose `[low − tol, high + tol]`
  contains the level, with tol = $1 (the scanner's `TOL`). The approach
  side is the side of the previous bar's close. If the first bar after the
  snapshot already contains the level, there is no event that day ("started
  at the level"). One event per level per trade date.
- **Outcome**, first passage within H = 60 market-open minutes after the
  touch bar:
  - **reject** (y = 1): price reaches `L ∓ k·σ` on the approach side before
    `L ± k·σ` through the level;
  - **break** (y = 0): the reverse;
  - **neither:** neither within H, or both inside one bar.
  - k = 1, with σ = `sigma_px_60` at the touch. "Neither" is excluded from the
    rates, and counted and reported per group.
- The same definitions apply to real levels and placebos.

## 6. Hypotheses and verdict

The unit is the **expiry cycle** c (one monthly OG expiry). Events in a cycle
share a market regime, so they aren't independent; the test is across cycles.

| # | Hypothesis | Statistic | Test |
|---|---|---|---|
| **H1 (primary)** | Near expiry (the last W trading days), L-OI rejects more than its placebos | `D_c` = reject rate (real) − reject rate (placebo), over cycle c's window | one-sided one-sample t-test on `D_c` across cycles, α = 0.025; a cycle-level bootstrap as a check |
| H2 (mechanism) | The effect is larger near expiry than earlier in the cycle | `D_c(near) − D_c(far)` (far = more than W trading days to expiry) | one-sided paired t-test |
| H3 (zero-gamma) | Touches of L-ZG from above break more often than at placebos | `B_c` = break rate (real) − break rate (placebo), approaches from above, all days | one-sided t-test |

H2 and H3 are Holm-corrected at α = 0.05 (one-sided) and are reported
alongside H1, never instead of it.

**Verdict:**
- **"GEX pin supported"** if all of these hold:
  - H1 is significant;
  - **symmetry:** `D` > 0 separately for approaches from above and from below
    (point estimates);
  - H2's point estimate > 0 (the effect leans toward expiry).

  Then GEX levels become a candidate level family for the scanner (shadow
  only, under their own plan).
- **"Not supported"** otherwise: GEX is dropped as a level family.
- **H3** is reported, with no verdict of its own.

## 7. Gates (checked before any outcome is computed)

The lesson from the feasibility study's G5 applies: **a gate on an input is
restricted to, or weighted like, the part of the data the result depends on.**
Here, that is the band.

| # | Gate |
|---|---|
| G1 | Snapshots on at least 90 % of the trading days in each included cycle's window. A cycle below that is left out (counted), and the next cycle replaces it. |
| G2 | For each level-day: open interest on at least 90 % of the band's contracts, and an IV on contracts holding at least 90 % of the band's open interest. |
| G3 | At least 80 % of real L-OI events have 5 placebos. |
| G4 | Price bars cover at least 99 % of the market-open minutes in the windows used. |

## 8. Sample: fixed in advance, no early looks

The test runs on a **fixed number N of included expiry cycles** (§9, set by D4
and D5).
- Only counts are monitored while the data accumulates: cycles, events and
  coverage. **No outcome (reject, break, neither) is computed or looked at
  before N cycles are complete.**
- Under plan A+B, B is analysed first under these rules, and A's forward
  cycles are then a separate, pre-declared confirmation with the same N rule.

## 9. How many expiries, and how long

- **Formula:** `n = (z_0.975 + z_0.80)² · s² / Δ²` (one-sided α = 0.025,
  power 0.80).
- **s²**, the variance of `D_c` across cycles: `0.25 · (1/m) · (1 + 1/5) · 1.2 + 0.005`. The terms:
  - p = 0.5 is the reject rate;
  - m is the number of real touches per cycle window;
  - 5 placebos per real level;
  - 1.2 is a design effect for touches on the same day;
  - 0.005 is the between-cycle variance of the true effect.
- **m = 3 levels × q × W**, where q is the probability that a band level is
  touched on a given day.

**The touch rate q is the main unknown.** Before this is finalized, it can be
counted on seen data (XAUUSD 2007–2023) for round-number levels near the
price, without any GEX data or outcomes.

**Expiry cycles needed for H1** (one monthly OG expiry per month, so roughly
**months of daily snapshots** under plan A, plus setup):

| q | W (days) | m | Δ = 10 pp | Δ = 15 pp | Δ = 20 pp |
|---|---|---|---|---|---|
| 0.2 | 5 | 3.0 | 98 | 44 | 25 |
| 0.2 | 10 | 6.0 | 51 | 23 | 13 |
| **0.3** | 5 | 4.5 | 67 | 30 | 17 |
| **0.3** | **10** | 9.0 | 36 | **16** | 9 |
| 0.4 | 5 | 6.0 | 51 | 23 | 13 |
| 0.4 | 10 | 12.0 | 28 | 12 | 7 |

- **With the draft defaults (W = 10, Δ = 15 pp):** about **16 expiry cycles**,
  so **about 16 months** of daily snapshots, with a range of 12–23 months for
  q between 0.4 and 0.2.
- Finding a 10 pp effect takes about 3 years (28–51 months). A 5-day window
  roughly doubles every figure.
- **H2 needs more than H1:** a difference of two noisy differences, so about
  twice as many cycles for the same Δ. With N set for H1, H2 is underpowered,
  which is why it is a secondary check, not a condition beyond its sign.
- **Plan B:** 2007–2023 holds about 204 monthly cycles (about 168 in CONF and
  36 in DEV). If the historical data exists, the test is powered for
  Δ = 10 pp on CONF alone, and DEV is a replication. It runs in weeks, not
  years.

## 10. Sensitivities (reported, never change the verdict)

- W = 5 instead of 10.
- k = 0.5 and 1.5; H = 30 and 120 minutes.
- The top 5 strikes instead of 3, and the top |GEX| strikes instead of the
  top open-interest strikes.
- No band (raw top open interest).
- Under D3 (b), the basis measured at each touch instead of at the snapshot.

## 11. Known limits

- **Open interest is the previous day's.** Positions opened during the day
  aren't seen.
- **Only the OG monthly options:** weekly gold options (which also pin, near
  their own expiries), GLD options and OTC options are left out.
- **The zero-gamma sign convention is an assumption** (§4.2).
- **Round numbers** are controlled by the placebo design (§4.3), not
  eliminated: the open-interest ranking itself may favour some round strikes.
- **One instrument, one level family:** a supported verdict says nothing about
  trading costs or a strategy. That would be a separate plan.
