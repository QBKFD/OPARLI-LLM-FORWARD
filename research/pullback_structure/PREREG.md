# pullback_structure: pre-registration

**Amendment 1 (2026-10-27, owner, before any outcome): §11.** It replaces the
depth buckets with one pooled Q1 test and sets the verdicts. Rows marked
"AMENDED (A1)" in §0 are read as §11 says.

**Status: every choice (P1–P37) APPROVED by the owner, 2026-10-27.** Written
before any code, and before any price data was loaded or looked at for this
study. Nothing below has been computed. The commit that merges this file into
`main` is the pre-registration.

**Labels.**
- Every value in §0 is APPROVED. Which ones came from the owner's brief and
  which from Claude is in the draft at commit 243b26d.
- **CITED** means file:line on `main` at 2b1950d. **INFERRED** means my
  reasoning, not a measurement.

## 0. Choices (all approved)

| id | item | value | status |
|---|---|---|---|
| P1 | Timeframe | 4h candles built from 1-min bars | APPROVED (owner, 2026-10-27) |
| P2 | Candle anchor | New York time: 18–22, 22–02, 02–06, 06–10, 10–14, 14–17 (the last candle is 3h, ending at the daily break) | APPROVED (owner, 2026-10-27) |
| P3 | Incomplete candles | a candle with < 50 % of its scheduled open minutes is dropped, and counted | APPROVED (owner, 2026-10-27) |
| P4 | Swing confirmation | k = 3 candles each side. A swing exists only once the k-th candle after it has closed | APPROVED (owner, 2026-10-27) |
| P5 | Swing tie rule | swing low: the candle's low is strictly below the lows of the k candles on each side (mirror for highs); ties make no swing | APPROVED (owner, 2026-10-27) |
| P6 | Bearish structure | the last 3 confirmed swing highs are strictly descending (2 lower highs), **and** the last 3 confirmed swing lows are strictly descending (2 lower lows), the newest being the swing low that starts the event | APPROVED (owner, 2026-10-27) |
| P7 | Last lower high (LH) | the newest confirmed swing high before the event's swing low | APPROVED (owner, 2026-10-27) |
| P8 | Turn | the first 4h close below the previous candle's low, after the swing is confirmed (mirror for bull) | APPROVED (owner, 2026-10-27) |
| P9 | σ | HAR σ over 240 min, `sigma_over(model, state, 240).sigma_px`, pinned model `parquet2021_2023` (SHA-256 `dc0eb3c5…0dde`, CITED `backend/datafeed/features/vol_block.py:336-337`). State as of the close of the confirmation candle | APPROVED (owner, 2026-10-27) |
| P10 | Pullback depth | (pullback_high − swing_low) / σ, at the turn | APPROVED (owner, 2026-10-27) |
| P11 | Depth buckets | [0, 0.5), [0.5, 1.0), [1.0, 1.5), ≥ 1.5 σ | AMENDED (A1, §11) |
| P12 | **Q1 outcome** | **first passage:** A = a 1-min low below swing_low before a 1-min high above pullback_high. This is the event the baseline P is exact for (§3.6) | APPROVED (owner, 2026-10-27) |
| P13 | Order within a candle | resolved on the 1-min bars; a single 1-min bar that holds both levels counts **against** the hypothesis (not A) | APPROVED (owner, 2026-10-27) |
| P14 | B | a 4h close above LH, then a high ≥ that close + X·σ within W candles; σ as of the breaking close | APPROVED (owner, 2026-10-27) |
| P15 | X, W | X = 1.0 σ, W = 5 candles | APPROVED (owner, 2026-10-27) |
| P16 | C | a 4h close above LH that does not become B within W candles. C is fixed when the W-th candle closes | APPROVED (owner, 2026-10-27) |
| P17 | Max horizon | 30 candles from the turn (Q1), from the break (Q2) and from the moment C is fixed (Q3); otherwise "unresolved", counted and reported | APPROVED (owner, 2026-10-27) |
| P18 | Overlapping events | per direction, at most one live event; a new event can start only after the previous one is resolved | APPROVED (owner, 2026-10-27) |
| P19 | Bull and bear | the bull side is the mirror. Both are pooled for the tests and also reported apart (descriptive) | APPROVED (owner, 2026-10-27) |
| P20 | Costs | no Q1–Q3 metric is a return, so no spread is deducted. A trading rule built on a result would need its own PREREG with costs | APPROVED (owner, 2026-10-27) |
| P20a | Spread value, if P20 is rejected | no measured XAUUSD spread for 2016–2023 is in the repo. A provisional proportional cost exists: 1.0 bp spread (CITED `research/level_target/DECISIONS.md:16`, T7) | not needed: P20 approved (no spread deducted) |
| P21 | Data | 2016-01-01 .. 2026-01-30: `database/ohlcv_data/XAUUSD.txt` (manifest sha256 `dc2d30cd…abfb9`), 1-min, UTC. 2026-02-01 .. 2026-09-30: the IBKR backfill, manifest key `database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet` (named here for `research/data_guard.py`), read only after the manifest's `timezone_break` check passes. Events from 2016-01-01; warm-up from 2015-07-01 for structure and the HAR state | APPROVED (owner, 2026-10-27; periods changed the same day) |
| P22 | Dev period | 2016-01-01 .. 2023-12-31 | APPROVED (owner, 2026-10-27; changed from 2016–2020 the same day) |
| P23 | Holdout period | 2024-01-01 .. 2026-09-30, read once (σ out of sample throughout, §2) | APPROVED (owner, 2026-10-27; changed from 2021–2023 the same day) |
| P24 | Events at a period end | an event whose horizon runs past its period's end is dropped, and counted ("truncated") | APPROVED (owner, 2026-10-27) |
| P25 | Q2 null | Q2 needs a p-value for the BH family. The null is B / (B + C) from the same pipeline run on the simulated random walk (§5) | APPROVED (owner, 2026-10-27) |
| P26 | Q3 baseline | at the moment C is fixed: P = (H − close) / (H − swing_low), with H = the highest high since the swing low | APPROVED (owner, 2026-10-27) |
| P27 | Tests | Q1 and Q3: one-sided, excess > 0. Q2: two-sided against the P25 null | APPROVED (owner, 2026-10-27) |
| P28 | Bootstrap | block bootstrap of events by calendar month, 10,000 resamples, seed 20261027; 95 % percentile CI | APPROVED (owner, 2026-10-27) |
| P29 | BH family | dev: Q1 × 4 buckets, Q2, Q3 (6 p-values); holdout: Q1 in the dev-fixed buckets, Q2, Q3. BH q = 0.05 | AMENDED (A1, §11) |
| P30 | Minimum events | a bucket needs ≥ 50 resolved events in a period to be tested, else it's reported as "too few" | AMENDED (A1, §11) |
| P31 | Bucket fixed on dev | every bucket with dev excess ≥ 5 pp, 95 % CI excluding 0 and BH p < 0.05. If none: stop at dev, verdict "not supported on dev", and no holdout look | AMENDED (A1, §11) |
| P32 | Pass rule | on holdout, at least one dev-fixed bucket has Q1 excess ≥ 5 pp, a 95 % CI excluding 0, and BH p < 0.05 | AMENDED (A1, §11) |
| P33 | Random-walk check | §5. Pass: \|excess\| ≤ 1 pp in every bucket, and the 95 % CI covers 0 in 93–97 % of 200 dev-sized samples. A fail stops the study before dev | AMENDED (A1, §11) |
| P34 | Attempt counter | §9: `ATTEMPTS.json` records this registration as attempt 1 at the question; outcome analyses stay at 0 | APPROVED (owner, 2026-10-27) |
| P35 | 2026-02-01 .. 2026-09-30 | part of the holdout (P23). There is no separate second confirmation from the backfill; data from 2026-10-01 stays sealed for the final strategy test | APPROVED (owner, 2026-10-27; replaces "planned second confirmation") |
| P36 | Robustness (ATR) | on holdout only, Q1 is also reported with depth = (pullback_high − swing_low) / ATR(14) on 4h (Wilder, as of the confirmation close), with the P11 cut-points in ATR units, for the dev-fixed buckets. Reported next to the σ result, not part of the pass rule; any difference of ≥ 5 pp in excess or in pass/fail is stated in the verdict section | AMENDED (A1, §11) |
| P37 | Inspection gallery (dev only) | §10: one chart per event, dev 2016–2023 only, 60 sampled events per direction plus 20 audit events, seed 20261037; local and git-ignored; bug-finding only, never a pass/fail input | APPROVED (owner, 2026-10-27) |

