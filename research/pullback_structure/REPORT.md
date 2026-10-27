# pullback_structure: report

## 1. Verdict (PREREG §11.4, Amendment 2 settings): **FAIL on dev**. The holdout stays unread

Q1 asks whether, after the turn, a new lower low comes more often than the
random-walk baseline P. The answer on dev 2016–2023 is **no**:
- the excess is **−2.3 pp**, with a 95 % CI of [−4.7, +0.1] pp;
- the CI's upper bound is below +5 pp, which is **FAIL** by §11.4;
- the point estimate even goes the other way: a new lower low came 48.7 % of
  the time, against a mean baseline of 51.0 %.

By §11.4 the holdout runs only after a dev PASS, so 2024-01 .. 2026-09 stays
unread. This was outcome analysis 1 (`ATTEMPTS.json`), run once.

All OBSERVED: `results/dev.json`, `results/rw.json`.

| | n | statistic | 95 % CI | p (raw) | p (BH) |
|---|---:|---:|---|---:|---:|
| **Q1** lower-low rate − baseline (pooled) | 1,256 resolved of 1,277 turns (21 unresolved) | **−2.3 pp** | [−4.7, +0.1] | 0.97 (one-sided) | 1.00 |
| **Q2** share of LH breaks that become B | 967 breaks (272 B, 695 C) | **28.1 %**, against 34.0 % on the random walk | [25.5, 30.7] % | 0.0002 (two-sided) | 0.0006 |
| **Q3** after C: lower-low rate − baseline | 556 resolved of 623 C (67 unresolved; 72 left Q3 with a lower low inside W) | **−4.9 pp** | [−8.0, −1.8] | 0.999 (one-sided) | 1.00 |

**Q2 is the only BH-significant result, and it goes against a "confirmed
reversal" story.** After a close beyond the last lower high, the +1σ
follow-through within 5 candles came less often than on a driftless random
walk with the same σ. Q2 is reported, but it isn't part of the pass rule.

**Descriptive only** (no test):
- **Q1 by depth tercile** (cuts 1.48 σ and 2.12 σ): −1.6 pp (n 426),
  −3.9 pp (n 425), −1.3 pp (n 405).
- **By direction:**
  - Q1: bear −3.7 pp (n 622), bull −0.9 pp (n 634);
  - Q2 B share: bear 29.1 %, bull 27.1 %;
  - Q3: bear −4.7 pp, bull −5.1 pp.
- **Event kinds:** 2,424 events, 0 truncated.
  - Q1 path: A 612, lower low before the turn 505, lower low after Q1
    resolved 211, no break 108, unresolved 21.
  - Breaks: B 272, C 695.
  - Breaks came 642 times during the pullback and 325 times after the turn.

**Machinery check (§5 / §11.3): PASS**, `results/rw.json`:
- 200 random-walk runs on the dev timestamps, with 249,002 resolved simulated
  events;
- pooled excess −0.44 pp (tolerance ±1 pp);
- the CI covered 0 in 93.5 % of runs (band 93–97 %, near its lower edge);
- by tercile: −1.2 / −0.4 / −0.1 pp. INFERRED: a small negative bias from
  1-min discreteness, largest for shallow pullbacks. It is about a tenth of
  dev's effect size, and dev's sign is the same.

**Gallery (P37):** 140 charts of dev only, under
`research/pullback_structure/gallery/` (local, git-ignored). A spot-check of
an A event in each direction found no bug: the swing and LH are marked at
their confirmations, P and the ±1σ band sit at the turn, and the end is where
price passes the swing level.

INFERRED: on 1h gold in 2016–2023, a turn after a pullback in a 1-swing
structure gives no edge for continuation beyond the random-walk baseline. The
point estimates sit slightly below it.

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
- Implementation choices: `DECISIONS.md` (D1–D17).
- `run_variants.py`: the owner's count-only comparison
  (`results/variant_counts.json`).
- `run_rw.py`, `run_dev.py`, `gallery.py`: written and tested on synthetic
  bars only. Not run.
