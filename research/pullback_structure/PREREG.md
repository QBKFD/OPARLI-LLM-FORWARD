# pullback_structure: pre-registration (DRAFT, awaiting the owner's approvals)

**Status: DRAFT, 2026-10-27.** Written before any code, and before any price
data was loaded or looked at for this study. Nothing below has been computed.
The study may not load data until every item in §0 is APPROVED (or replaced by
the owner). After approval, this file is committed again, and that commit is
the pre-registration.

**Labels.**
- **PROPOSED (owner):** a value from the owner's brief, waiting for the
  owner's approval.
- **PROPOSED (Claude):** a value the brief doesn't give, which I propose.
- **OPEN:** a value I can't propose without a source.
- **CITED** means file:line on `main` at 2b1950d. **INFERRED** means my
  reasoning, not a measurement.

## 0. Choices to approve

| id | item | value | status |
|---|---|---|---|
| P1 | Timeframe | 4h candles built from 1-min bars | PROPOSED (owner) |
| P2 | Candle anchor | New York time: 18–22, 22–02, 02–06, 06–10, 10–14, 14–17 (the last candle is 3h, ending at the daily break) | PROPOSED (Claude) |
| P3 | Incomplete candles | a candle with < 50 % of its scheduled open minutes is dropped, and counted | PROPOSED (Claude) |
| P4 | Swing confirmation | k = 3 candles each side. A swing exists only once the k-th candle after it has closed | PROPOSED (owner) |
| P5 | Swing tie rule | swing low: the candle's low is strictly below the lows of the k candles on each side (mirror for highs); ties make no swing | PROPOSED (Claude) |
| P6 | Bearish structure | the last 3 confirmed swing highs are strictly descending (2 lower highs), **and** the last 3 confirmed swing lows are strictly descending (2 lower lows), the newest being the swing low that starts the event | PROPOSED (Claude: my reading of "≥ 2 lower highs and ≥ 2 lower lows") |
| P7 | Last lower high (LH) | the newest confirmed swing high before the event's swing low | PROPOSED (Claude) |
| P8 | Turn | the first 4h close below the previous candle's low, after the swing is confirmed (mirror for bull) | PROPOSED (owner) |
| P9 | σ | HAR σ over 240 min, `sigma_over(model, state, 240).sigma_px`, pinned model `parquet2021_2023` (SHA-256 `dc0eb3c5…0dde`, CITED `backend/datafeed/features/vol_block.py:336-337`). State as of the close of the confirmation candle | PROPOSED (owner: pinned 240-min model; Claude: the as-of time) |
| P10 | Pullback depth | (pullback_high − swing_low) / σ, at the turn | PROPOSED (owner) |
| P11 | Depth buckets | [0, 0.5), [0.5, 1.0), [1.0, 1.5), ≥ 1.5 σ | PROPOSED (owner) |
| P12 | **Q1 outcome** | **first passage:** A = a 1-min low below swing_low before a 1-min high above pullback_high. This is the event the baseline P is exact for (§3.6) | PROPOSED (Claude), see §3.7 |
| P13 | Order within a candle | resolved on the 1-min bars; a single 1-min bar that holds both levels counts **against** the hypothesis (not A) | PROPOSED (Claude) |
| P14 | B | a 4h close above LH, then a high ≥ that close + X·σ within W candles; σ as of the breaking close | PROPOSED (owner: X, W; Claude: the reference price and σ time) |
| P15 | X, W | X = 1.0 σ, W = 5 candles | PROPOSED (owner) |
| P16 | C | a 4h close above LH that does not become B within W candles. C is fixed when the W-th candle closes | PROPOSED (owner, Claude: the time C is fixed) |
| P17 | Max horizon | 30 candles from the turn (Q1), from the break (Q2) and from the moment C is fixed (Q3); otherwise "unresolved", counted and reported | PROPOSED (owner) |
| P18 | Overlapping events | per direction, at most one live event; a new event can start only after the previous one is resolved | PROPOSED (Claude) |
| P19 | Bull and bear | the bull side is the mirror. Both are pooled for the tests and also reported apart (descriptive) | PROPOSED (Claude) |
| P20 | Costs | no Q1–Q3 metric is a return, so no spread is deducted. A trading rule built on a result would need its own PREREG with costs | PROPOSED (Claude) |
| P20a | Spread value, if P20 is rejected | no measured XAUUSD spread for 2016–2023 is in the repo. A provisional proportional cost exists: 1.0 bp spread (CITED `research/level_target/DECISIONS.md:16`, T7) | OPEN |
| P21 | Data | `database/ohlcv_data/XAUUSD.txt` (manifest sha256 `dc2d30cd…abfb9`), 1-min, UTC. Events from 2016-01-01; warm-up from 2015-07-01 for structure and the HAR state | PROPOSED (owner: from 2016; Claude: source and warm-up) |
| P22 | Dev period | 2016-01-01 .. 2020-12-31 | PROPOSED (Claude), §2 |
| P23 | Holdout period | 2021-01-01 .. 2023-12-31 (σ in sample there, §2) | PROPOSED (Claude), §2 |
| P24 | Events at a period end | an event whose horizon runs past its period's end is dropped, and counted ("truncated") | PROPOSED (Claude) |
| P25 | Q2 null | Q2 needs a p-value for the BH family. The null is B / (B + C) from the same pipeline run on the simulated random walk (§5) | PROPOSED (Claude) |
| P26 | Q3 baseline | at the moment C is fixed: P = (H − close) / (H − swing_low), with H = the highest high since the swing low | PROPOSED (Claude) |
| P27 | Tests | Q1 and Q3: one-sided, excess > 0. Q2: two-sided against the P25 null | PROPOSED (Claude) |
| P28 | Bootstrap | block bootstrap of events by calendar month, 10,000 resamples, seed 20261027; 95 % percentile CI | PROPOSED (owner: block bootstrap, 95 %; Claude: block, B and seed) |
| P29 | BH family | dev: Q1 × 4 buckets, Q2, Q3 (6 p-values); holdout: Q1 in the dev-fixed buckets, Q2, Q3. BH q = 0.05 | PROPOSED (owner: BH across Q1–Q3; Claude: the family) |
| P30 | Minimum events | a bucket needs ≥ 50 resolved events in a period to be tested, else it's reported as "too few" | PROPOSED (Claude) |
| P31 | Bucket fixed on dev | every bucket with dev excess ≥ 5 pp, 95 % CI excluding 0 and BH p < 0.05. If none: stop at dev, verdict "not supported on dev", and no holdout look | PROPOSED (Claude: my reading of "a depth bucket fixed on dev") |
| P32 | Pass rule | on holdout, at least one dev-fixed bucket has Q1 excess ≥ 5 pp, a 95 % CI excluding 0, and BH p < 0.05 | PROPOSED (owner, Claude: BH p) |
| P33 | Random-walk check | §5. Pass: \|excess\| ≤ 1 pp in every bucket, and the 95 % CI covers 0 in 93–97 % of 200 dev-sized samples. A fail stops the study before dev | PROPOSED (owner: "excess ~0"; Claude: the tolerances) |
| P34 | Attempt counter | §8: `ATTEMPTS.json` records this registration as attempt 1 at the question; outcome analyses stay at 0 | PROPOSED (Claude: my reading of "increment the attempt counter") |
| P35 | 2026-02-01 .. 2026-09-30 | not used by this study (the unread backfill; §2) | PROPOSED (Claude) |

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