## 1. Hypothesis

Bear case (bull is the mirror). After a confirmed swing low in bearish
structure, price pulls back. Once it turns down from the pullback high, the
chance of a new lower low **exceeds** the random-walk baseline

    P = (pullback_high − price) / (pullback_high − swing_low)

where price is the close of the turn candle. Under a driftless continuous
random walk, P is the probability of reaching swing_low before pullback_high
(gambler's ruin). So excess = mean(A − P) over events is 0 under the null.

## 2. Data and periods

**Seal registry** (CITED `research/SEALING.md`, `research/sealing.py`). From
2016 on:

| Years | Status | Used by |
|---|---|---|
| 2016–2020 | **unspent**, but seen by other studies on other questions | the CONF periods of `vol_features_r2/r3`, `trend_features`, `regime_v2`, `levels`, `sigma_compare`; `levels_diag`; `squeeze_oco` (dev 2012–2020) |
| 2020 | as above; also the warm-up rows of the pinned HAR fit | `sigma_har_parquet2021_2023_v1.json`: data from 2020-01-01, training from 2021-01-01 |
| 2021–2023 | **unspent**, seen; **the pinned HAR σ was fitted on it** | the DEV periods of most studies above, `vol_features` r1, the HAR fit |
| 2024-01-01 .. 2026-01-30 | **SPENT** (the only spent period in the registry) | `levels_confirm` |
| 2026-02-01 .. 2026-09-30 | unspent and **unread**. The backfill is prepared, not run; `research/data_guard.py` refuses it to any study whose PREREG doesn't name it | nothing |
| from 2026-10-01 | **SEALED** (final strategy test) | nothing may read it |

**No earlier study tested pullbacks or swing structure** (INFERRED from the
questions in `research/README.md`). The nearest is `trend_features`, which
found a 24h trend-exhaustion signal (Z24); that's a different variable on
1-hour data.

**Periods (approved, P22–P23; changed by the owner on 2026-10-27 to use all
data from 2016 on).**
- **Dev 2016-01-01 .. 2023-12-31, holdout 2024-01-01 .. 2026-09-30.** Together
  they are every unsealed year from the brief's start. The holdout is read
  once.
- **The holdout includes the spent period** 2024-01-01 .. 2026-01-30, by the
  owner's decision. `levels_confirm` read it once, on PDH/PDL breaks, a
  different question. No study has read it for pullbacks or swing structure.
  For this question it is as fresh as the seen years of dev (INFERRED).
- **σ is out of sample on the whole holdout.** The pinned model was fitted on
  2021–2023 (training rows), so on dev σ is in sample for 2021–2023 and uses
  later-fitted parameters for 2016–2020. That is a look-ahead in parameters,
  not prices. INFERRED: σ only normalises depth (the bucket) and X; the fit
  used realised variance, never swing outcomes.
- **The backfill must exist before the holdout runs.** The owner runs it on
  EC2. Its timestamps must pass the manifest's `timezone_break` check (IBKR
  data after 2026-02-01 switched to US-Eastern time), and it is read through
  `assert_may_read` with this study's name. The two files meet over a
  weekend: 2026-01-30 21:59 UTC, then 2026-02-01.
