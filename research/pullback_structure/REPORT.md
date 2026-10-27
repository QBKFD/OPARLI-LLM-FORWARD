# pullback_structure: report

## Status: STOPPED at step 2 (event counts), before the random-walk check and before any outcome

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
- Implementation choices: `DECISIONS.md` (D1–D11).
- Not run: the random-walk check (§5), dev (step 4) and the gallery (P37).