**Proposed periods.**
- **Dev 2016–2020 and holdout 2021–2023** (P22, P23): the brief's start, and
  every unspent, downloaded year.
- **σ is in sample on the holdout.** The pinned model was fitted on
  2021–2023, so σ there is in sample. INFERRED: σ only normalises depth (the
  bucket) and X. The fit used realised variance, never swing outcomes, so the
  in-sample part makes σ more accurate on the holdout than on dev. It can't
  have been tuned toward the hypothesis.
- **On dev, σ uses parameters fitted later** (2021–2023 applied to 2016–2020).
  That is a look-ahead in parameters, not in prices. The same applied in
  `sigma_compare`'s frozen check.
- **Alternatives for the owner:**
  1. Fit a HAR on 2007–2015 only for this study, as `level_target` proposed
     (CITED `research/level_target/DECISIONS.md:20`, T11). That departs from
     "pinned 240-min model".
  2. Use 2026-02..09 as the holdout (P35). That data is unread, but it's 8
     months, needs the backfill run on EC2, and needs the timezone check in
     the manifest (`timezone_break`).
- **Source:** `XAUUSD.txt` covers 2016–2023 in one file. The clean parquet
  starts in 2020, and equals the file close for close from 2020 to 2023
  (CITED: manifest, `verified`).

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
   Verdict by P32.

The attempt is recorded before step 4 reads an outcome. No step is rerun
without a new, recorded attempt.

## 7. Verdict mapping

| Result | Verdict |
|---|---|
| P33 fails | STOPPED at machinery |
| no bucket fixed on dev (P31) | NOT SUPPORTED on dev; holdout unread |
| P32 holds | SUPPORTED on holdout, for the passing buckets |
| otherwise | NOT CONFIRMED on holdout |

Q2 and Q3 are reported with their BH p-values. They do not enter the pass
rule.

## 8. Attempt counter

`ATTEMPTS.json` counts two things:
- **`prereg_attempts`:** registrations of this question. It is set to 1 by
  this draft.
- **`outcome_analyses`:** 0. Nothing has been run, so I did not count an
  analysis (P34).