- **Nothing from 2026-10-01 on is read** (`sealing.assert_before_global_seal`).
- **Alternative considered, not chosen:** fit a HAR on 2007–2015 only for this
  study, as `level_target` proposed (CITED
  `research/level_target/DECISIONS.md:20`, T11). That departs from "pinned
  240-min model", and with the holdout from 2024 it isn't needed for an
  out-of-sample σ.
- **Source:** `XAUUSD.txt` covers 2016-01-01 .. 2026-01-30 in one file, UTC
  throughout (CITED: manifest, `timezone`). The clean parquet starts in 2020,
  and equals the file close for close from 2020 to 2023 (CITED: manifest,
  `verified`).

## 3. Definitions

1. **Candles (P1–P3).** 4h candles built from 1-min bars, anchored to New
   York time (P2), so the daily break never falls inside a candle. OHLC
   comes from the 1-min bars within the candle.
2. **Swings (P4, P5).** A swing low at candle i is confirmed at the close of
   candle i + k, and usable only from then on. No later bar is ever used to
   define a swing.
3. **Event start (P6, P7, P18).** A swing low confirmed in bearish structure
   starts an event. LH is the newest swing high before it.
4. **Pullback.** After confirmation, pullback_high is the highest high of
   the candles after the swing-low candle, up to the turn.
   - If a 1-min low goes below swing_low before the turn, the event ends as
     "lower low before turn" (counted; not a Q1 event).
   - If a 4h close goes above LH before the turn, the event enters Q2
     directly and is not a Q1 event.
