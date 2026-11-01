# news_vol (Study A): report

## 1. Verdict: **PASS** (D7, applied literally)

- **What was run:** the walk-forward HAR σ forecast for XAUUSD, dev 2016–2023, tested on the years
  2018–2023. The run was **attempt 1**: the single run of `nv_run.py run` at a863b62, under the
  PREREG merged as 843b447.
- **The rule's two conditions both hold.** Adding I(data release in window) and I(FOMC in window)
  **lowers QLIKE at H = 240**, BH p < 0.05. It is **not worse at H = 60**: it is significantly
  better there too.

| | HAR | HAR + releases | mean d | DM z | p | BH p |
|---|---|---|---|---|---|---|
| **Q2, H = 240 (verdict)** | 0.1517 | **0.1235** (−18.6 %) | −0.0282 | −8.25 | 1.6e−16 | 6.5e−16 |
| Q2, H = 60 | 0.2075 | 0.1812 (−12.7 %) | −0.0263 | −7.73 | 1.1e−14 | 2.2e−14 |

- **Rows:** 29,216 test rows at H = 240 (703 release windows) and 33,839 at H = 60 (188).
- **The checks before the run:**
  - the machinery check passed from the merged commit (`results/machinery.json`);
  - the counts in PREREG §8 match the run's release rows: 703 and 188.

**Q1 and Q3 (reported with BH, outside the verdict):**

| | estimate | 95 % CI (month blocks) | p | BH p |
|---|---|---|---|---|
| **Q1**, H = 240: mean log(RV/h_HAR), release − other | **+1.04** | [0.93, 1.16] | 1e−4 (bootstrap floor) | 1.3e−4 |
| Q3, H = 240: Spearman(log(RV/h_HAR), window return), release windows | +0.07 | [−0.05, 0.18] | 0.26 | 0.26 |
| Q1, H = 60, outside BH (N2) | +1.67 | [1.49, 1.85] | 1e−4 | — |

- **Q1:** HAR misses release windows badly. At H = 240 their realized variance is
  e^1.04 ≈ 2.8× what HAR's forecast implies, relative to the other windows.
- **Q3:** the size of the vol surprise doesn't lean with the direction of gold's move in the
  window.

## 2. Where the gain comes from

**By window type**, from `run.json`. y = log(RV/h_HAR); d per row = QLIKE_aug − QLIKE_HAR.

| H | type | n | mean y | mean d per row |
|---|---|---|---|---|
| 240 | data only (CPI, NFP) | 560 | +0.70 | −0.58 |
| 240 | FOMC only | 143 | **+1.57** | **−3.23** |
| 240 | no release | 28,513 | −0.17 | −0.0014 |
| 60 | data only | 140 | +1.10 | −2.21 |
| 60 | FOMC only | 48 | **+2.43** | **−11.18** |
| 60 | no release | 33,651 | −0.23 | −0.0013 |

- **FOMC carries most of the gain.** It has a fifth of the release rows and 56 % of the total
  QLIKE reduction at H = 240; data releases have 39 %, other rows 5 %.
- **No window holds both** a data release and FOMC.
- **Rows without a release gain a little too.** Their forecast no longer absorbs the release
  windows' variance into the intercept.

**By fold (test year), mean d:**

| H | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|
| 240 | −0.023 | −0.027 | **+0.0001** | −0.037 | −0.048 | −0.035 |
| 60 | −0.015 | −0.023 | −0.004 | −0.036 | −0.048 | −0.030 |

- **2020 shows no gain at H = 240.** There, release windows sit only +0.44 above the rest in
  mean y, against +0.90 to +1.36 in the other years: general vol was high (COVID), so releases stood
  out less.
- **Every other fold gains, at both H.**

**Coefficients** (augmented arm, `results/coefs.csv`, stable across the six training folds):
- **H = 240:** I_data 0.78–1.01 and I_FOMC 1.73–1.97, i.e. variance × 2.2–2.7 and × 5.6–7.2.
- **H = 60:** I_data 1.24–1.43 and I_FOMC 2.57–2.68.
- **HAR's own terms barely move** when the indicators are added: L 0.89–0.95, S_hd 0.25–0.46,
  T −0.18 to −0.36.

## 3. Caveats (also in PREREG §1)

- **Dev isn't unseen.** Gold 2016–2023 was read by earlier studies, and HAR's (L, S_hd, T)
  were chosen on overlapping data.
- **The direction was close to known.** The calendar notes already show minute-0 range spikes
  at releases. What this study adds is the size of the miss (Q1) and that the gain survives
  out of sample, fold by fold.
- **It is one market.** D1 puts XAGUSD and EURUSD in their own later PREREGs.
- **N1 reads D4 one way:** a release counts when its reaction bar closes inside the window. The
  literal reading would differ only for whole-hour releases (FOMC 14:00): at H = 240, 749 release
  windows instead of 703, mostly the window ending at 14:00. That reading wasn't run, and wasn't
  allowed to be (run once). **The owner should confirm N1.**
- **N8, the unscheduled FOMC cut of 2020-03-03,** is in the set. A live forecaster couldn't have
  known it in advance. It is one event, inside the 2020 fold that shows no gain anyway.
- **Crossing windows are excluded (D4).** So FOMC enters at H = 240 only through the 11:00,
  12:00 and 13:00 NY origins: the 14:00 window crosses the daily break. Nothing here says how a
  forecast should treat a window over the break that holds a release.
- **What this means for risk/:** a σ for brackets near a scheduled release is too small by
  about ×2–3 (data) and more for FOMC, at both horizons.
  - That fits the scoring and agents work only if they use the calendar. `feat/calendar-source`
    (#63) is the as-of source for that. Wiring it in is the owner's decision; nothing live
    changed.

## 4. Files

- `results/run.json`: every number above, plus the git head and the PREREG hash.
- `results/coefs.csv`: coefficients per fold, H and arm.
- `results/machinery.json`: the 1c check.
- `results/counts.{md,json}`: no outcome.
- `ATTEMPTS.json`: prereg 1, machinery_checks 1, outcome_analyses 1.
- **No price and no per-row output is written.**
