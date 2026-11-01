# breakout_fade: pre-registration

**Amendment 1 (2026-10-28, owner, before any outcome): §6.** It replaces H2's
statistic (B9) and H2's random-walk check (B15). Rows marked "AMENDED (A1)" are
read as §6 says.

**Status: every choice (B1–B20) APPROVED by the owner, 2026-10-28**, with
B10 and B11 changed: there is no spread value and no download, only a break-even
spread (below). Written before any code for this study, and before any XAGUSD
bar was loaded or looked at. Nothing below has been computed on silver. The
commit that merges this file into `main` is the pre-registration.
**Merge order: #50, #53, then this one.**

**Labels.** Every value in §0 is APPROVED. Which ones came from the owner's
brief and which from Claude is in the draft at commit e210b26.
- **CITED** means file:line on `main` at d19fbdc. **INFERRED** means my
  reasoning, not a measurement.

## 0. Choices (all approved)

| id | item | value | status |
|---|---|---|---|
| B1 | Data | `database/ohlcv_data/XAGUSD_ibkr_1min_20160101_20260930.parquet` (manifest sha256 `49ceaa39…5bfb`), read through `research/data_guard.py` (this file names it) and `sealing.assert_period("XAGUSD", ts, "dev")` | APPROVED (owner, 2026-10-28) |
| B2 | Period | silver **dev 2016-01-01 .. 2023-12-31, read once**, with 2015 not available (the file starts 2016-01-04), so the first weeks serve as warm-up: events from the first confirmed swing with a σ | APPROVED (owner, 2026-10-28) |
| B3 | Definitions | **identical to `research/pullback_structure`, Amendment 2 plus D18 and D19:** 1h UTC candles (≥ 50 % of minutes), k = 3, 2 swings, LH, X = 1.0 σ, W = 5 candles, the 30-candle horizon, the B/C labels, P18, P24. **The code is `research/pullback_structure/pullback.py` at commit 642c280 (#50), unchanged.** #50 must be merged before step 2 | APPROVED (owner, 2026-10-28) |
| B4 | **σ model** | **no silver-specific pinned fit exists** (CITED `backend/datafeed/features/vol_block.py:334-337`: `txt2007_2023` and `parquet2021_2023`, both gold). Approved: **gold's pinned `parquet2021_2023`** (SHA-256 `dc0eb3c5…0dde`) on silver's own 1-min bars. L, S_hd and T come from silver's returns, but β, c and the seasonal profile are gold's. (Not chosen: a silver HAR fitted on 2016–2019.) | APPROVED (owner, 2026-10-28: gold's pinned parquet2021_2023 on silver's bars) |
| B5 | H1 | the share of Q2 breaks that become B, **below** the random walk's B share. The random walk runs on the same silver timestamps, with per-minute sd = silver's σ_240 / √240 (pullback_structure §5, D12) | APPROVED (owner, 2026-10-28) |
| B6 | H1 test | one-sided: the difference (silver share − random-walk share) < 0. p = the share of month-block resamples with difference ≥ 0, +1-corrected. The random-walk share is pooled over 200 paths (its sampling error is ignored, as P25) | APPROVED (owner, 2026-10-28) |
| B7 | H2 trade | after a close beyond LH (bear: above; bull: mirror), **enter against the break at that close**. **Target = the swing low** (bull: the swing high). **Stop = entry + 1.0 σ** (bull: −). σ = σ_240 as of the break candle's close (as P14) | APPROVED (owner, 2026-10-28) |
| B8 | H2 outcome | first passage on 1-min bars from the minute after the break candle: **hit** = a 1-min low ≤ target before a 1-min high ≥ stop (mirror for bull). A single bar holding both counts as **the stop** (against the hypothesis, as P13). Neither within 30 candles: unresolved, excluded and counted | APPROVED (owner, 2026-10-28) |
| B9 | H2 statistic | baseline P = stop_dist / (stop_dist + target_dist) per event, the driftless chance of the target first. Statistic: **mean(hit − P)**, one-sided **> 0** (the fade wins more often than the baseline). Month-block bootstrap | AMENDED (A1, §6) |
| B10 | H2 break-even spread | a separate, descriptive line: **the break-even spread in σ units**, the spread at which H2's mean payoff is zero. Per event, the gross payoff in σ is hit × target_dist/σ − (1 − hit) × stop_dist/σ (resolved events only), so the break-even is their mean. A value ≤ 0 means no spread makes the fade pay. It is not in the BH family or the verdict | APPROVED (owner, 2026-10-28: replaces the spread-deducted line) |
| B11 | Spread value | **none:** no spread value and no download (owner, 2026-10-28). B10's break-even spread replaces it | APPROVED (owner, 2026-10-28) |
| B12 | BH | across H1 and H2, q = 0.05 | APPROVED (owner, 2026-10-28) |
| B13 | Verdict (H1) | **PASS:** difference < 0, 95 % month-block CI excludes 0, BH p < 0.05. **FAIL:** the CI's lower bound is above −2 pp. **INCONCLUSIVE:** anything else, or < 50 breaks (reported as INCONCLUSIVE, never as a null or a pass). H2 is reported with its BH p and doesn't enter the verdict | APPROVED (owner, 2026-10-28) |
| B14 | Bootstrap | calendar-month blocks, 10,000 resamples, seed 20261029, 95 % percentile CI | APPROVED (owner, 2026-10-28) |
| B15 | **Random-walk check first** | the pipeline on 200 random-walk paths on the silver dev timestamps (B5), no real outcome. **Pass:** H2 pooled \|mean(hit − P)\| ≤ 1 pp over all simulated events; its 95 % CI covers 0 in 93–97 % of the 200 paths; and for H1, each path's B share against the pooled share has a CI covering 0 in 93–97 % of paths. A fail stops the study | AMENDED (A1, §6) |
| B16 | Short days | trade dates (New York, the session from 18:00) with **< 1,000 one-minute rows** are short. An event is excluded if its break candle, its W window or its H2 horizon touches one; the count is reported | APPROVED (owner, 2026-10-28) |
| B17 | Seam | **XAGUSD has none**: one source (IBKR MIDPOINT, `docs/MULTI_MARKET_DATA.md`) for 2016–2026. Gold's seam (OD2) applies to the gold replication | APPROVED (owner, 2026-10-28) |
| B18 | Step 2 (counts) | after the merge and before the random walk: silver break counts on dev (no outcome), which refresh §5's power. Stop if there are < 50 breaks | APPROVED (owner, 2026-10-28) |
| B19 | Attempt counter | `ATTEMPTS.json`: `prereg_attempts` 1, `outcome_analyses` 0; incremented before H1/H2 read an outcome | APPROVED (owner, 2026-10-28) |
| B20 | Not part of this study | each replication gets its own later PREREG: gold holdout (2024-01-01 .. 2026-09-30, OD2 seam rule), silver holdout (same window), EURUSD dev. Nothing from 2026-10-01 is read; no gold bar is read here | APPROVED (owner, 2026-10-28) |

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
   the verdict (B13), the break-even spread (B10), and the counts next to every
   result: breaks, B, C, unresolved, excluded short-day events.
5. **The replications (B20)** are not part of this file.

## 4. What is reported

- **H1:** silver's B share, the random walk's, the difference with its CI, and
  the raw and BH p.
- **H2:** mean(hit − P) with its CI and the raw and BH p; the hit rate and mean
  P; the resolved, unresolved and excluded counts; the break-even spread in σ
  (B10).
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

## 6. Amendment 1 (2026-10-28)

**Decided by the owner on 2026-10-28, before any outcome was read**
(`ATTEMPTS.json`: `outcome_analyses` 0; the dev step never ran).

**Reason: the B15 random-walk check failed on H2** (`results/rw.json`, 200
paths; `REPORT.md`). H2's pooled mean(hit − P) was **−1.86 pp** (tolerance
±1 pp), and its CI covered 0 in **81.5 %** of paths (93–97 % needed). H1
passed (96.5 %).
- **The cause is the 30-candle horizon.** The analytic baseline P = stop /
  (stop + target) has no time limit, but H2 counts only trades resolved within
  30 candles.