5. **Turn (P8).** The first close below the previous candle's low. Depth
   (P10) and P are fixed at that close, and tracking starts at the next
   1-min bar.
6. **Baseline P.** As in §1, with price = the turn close, so 0 < P < 1 by
   construction. P is exact only for a first passage between pullback_high
   and swing_low, hence P12.
7. **Why P12 rather than "any new lower low within 30 candles".** That
   literal outcome would also count lower lows reached after price went back
   above pullback_high, which P doesn't account for. The excess would then
   be positive even for a random walk. If the owner prefers the literal
   outcome, the baseline must change too, for example to the
   random-walk-simulated rate per depth bucket.
8. **Outcomes A, B, C (P12–P17).**
   - **A:** P12 (Q1).
   - **B and C:** on every close above LH before a new lower low (Q2).
   - **After C:** the lower-low rate against the P26 baseline (Q3),
     first-passage between H and swing_low as in P12.

## 4. Questions and statistics

- **Q1:** per depth bucket, excess = mean(A − P) over resolved events.
- **Q2:** B / (B + C), against the random-walk share (P25).
- **Q3:** after C, excess = mean(A' − P') over resolved events.

For every question the report also gives:
- the counts: events, resolved, unresolved, truncated, dropped candles, and
  "lower low before turn";
- each figure for bull and bear separately.

The p-values come from the block bootstrap (P28) and are corrected with BH
across the family (P29).

## 5. Machinery check (before dev; a fail stops the study)

The same code runs on a simulated driftless random walk, with no real
outcome read:
- 1-min Gaussian log returns on the dev period's real timestamps (the same
  closures);
- per-minute σ = the dev period's HAR σ_240 / √240 at that minute.

Reading σ is not reading an outcome. The run reports:
- the excess per bucket, over ≥ 20 × the dev event count;
- the CI coverage of 0 over 200 dev-sized samples;
- the Q2 random-walk share that P25 needs.

INFERRED: discreteness (1-min steps) can bias the excess slightly, and this
check measures how much. P33 is the gate.

## 6. Procedure

1. **The owner approves §0.** This file is committed as the PREREG.
2. **Counts only:** events per bucket and period, with no outcome. If a
   bucket can't reach P30 on dev, stop and report.
