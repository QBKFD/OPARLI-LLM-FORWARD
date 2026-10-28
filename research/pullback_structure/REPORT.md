# pullback_structure: report

## 1. Verdict (PREREG §11.4, Amendment 2 settings, after bug fixes D18–D19): **FAIL on dev**. The holdout stays unread

**Outcome analysis 2** (`ATTEMPTS.json`): a dev rerun after two bugs found by
the owner's exhaustive audit (§2) were fixed and logged (DECISIONS D18–D20).
Attempt 1's outputs are kept as `results/*_attempt1.json`.

All OBSERVED: `results/dev.json` and `results/rw.json` (attempt 2), and
`results/dev_attempt1.json` and `results/rw_attempt1.json`.

| | attempt 1 (before the fixes) | **attempt 2 (after D18–D19)** |
|---|---|---|
| events started on dev | 2,424 | 2,151 |
| **Q1** excess (lower-low rate − baseline) | −2.3 pp [−4.7, +0.1], n 1,256 | **−2.1 pp [−4.4, +0.2]**, n 1,271 (21 unresolved) |
| Q1 rate A / mean P | 48.7 % / 51.0 % | 48.9 % / 51.0 % |
| Q1 verdict | FAIL | **FAIL** (the CI's upper bound is below +5 pp) |
| **Q2** share B | 28.1 % (272 of 967) | **29.0 % (194 of 668)**, CI [25.8, 32.3] % |
| Q2 random-walk share | 34.0 % | **34.4 %** |
| Q2 p (two-sided) raw / BH | 0.0002 / 0.0006 | **0.0008 / 0.0024** |
| **Q3** excess after C | −4.9 pp [−8.0, −1.8], n 556 | **−4.5 pp [−8.2, −0.9]**, n 384 (48 unresolved; 42 left Q3 with a lower low inside W) |
| Q3 p one-sided raw / BH | 0.999 / 1.00 | 0.992 / 0.99 |
| random walk: pooled excess / coverage of 0 | −0.44 pp / 93.5 % | −0.44 pp / 94.0 % (PASS) |

**What the fixes changed:**
- **Q1 barely moved.** The fixed events are mostly Q2 events.
- **Q2 lost 299 breaks:** the 300 that came before the event existed, less
  any made up by new starts. Its result holds after the fix. The B share is
  still below the random walk's, at 29.0 % against 34.4 %, and BH-significant.
- **Q3** shrank with Q2.

INFERRED: D19's look-ahead didn't create the Q2 effect.

## 2. The owner's audit of all dev events (`audit.py`; dev 2016–2023 only)

Each row gives the count, the share and up to 5 example candle indices.

| # | check | before the fixes (`audit_dev_attempt1.json`) | after (`audit_dev.json`) | verdict |
|---|---|---|---|---|
| 1 | Q2 break (close beyond LH) before the swing low is confirmed | **300 of 967 (31.0 %)**; 77 B, 223 C; e.g. breaks at 3326, 3505, 3573, 4103, 4976 | 0 of 668 | **bug, fixed (D19)** |
| 2 | structure: the last 2 swing lows or the last 2 confirmed swing highs not falling | **25 of 2,424 (1.0 %)**, all outside candles (the swing-low candle is also a swing high, here above the previous one); 55 outside candles in all, e.g. 3595, 5207, 6222, 6699, 7042 | 0 of 2,151; 57 outside candles kept, each with a falling high, e.g. 3595, 4781, 5207, 5697, 6385 | **bug, fixed (D18)** |
| 3 | near-ties: the last 2 swing highs or lows within 0.05 σ | 183 of 2,424 (7.5 %); 86 highs, 101 lows | 152 of 2,151 (7.1 %); e.g. 4610, 6099, 6107, 7715, 8543 (before) | definition, not changed |
| 4 | Q1 event with a close beyond LH during the confirmation | 0 of 1,277 (such events went to Q2 under D3); the event near 41324 (bear, swing 41324, conf 41327, turn 41329, A) has none | 0 of 1,292 | no bug |
| 5 | same-candle A: the candle that decides A also closes beyond the pullback high | 8 of 612 A (1.3 %); e.g. 9436, 21061, 29372, 46481, 47806 | 8 of 622 (1.3 %) | definition, not changed (the order inside the candle comes from the 1-min bars, P13) |
| 6 | the σ behind the depth | σ_240 at the label equal to the **confirmation** candle's close (P9, D10). The gallery's ±1σ band uses the **turn** candle's close (§10). Over Q1 events the two differ by a median of 16 % (90th percentile 47 %). Event near 15381 (bear, swing 15371, conf 15374, turn 15383, A): σ at conf 3.878, σ at turn 5.409 (price units); depth 3.12 σ as used, 2.24 σ with the turn's σ | same | as specified, not a bug; Q1 is pooled, so depth enters only the descriptive terciles |

## 3. The owner's audit, items 7, 10 and 11 (`audit2.py`; dev 2016–2023, after D18–D19; descriptive)

OBSERVED: `results/audit2_dev.json`. Counts, shares and candle indices only.
No test, so no outcome analysis was counted.

**7. Data gaps inside the W window.** A gap here means two consecutive 1h
candles in the 5 candles after the break that are more than an hour apart:
the weekend, a holiday, or a dropped candle.
- **108 of 668 breaks (16.2 %)** have one. Examples: breaks at 3308, 3399,
  3630, 3924, 4200.
- **29 of the 194 B labels** have a gap in their window. 13 of those reached
  the B target after the gap.
- **B share:** 26.9 % with a gap, 29.5 % without.
- The random walk uses the same timestamps, so it has the same gaps. It has
  no weekend jump in price, though: its paths continue across closures.

**10. What "event end" marks for a C event.** It marks the **Q3 resolution**,
not the end of W: the first 1-min bar that passes the swing low or H, or the
close of c_fix + 30 when Q3 is unresolved.
- **428 of 474 C events** end at their Q3 resolution, or at the horizon.
- **46 end exactly at the close of W:**
  - 42 left Q3 with a lower low inside W (`ll_during_w`);
  - 4 had Q3 decided in the first minute after W.

| near | event | break | C fixed at the close of | event end | Q3 | end marks |
|---|---|---:|---:|---:|---|---|
| 4565 | bear, swing 4560 | 4565 | 4570 | 4571 | the high H first | Q3 resolution |
| 4763 | bear, swing 4753 | 4763 | 4768 | 4769 | the high H first | Q3 resolution |
| 7620 | bear, swing 7607 | 7620 | 7625 | 7632 | the high H first | Q3 resolution |

**11. W sensitivity of Q2.** Every break is relabelled with W = 4, 5 and 6
candles and the same +1σ target. The event population is the W = 5 run's.

| W | gold: share B (n 668) | random walk: share B (n ≈ 140,600, 200 paths) | gold − random walk |
|---|---:|---:|---:|
| 4 | 24.0 % | 29.5 % | **−5.5 pp** |
| 5 | 29.0 % | 34.4 % | **−5.4 pp** |
| 6 | 32.2 % | 38.4 % | **−6.2 pp** |

The gap of about −5 pp holds at every W (INFERRED: it isn't an artefact of
W = 5). This is descriptive only, with no test at W = 4 or 6. The tested
Q2 result stays the one in §1.

## The original step 2: STOPPED (before the amendment)

PREREG §6.2: "If a bucket can't reach P30 on dev, stop and report." Three of
the four depth buckets (P11) have fewer than 50 turns (P30) on dev,
2016-01-01 .. 2023-12-31. No outcome has been read, and `ATTEMPTS.json`
`outcome_analyses` is still 0.

**Turns per depth bucket on dev** (OBSERVED, `results/counts.json`):

| | [0, 0.5) σ | [0.5, 1.0) σ | [1.0, 1.5) σ | ≥ 1.5 σ |
|---|---:|---:|---:|---:|
| bear | 0 | 0 | 0 | 76 |
| bull | 0 | 0 | 1 | 78 |
| pooled | 0 | 0 | 1 | 154 |

**Other counts:**
- 277 events started (137 bear, 140 bull), 1 truncated, 103 breaks of LH (Q2).
- Candles: 13,093 kept, 15 dropped (P3), 52 bars in the 17:00–18:00 NY hour.

**Why the low buckets are empty** (OBSERVED, descriptive; no outcome):
- σ has the right scale. The median 4h candle range is 1.21 σ_240, and the
  median 4h close-to-close move is 0.48 σ_240.
- Pullback depths on dev range from 1.38 σ (minimum) to 3.94 σ (median) and
  7.48 σ (95th percentile).
- From the swing low to the turn takes 4 to 21 candles (median 6).

INFERRED: the low buckets are empty by construction. A swing low needs k = 3
higher-low candles after it (P4, P5), and the turn can come only after them
(P8). So pullback_high spans at least 4 candles' highs above the swing low,
and a span of 4+ candles is rarely under 1.5 σ_240. The cut-points in P11
suit σ of one candle, not a pullback of several.

**Also for the owner** (INFERRED): there are 155 turns in all. Any split into
four buckets leaves at least one bucket under 50, so P30 can't hold for four
buckets on this sample. Three buckets would just reach 50 each, and two
buckets would leave room.

## What is built
- The pipeline: `pullback.py` (candles, swings, the state machine, the
  statistics, the random walk), `data.py`, `common.py`, `params.py` (every
  value with its PREREG id).
- `run_counts.py` (step 2).
- Synthetic tests: `test_pullback.py`.
- Implementation choices: `DECISIONS.md` (D1–D20; D3 replaced by D19).
- `run_variants.py`: the owner's count-only comparison
  (`results/variant_counts.json`).
- `run_rw.py`, `run_dev.py`, `gallery.py`: written and tested on synthetic
  bars only. Not run.