- **The near stop resolves first.** The stop is 1 σ away and the target
  usually 2–3.5 σ, so the trades left unresolved (9.6 %) are mostly would-be
  hits.
- **The bias grows with target distance.** On the random walk, by target
  distance: −1.3 pp (0.55–1.87 σ), −0.8 pp (1.87–2.60 σ), −1.7 pp
  (2.60–3.50 σ), −4.5 pp (3.50–10.3 σ).
- **A residual of about −1 pp** comes from the 1-min steps and the tie rule
  (B8).

### 6.1 H2 (replaces B9's statistic)
- **Strata:** H2's trades are split into **four target-distance quartiles**
  (target_dist / σ at the break). The edges are **1.8719, 2.5957 and 3.4948 σ**,
  fixed from all 141,503 random-walk breaks of the 200 paths on the silver dev
  timestamps (`results/rw_target_quartiles.json`; no real outcome).
- **Statistic:** in each quartile q, d_q = silver's mean(hit − P) minus the
  random walk's mean(hit − P), each over resolved trades. **H2 = the
  equal-weight mean of d_1 .. d_4.** The random walk's quartile means come from
  the 200-path simulation (`results/rw.json`) and are held fixed.
- **Test:** one-sided, H2 > 0 (the fade beats the random walk). The 95 % CI and
  p come from the month-block bootstrap on silver (B14). p is the share of
  resamples with H2 ≤ 0, +1-corrected. A resample with an empty quartile is
  dropped and counted.
- **Unchanged:** P, the trade (B7, B8), BH across H1 and H2 (B12), and the
  break-even spread (B10). H2 still doesn't enter the verdict (B13).

### 6.2 The random-walk check (replaces B15's H2 part)
- **Path against the rest:** each path r is tested against the other 199. Its
  stratified statistic is computed against the leave-one-out random-walk
  quartile means, and its CI comes from the month-block bootstrap on path r.
- **H2 passes if** the mean of the 200 path statistics is within ±1 pp, and
  their 95 % CIs cover 0 in 93–97 % of paths.
- **H1's part is unchanged:** coverage 93–97 %.
- **A fail stops the study.**

### 6.3 Attempt counter
`ATTEMPTS.json` records this amendment (`amendments` = 1). `outcome_analyses`
stays 0.