3. **Machinery check (§5).**
4. **Dev:** Q1–Q3. Fix the buckets by P31.
5. **Holdout, once:** only the dev-fixed buckets for Q1, plus Q2 and Q3.
   Verdict by P32. The ATR line (P36) is computed in the same run.

The attempt is recorded before step 4 reads an outcome. No step is rerun
without a new, recorded attempt.

## 7. Verdict mapping

| Result | Verdict |
|---|---|
| *(replaced by §11.4; kept for the record)* | |
| P33 fails | STOPPED at machinery |
| no bucket fixed on dev (P31) | NOT SUPPORTED on dev; holdout unread |
| P32 holds | SUPPORTED on holdout, for the passing buckets |
| otherwise | NOT CONFIRMED on holdout |

Q2 and Q3 are reported with their BH p-values. They do not enter the pass
rule, and neither does the ATR robustness line (P36).

## 8. After the verdict

- **If P32 passes:** the next confirmation is the sealed forward period (from
  2026-10-01), under its own pre-registration. The backfill is part of this
  holdout (P35), not a separate confirmation.
- **ATR robustness (P36):** reported with the holdout result, whatever it shows.
- **Inspection gallery (P37):** dev only; never on holdout.

## 9. Attempt counter

`ATTEMPTS.json` counts two things:
- **`prereg_attempts`:** registrations of this question. It is set to 1 by
  this draft.
- **`outcome_analyses`:** 0. Nothing has been run, so I did not count an
  analysis (P34).

## 10. Inspection gallery (P37)

**Purpose:** finding bugs and understanding the events, on the **dev period
only** (2016–2023). The holdout is never plotted.

**One chart per event:**
- 4h candles from 20 candles before the swing low to up to 30 candles after
  the outcome;
- the swing low and the last lower high (LH), each marked at the candle where
  it was **confirmed**, not where it occurred;
- the pullback peak, with its depth in σ (P10);
- the turn candle;
- the outcome label: A, B or C;
- the baseline probability P at the turn;
- the HAR σ band at the turn: turn close ± 1σ, with σ_240 as of the turn
  close (pinned model, P9).

**Sampling** (seed **20261037**, numpy `default_rng`):
- **Per direction (bear, bull):** 20 random events per outcome label (A, B,
  C), so 60 charts per direction. A label with fewer than 20 events shows
  all of them.
- **20 audit events,** pooled over both directions:
  - the 10 with the largest clock-time gap between the swing candle and the
    candle that confirms it (k = 3 candles, so the gap is larger only across
    weekends, holidays or dropped candles);
  - 10 drawn with the seed from the events whose label changes when W moves
    by ±1 candle (W = 4 or 6).

  An event already in the per-label sample may be drawn again here.

**Output:**
- One local HTML page plus PNGs under `research/pullback_structure/gallery/`.
  This is **git-ignored**, because the charts show real prices.
- Only the generator script and its test are committed. The test uses
  synthetic bars.

**Use:**
- A bug fixed through the gallery is allowed, and is logged in
  `DECISIONS.md`.
- **Any parameter change motivated by the gallery counts as a new attempt on
  dev,** and the counter is incremented (§9).
- The gallery never decides pass or fail; the holdout does.

## 11. Amendment 1 (2026-10-27)

**Decided by the owner on 2026-10-27, before any outcome was read**
(`ATTEMPTS.json`: `outcome_analyses` 0).

**Reason:** the depth-bucket edges (P11) did not fit the structure of a
pullback. Step 2 (§6) stopped: the dev turns per bucket were 0 / 0 / 1 / 154
(`results/counts.json`).
- A swing needs k = 3 confirming candles, and the turn comes only after them.
  So a pullback spans at least 4 candles, and its depth was never below
  1.38 σ (median 3.94 σ).
- These depth figures are descriptive. Depth is not an outcome.

**Seen before this amendment** (counts only; no outcome, baseline or label):
- the step 2 counts;
- the depth quantiles;
- the turn counts of six timeframe, k and swing variants
  (`results/variant_counts.json`). The owner kept the original variant: 4h,
  k = 3, 3 swings.

