# breakout_fade: pre-registration (DRAFT, awaiting the owner's approvals)

**Status: DRAFT, 2026-10-28.** Written before any code for this study, and
before any XAGUSD bar was loaded or looked at by anyone. Nothing below has been
computed on silver. The study may not load data until every item in §0 is
APPROVED. The commit that merges this file into `main` with no OPEN item is the
pre-registration.

**Labels.**
- **PROPOSED (owner):** a value from the owner's brief, waiting for approval.
- **PROPOSED (Claude):** a value the brief doesn't give, which I propose.
- **OPEN:** a value I can't propose without a source.
- **CITED** means file:line on `main` at d19fbdc. **INFERRED** means my
  reasoning, not a measurement.

## 0. Choices to approve

| id | item | value | status |
|---|---|---|---|
| B1 | Data | `database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet` (manifest sha256 `49ceaa39…5bfb`), read through `research/data_guard.py` (this file names it) and `sealing.assert_period("XAGUSD", ts, "dev")` | PROPOSED (owner) |
| B2 | Period | silver **dev 2016-01-01 .. 2023-12-31, read once**, with 2015 not available (the file starts 2016-01-04), so the first weeks serve as warm-up: events from the first confirmed swing with a σ | PROPOSED (owner: dev; Claude: warm-up) |
| B3 | Definitions | **identical to `research/pullback_structure`, Amendment 2 plus D18 and D19:** 1h UTC candles (≥ 50 % of minutes), k = 3, 2 swings, LH, X = 1.0 σ, W = 5 candles, the 30-candle horizon, the B/C labels, P18, P24. **The code is `research/pullback_structure/pullback.py` at commit 642c280 (#50), unchanged.** #50 must be merged before step 2 | PROPOSED (owner; Claude: the pin) |
| B4 | **σ model** | **no silver-specific pinned fit exists** (CITED `backend/datafeed/features/vol_block.py:334-337`: `txt2007_2023` and `parquet2021_2023`, both gold). Proposed: **gold's pinned `parquet2021_2023`** (SHA-256 `dc0eb3c5…0dde`) on silver's own 1-min bars. L, S_hd and T come from silver's returns, but β, c and the seasonal profile are gold's. Alternative: fit a silver HAR on silver 2016–2019, pinned before step 2 (in-sample for half of dev) | **PROPOSED (Claude), decision for the owner** |
| B5 | H1 | the share of Q2 breaks that become B, **below** the random walk's B share. The random walk runs on the same silver timestamps, with per-minute sd = silver's σ_240 / √240 (pullback_structure §5, D12) | PROPOSED (owner; Claude: the σ of the walk) |
| B6 | H1 test | one-sided: the difference (silver share − random-walk share) < 0. p = the share of month-block resamples with difference ≥ 0, +1-corrected. The random-walk share is pooled over 200 paths (its sampling error is ignored, as P25) | PROPOSED (owner: one-sided; Claude: mechanics) |
| B7 | H2 trade | after a close beyond LH (bear: above; bull: mirror), **enter against the break at that close**. **Target = the swing low** (bull: the swing high). **Stop = entry + 1.0 σ** (bull: −). σ = σ_240 as of the break candle's close (as P14) | PROPOSED (owner; Claude: the σ time) |
| B8 | H2 outcome | first passage on 1-min bars from the minute after the break candle: **hit** = a 1-min low ≤ target before a 1-min high ≥ stop (mirror for bull). A single bar holding both counts as **the stop** (against the hypothesis, as P13). Neither within 30 candles: unresolved, excluded and counted | PROPOSED (Claude) |
| B9 | H2 statistic | baseline P = stop_dist / (stop_dist + target_dist) per event, the driftless chance of the target first. Statistic: **mean(hit − P)**, one-sided **> 0** (the fade wins more often than the baseline). Month-block bootstrap | PROPOSED (owner) |
| B10 | H2 spread line | a separate, descriptive line: mean net payoff in σ units, hit × target_dist/σ − (1 − hit) × stop_dist/σ − spread/σ per event (unresolved events excluded). It is not in the BH family or the verdict | PROPOSED (owner: separate line, σ units; Claude: the payoff form) |
| B11 | **Spread value** | **OPEN.** No measured XAGUSD spread is in the repo. Options: (a) measure it from IBKR BID/ASK 1-min bars for XAGUSD over a short dev window (a download the owner approves); (b) a provisional proportional value such as `level_target`'s 1.0 bp (CITED `research/level_target/DECISIONS.md:16`, T7), which is gold's and likely too small for silver (INFERRED) | **OPEN** |
| B12 | BH | across H1 and H2, q = 0.05 | PROPOSED (owner) |
| B13 | Verdict (H1) | **PASS:** difference < 0, 95 % month-block CI excludes 0, BH p < 0.05. **FAIL:** the CI's lower bound is above −2 pp. **INCONCLUSIVE:** anything else, or < 50 breaks (reported as INCONCLUSIVE, never as a null or a pass). H2 is reported with its BH p and doesn't enter the verdict | PROPOSED (owner; Claude: the 50-event floor, as P30) |
| B14 | Bootstrap | calendar-month blocks, 10,000 resamples, seed 20261029, 95 % percentile CI | PROPOSED (Claude) |
| B15 | **Random-walk check first** | the pipeline on 200 random-walk paths on the silver dev timestamps (B5), no real outcome. **Pass:** H2 pooled \|mean(hit − P)\| ≤ 1 pp over all simulated events; its 95 % CI covers 0 in 93–97 % of the 200 paths; and for H1, each path's B share against the pooled share has a CI covering 0 in 93–97 % of paths. A fail stops the study | PROPOSED (owner: the check, same tolerances; Claude: the H1 form) |
| B16 | Short days | trade dates (New York, the session from 18:00) with **< 1,000 one-minute rows** are short. An event is excluded if its break candle, its W window or its H2 horizon touches one; the count is reported | PROPOSED (owner: about 1,000; Claude: the exact rule) |
| B17 | Seam | **XAGUSD has none**: one source (IBKR MIDPOINT, `docs/MULTI_MARKET_DATA.md`) for 2016–2026. Gold's seam (OD2) applies to the gold replication | PROPOSED (Claude) |
| B18 | Step 2 (counts) | after the merge and before the random walk: silver break counts on dev (no outcome), which refresh §5's power. Stop if there are < 50 breaks | PROPOSED (Claude) |
| B19 | Attempt counter | `ATTEMPTS.json`: `prereg_attempts` 1, `outcome_analyses` 0; incremented before H1/H2 read an outcome | PROPOSED (owner) |
| B20 | Not part of this study | each replication gets its own later PREREG: gold holdout (2024-01-01 .. 2026-09-30, OD2 seam rule), silver holdout (same window), EURUSD dev. Nothing from 2026-10-01 is read; no gold bar is read here | PROPOSED (owner) |

## 1. Origin and why silver

`research/pullback_structure` Q2 on gold dev 2016–2023 (`REPORT.md` §1, §3):
- **The finding:** after a close beyond the last lower high, the +1 σ
  follow-through within 5 candles came less often than on a driftless random
  walk with the same σ: B share 29.0 % against 34.4 % (668 breaks; BH p 0.0024).
- **Robust in W:** −5.5 / −5.4 / −6.2 pp at W = 4 / 5 / 6.
- **Not evidence:** it was found on gold dev, so gold dev can't confirm it.
  Silver dev has never been read for this idea, or by any study.

## 2. Hypothesis

On 1h silver, after a close beyond the last swing high in a bear structure
(mirrored for bull), follow-through is weaker than on a random walk with the
same σ: **H1**. Fading the break, with the swing low as the target and a 1 σ
stop, reaches the target more often than its driftless baseline: **H2**.

## 3. Procedure

1. **The owner approves §0.** This file is merged as the PREREG, and #50 is
   merged (B3).
2. **Counts only (B18).** Breaks on silver dev, per direction, plus the
   short-day exclusions (B16). No outcome.
3. **The random-walk check (B15).** A fail stops the study.
4. **Dev, once.** `ATTEMPTS.json` is incremented first. Then H1, H2, BH and
   the verdict (B13), the spread line (B10), and the counts next to every
   result: breaks, B, C, unresolved, excluded short-day events.
5. **The replications (B20)** are not part of this file.

## 4. What is reported

- **H1:** silver's B share, the random walk's, the difference with its CI, and
  the raw and BH p.
- **H2:** mean(hit − P) with its CI and the raw and BH p; the hit rate and mean
  P; the resolved, unresolved and excluded counts; the spread line (B10).
- **Descriptive** (no test): bull and bear apart; W = 4 and 6 relabels (as
  pullback_structure item 11); the average 5- and 30-candle move after a break,
  in σ, against the random walk. These are counts and σ units only, with no
  price.

## 5. Power (stated before any silver data)

Silver's break count isn't known: no silver bar has been read. **Stand-in:**
gold dev's rate under the same code, 668 breaks over 8 years
(`research/pullback_structure/results/counts_a2.json` after D18–D19, on #50 at 0875a0f; the copy on `main` is from before the fixes). Step 2
replaces it with silver's own count before any outcome.

- **H1** (80 % power, 95 % CI excluding 0; B share near the random walk's
  0.344): MDE = 2.80 × √(0.344 × 0.656 / n). **n = 668 gives 5.2 pp.**
- **H2** (bound sd(hit − P) ≤ 0.5): MDE ≤ 2.80 × 0.5 / √n. **n = 668 gives
  5.4 pp.**

INFERRED:
- Gold's effect, −5.4 pp, sits at about the H1 MDE. A silver effect of the same
  size has about 80 % power before the month-block bootstrap widens the CI.
- A smaller effect would most likely come out INCONCLUSIVE.
- Silver could also have fewer breaks than gold.
