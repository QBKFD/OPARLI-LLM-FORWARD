# Pre-registration: do GEX levels behave differently from matched placebo levels near monthly expiry?

**Committed before any evidence data for this test exists:** no daily
snapshot has been collected for it, and no outcome has been computed. The
owner's decisions of 2026-10-22 are in §2. The sample size N was set from a
touch-rate count on seen data (`TOUCH_RATE.md`; touches only, no outcomes,
no GEX data) under a rule fixed before that count. After this commit the file
is never edited; deviations go to "Amendments" in REPORT.md.

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

## 2. Owner decisions (2026-10-22)

| # | Decision |
|---|---|
| D1 | **Evidence: forward daily snapshots** (plan A). No historical data is bought. |
| D2 | **The seal:** this test reads XAUUSD prices after 2026-10-01 around GEX levels. It is to be **registered in the seal registry** (part of the C1 rewrite of the sealing rules) before its evidence period starts (§8, prerequisite P1). |
| D3 | **Price series: XAUUSD.** Each level is converted from futures to spot terms with the **spot-futures gap measured at the 05:00 UTC snapshot** (§4). This is **an approximation:** the gap moves during the day, and its moves aren't measured. |
| D4 | **Window W = 10** trading days up to and including the front expiry day. |
| D5 | **Smallest effect worth finding: Δ = 15 percentage points.** |
| D6 | **Snapshots by cron at 05:00 UTC** on the host (§8, P2). Whether to use a second IBKR username (against error 10197) is the owner's decision, after checking its cost. Missed snapshots count against gate G1. |

## 3. Data

**Levels:** one options snapshot per trade date d at 05:00 UTC, by the probe
(`tools/options_probe.py`); open interest as of d − 1, inferred as in the
feasibility study. The levels for trade date d come from that snapshot only.

**Prices:** XAUUSD 1-min bid/ask midpoint bars, the scanner's series, as the
datafeed records them. They run from the snapshot to the end of trade date
d's session (17:00 New York).

**σ:** the HAR σ of the price change over 60 minutes (`sigma_px_60`), computed
at each touch from XAUUSD bars before it. It uses the locked model
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

**From futures to spot (D3):**
- The gap: `G_d = F − S`, measured at the snapshot.
  - F is the mid of the front expiry's underlying GC future (by `underConId`).
  - S is the XAUUSD bid/ask mid that the probe records in the same snapshot
    (P3). If a snapshot lacks S, it is the close of the last XAUUSD 1-min bar
    before 05:00 UTC, and the fallback is recorded.
- Every level, real or placebo, is used as `L_spot = L − G_d` for the whole
  trade date.
- **This is an approximation.** The gap moves during the day (carry, funding
  and liquidity), and nothing here measures that. Because real levels and
  placebos are shifted by the same `G_d`, the error adds noise to both and
  lowers power. It isn't expected to favour either.

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

## 8. Evidence period and sample: fixed in advance, no early looks

**Prerequisites:** the evidence period starts on the first trade date after
all of these hold.
- **P1:** this study is registered in the seal registry (D2).
- **P2:** the daily 05:00 UTC cron runs the probe on the host (D6).
- **P3:** the probe records the XAUUSD bid/ask mid in the same snapshot as
  the futures (the gap, §4).
- **P4:** XAUUSD 1-min bars are recorded on the host (the datafeed's recorder,
  staged as in the deploy runbook), with S3 archiving.
- **P5:** the analysis code is written, tested on synthetic data, and frozen
  (its commit hash recorded here as an amendment) before any outcome is
  computed.

**Cycles:** a cycle counts only if its whole 10-day window lies inside the
evidence period and passes gate G1. **N = 22 counted cycles** (§9).
- While the data accumulates, only counts are monitored: cycles, snapshots,
  events and coverage. **No outcome (reject, break, neither) is computed or
  looked at before 22 counted cycles are complete.**
- A cycle that fails G1 isn't counted, and the next one takes its place.

## 9. Sample size: N = 22 expiry cycles

- **Formula (H1):** `n = (z_0.975 + z_0.80)² · s² / Δ²`, with Δ = 15 pp
  (one-sided α = 0.025, power 0.80).
- **s²**, the variance of `D_c` across cycles: `0.25 · (1/m) · (1 + 1/5) · 1.2 + 0.005`. The terms:
  - p = 0.5 is the reject rate;
  - 5 placebos per real level;
  - 1.2 is a design effect for touches on the same day;
  - 0.005 is the between-cycle variance of the true effect;
  - **m = 3 levels × q × 10 days**, the number of real touches per window.
- **q**, the probability that a band level is touched on a given day, was
  **counted on seen data** (`touch_rate.py` and `TOUCH_RATE.md`).
  - The data: XAUUSD 2007–2023 (202 expiry cycles, 1,756 window days), with
    round stand-in levels in the ±2 SD band.
  - **No GEX data and no outcomes.**
  - The rule was fixed before the count: N comes from the near-the-money
    weighted placement, rounded up, and is never below 16.

| Placement of the 3 stand-in levels | q (2007–2023) | Cycles for Δ = 15 pp |
|---|---|---|
| the 3 nearest round levels (upper bound) | 0.337 | 14.2 |
| **near-the-money weighted (central, the rule)** | **0.216** | **21.1** |
| uniform in the band (lower bound) | 0.172 | 26.0 |

**N = 22 cycles.** OG monthly options expire once a month, so this is about
**22 months of daily snapshots** after the evidence period starts.
- For example, if the first counted cycle is the December 2026 expiry
  (2026-12-28), the 22nd is the September 2028 expiry, so the analysis would
  run in late September 2028.
- Each cycle that fails G1 adds a month.

The count is conservative in one known way: on 263 window days the band held
fewer than 3 stand-in levels (a coarse round-number grid in a narrow band near
expiry), and those days were skipped. They are the days with the most
touches, so the true q is probably somewhat higher and 22 somewhat more than
needed.

**H2** (near against far) needs about twice H1's cycles for the same Δ, so it
is underpowered at N = 22. That is why it counts only by its sign.

## 10. Sensitivities (reported, never change the verdict)

- W = 5 instead of 10.
- k = 0.5 and 1.5; H = 30 and 120 minutes.
- The top 5 strikes instead of 3, and the top |GEX| strikes instead of the
  top open-interest strikes.
- No band (raw top open interest).
- Tolerance $2 instead of $1, which absorbs some of the gap's drift (D3).

## 11. Known limits

- **The spot-futures gap is fixed at 05:00 UTC** for the whole day (D3), an
  approximation; see §4.
- **Open interest is the previous day's.** Positions opened during the day
  aren't seen.
- **Only the OG monthly options:** weekly gold options (which also pin, near
  their own expiries), GLD options and OTC options are left out.
- **The zero-gamma sign convention is an assumption** (§4.2).
- **Round numbers** are controlled by the placebo design (§4.3), not
  eliminated: the open-interest ranking itself may favour some round strikes.
- **N rests on a planning count** (§9) with stand-in levels, realized rather
  than implied volatility, and expiry dates without holidays.
- **One instrument, one level family:** a supported verdict says nothing about
  trading costs or a strategy. That would be a separate plan.