### 11.1 Q1 (replaces P11, P31, P32)
- **One pooled test on all turns.** Statistic: the mean over events of
  (A − P), where A is the event's outcome indicator (P12) and P is its
  baseline (§1).
- **Inference:** block-bootstrap CI as in P28, one-sided p as in P27.
- **Depth terciles:** cut-points are the 1/3 and 2/3 quantiles of dev depth.
  They are **descriptive only**: no test, not in the BH family. The holdout
  uses the dev cut-points.

### 11.2 BH family and minimum events (replaces P29, P30)
- **BH family:** Q1 (pooled), Q2, Q3, with BH q = 0.05, on dev and on holdout.
- **Minimum events:** a question is tested only with ≥ 50 resolved events
  (P30). With fewer, its verdict is INCONCLUSIVE (too few).
- **Q2 and Q3** are unchanged. Each result is reported with its event counts:
  events, resolved, unresolved, truncated.
- **Step 2's gate (§6.2)** becomes: at least 50 pooled turns on dev (155 at
  step 2, so the gate passes).

### 11.3 The random-walk check (replaces the bucket form of P33)
- **Pass:** pooled Q1 |excess| ≤ 1 pp over all simulated events, and the 95 %
  CI covers 0 in 93–97 % of the 200 dev-sized samples.
- The terciles are reported for description only.
- A fail stops the study before dev.

### 11.4 Verdicts (replace §7)
Applied to Q1 on dev, and on holdout if it runs:
- **PASS:** excess ≥ 5 pp, the 95 % CI excludes 0 (lower bound > 0), and BH
  p < 0.05. The BH part is kept from P32, so the pass rule is unchanged.
- **FAIL:** the 95 % CI excludes +5 pp (upper bound < 5 pp).
- **INCONCLUSIVE:** neither PASS nor FAIL, or too few events. It is
  **reported as INCONCLUSIVE, never as a null and never as a pass.**

**The holdout runs only if dev is PASS** (as P31). Otherwise dev's verdict is
the study's, and the holdout stays unread. A failed random-walk check is still
STOPPED at machinery.

### 11.5 ATR robustness (replaces the bucket form of P36)
On holdout only, the depth terciles are also reported with depth in ATR(14)
units (Wilder, as of the confirmation close). The tercile cut-points come from
dev in each unit. The pooled Q1 statistic doesn't depend on the depth unit, so
this line is descriptive only.

### 11.6 Power (stated before any outcome)
**Smallest excess detectable with 80 % power,** for a 95 % CI that excludes 0:
MDE = (z₀.₉₇₅ + z₀.₈₀) × sd(A − P) / √n.
- No outcome or baseline is used, so sd(A − P) is bounded. Given P,
  Var(A − P) = P(1 − P) ≤ 0.25 under the null, so sd ≤ 0.5.
- **Dev, n = 155 turns:** MDE ≤ 2.80 × 0.5 / √155 = **11.2 pp**.
- **Holdout:** n estimated from the dev turn rate is 155 / 8.0 years ×
  2.75 years = **53 turns** (`results/variant_counts.json`), so MDE ≤
  **19.2 pp**.

INFERRED:
- The resolved events will be fewer than the turns (unresolved events drop
  out), and the month-block bootstrap can widen the CI. Both raise the MDE.
- The 5 pp pass threshold is below both MDEs, so a true excess of 5–11 pp on
  dev is more likely to come out INCONCLUSIVE than PASS. On a holdout of about
  53, anything under about 19 pp is likely INCONCLUSIVE.
- About 53 holdout turns is close to the 50-event minimum, so the holdout
  could be INCONCLUSIVE for too few events.

### 11.7 Attempt counter
`ATTEMPTS.json` records this amendment (`amendments` = 1). `prereg_attempts`
stays 1, because this is the same registration amended. `outcome_analyses`
stays 0.
