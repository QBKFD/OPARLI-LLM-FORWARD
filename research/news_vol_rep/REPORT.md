# news_vol_rep (Study A replication): report

## 1. Verdict: the replication **HOLDS**. XAGUSD **PASS**, EURUSD **PASS** (D7 per market, applied literally)

- **What was run:** Study A's walk-forward HAR σ forecast, refit per market, on XAGUSD and EURUSD dev
  2016–2023, tested on the years 2018–2023.
- **Attempt 1:** the run was the single run of `rep_run.py run` at 18e534b, under the PREREG merged as
  d653c68 (#68). That commit is the merge plus the machinery result.
- **D7 holds in each market.** Adding I(data release in window) and I(FOMC in window)
  **lowers QLIKE at H = 240** with BH p < 0.05 over the 8 tests of both markets (R4). It is **not worse
  at H = 60**: it is significantly better there too.

| market | H | HAR | HAR + releases | mean d | DM z | p | BH p (8 tests) |
|---|---|---|---|---|---|---|---|
| **XAGUSD** | **240 (verdict)** | 0.1251 | **0.1098** (−12.2 %) | −0.0153 | −7.00 | 2.6e−12 | 6.9e−12 |
| XAGUSD | 60 | 0.1936 | 0.1792 (−7.4 %) | −0.0144 | −6.60 | 4.1e−11 | 8.2e−11 |
| **EURUSD** | **240 (verdict)** | 0.1527 | **0.1193** (−21.9 %) | −0.0335 | −8.54 | 1.3e−17 | 1.0e−16 |
| EURUSD | 60 | 0.2054 | 0.1767 (−13.9 %) | −0.0286 | −7.44 | 1.0e−13 | 4.1e−13 |
| *gold, Study A* | *240* | *0.1517* | *0.1235 (−18.6 %)* | *−0.0282* | *−8.25* | | |

- **Rows:**

  | market | test rows, H = 240 | release, H = 240 | test rows, H = 60 | release, H = 60 |
  |---|---|---|---|---|
  | XAGUSD | 29,191 | 710 | 33,764 | 190 |
  | EURUSD | 31,075 | 723 | 35,741 | 194 |

- **The checks before the run:**
  - **Machinery:** the check passed in both markets from the merged commit
    (`results/machinery.json`, git head d653c68). Q1 was 0.85× the generator's truth in each.
  - **Counts:** the release rows match PREREG §8 exactly (710 / 190 and 723 / 194).
  - **RN1:** EURUSD's calendar check found 0 differing hourly labels on the run's bars, at both H.

**Q1 and Q3 (reported with BH over the 8 tests, outside the verdict):**

| market | test | estimate | 95 % CI (month blocks) | p | BH p |
|---|---|---|---|---|---|
| XAGUSD | **Q1**, H = 240: mean log(RV/h_HAR), release − other | **+0.79** | [0.68, 0.89] | 1e−4 (bootstrap floor) | 1.3e−4 |
| XAGUSD | Q3, H = 240: Spearman(log(RV/h_HAR), window return), release windows | +0.06 | [−0.07, 0.18] | 0.38 | 0.38 |
| XAGUSD | Q1, H = 60, outside BH (Study A N2) | +1.34 | [1.19, 1.50] | 1e−4 | — |
| EURUSD | **Q1**, H = 240 | **+1.08** | [0.95, 1.21] | 1e−4 (bootstrap floor) | 1.3e−4 |
| EURUSD | **Q3**, H = 240 | **+0.16** | [0.04, 0.28] | 0.008 | **0.009** |
| EURUSD | Q1, H = 60, outside BH | +1.68 | [1.49, 1.87] | 1e−4 | — |
| *gold* | *Q1, H = 240 / Q3* | *+1.04 / +0.07* | | | |

- **Q1:** HAR misses release windows in both markets. At H = 240, their realized variance relative to the
  other windows is e^0.79 ≈ 2.2× (silver) and e^1.08 ≈ 2.9× (EURUSD) what HAR implies. Gold's was 2.8×.
- **Q3:** in silver, as in gold, the size of the vol surprise doesn't lean with the window's direction.
  - **In EURUSD it does, with BH p 0.009:** release windows with a bigger vol surprise lean toward EURUSD
    up, i.e. the dollar down.
  - Q3 is two-sided and outside the verdict. No direction was predicted, and gold and silver don't
    show it.
  - It is one test in eight. It would need its own question before anything relies on it.

## 2. Where the gain comes from

**By window type**, from `run.json`. y = log(RV/h_HAR); d per row = QLIKE_aug − QLIKE_HAR. The share is
of the total QLIKE reduction.

| market | H | type | n | mean y | mean d per row | share |
|---|---|---|---|---|---|---|
| XAGUSD | 240 | data only (CPI, NFP) | 564 | +0.51 | −0.25 | 31 % |
| XAGUSD | 240 | FOMC only | 146 | **+1.33** | **−2.11** | **69 %** |
| XAGUSD | 240 | no release | 28,481 | −0.11 | −0.0000 | 0 % |
| XAGUSD | 60 | data only | 141 | +0.82 | −0.85 | 25 % |
| XAGUSD | 60 | FOMC only | 49 | **+2.11** | **−7.37** | **74 %** |
| XAGUSD | 60 | no release | 33,574 | −0.19 | −0.0002 | 1 % |
| EURUSD | 240 | data only | 576 | +0.67 | −0.56 | 31 % |
| EURUSD | 240 | FOMC only | 147 | **+1.75** | **−4.26** | **60 %** |
| EURUSD | 240 | no release | 30,352 | −0.19 | −0.0030 | 9 % |
| EURUSD | 60 | data only | 144 | +1.09 | −2.22 | 31 % |
| EURUSD | 60 | FOMC only | 50 | **+2.49** | **−13.17** | **64 %** |
| EURUSD | 60 | no release | 35,547 | −0.23 | −0.0013 | 5 % |

- **FOMC carries most of the gain in both markets**, as in gold (56 % there at H = 240). It does so with
  about a fifth of the release rows.
- **No window holds both** a data release and FOMC.

**By fold (test year), mean d, H = 240:**

| market | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 |
|---|---|---|---|---|---|---|
| XAGUSD | −0.006 | −0.015 | **−0.003** | −0.019 | −0.029 | −0.020 |
| EURUSD | −0.017 | −0.037 | **−0.004** | −0.048 | −0.045 | −0.050 |
| *gold* | *−0.023* | *−0.027* | ***+0.0001*** | *−0.037* | *−0.048* | *−0.035* |

- **Every fold gains in both markets, at both H.** H = 60 is in `run.json`.
- **2020 is again the weakest year.** Release windows stand only +0.40 (silver) and +0.39 (EURUSD) above
  the rest in mean y there, against +0.45 to +1.52 in the other years. Gold showed the same in 2020
  (+0.44): COVID-era vol was high everywhere, so releases stood out less.

**Coefficients** (augmented arm, `results/coefs.csv`, range over the six training folds):

| market | H | I_data | I_FOMC | variance × (data, FOMC) |
|---|---|---|---|---|
| XAGUSD | 240 | 0.38–0.58 | 1.32–1.49 | 1.5–1.8, 3.7–4.4 |
| XAGUSD | 60 | 0.61–1.04 | 2.05–2.36 | 1.8–2.8, 7.8–10.6 |
| EURUSD | 240 | 0.73–0.96 | 1.84–2.06 | 2.1–2.6, 6.3–7.8 |
| EURUSD | 60 | 1.14–1.42 | 2.50–2.69 | 3.1–4.1, 12–15 |
| *gold* | *240* | *0.78–1.01* | *1.73–1.97* | *2.2–2.7, 5.6–7.2* |

- **Silver reacts least:** its release multipliers are about two-thirds of gold's. **EURUSD reacts about as
  much as gold**, a little more to FOMC.
- **HAR's own terms barely move** when the indicators are added. L is 0.88–0.94 for silver and 0.81–0.96
  for EURUSD, across both H and both arms.

## 3. Caveats (also in PREREG §1 and DECISIONS)

- **The direction was expected.** Study A's gold result was known and strong. What this adds is that the
  gain holds on two other markets, out of sample, in every fold.
- **Dev isn't unseen.** Silver dev was read by `breakout_fade` and `variance_ratio`, and EURUSD dev by
  `variance_ratio`. Neither looked at release windows or HAR forecasts. HAR's features were chosen on
  gold; here they are only refit.
- **R5, US releases only.** In EURUSD, ECB and other EUR High-impact events sit in the "no release"
  windows: 767 of them at H = 240, 2.5 % of the rows. If they raise vol, they most likely make the
  US-release contrast look smaller, so EURUSD's gain would be understated if anything (not tested). A
  forecast for EURUSD would want them flagged too; that is a new question.
- **The same calendar and events as gold** (N1 approved, N8: the unscheduled 2020-03-03 cut stays in;
  2020-03-15 falls in no window, RN9).
- **Crossing windows are excluded (R3).** FOMC at H = 240 enters only through the 11:00, 12:00 and 13:00
  NY origins, as in gold. EURUSD's pause is 15 minutes, not an hour. RN1 shows the exclusion is the same
  set of hourly windows either way.
- **What this means for risk/:** the ×2–3 (data) and larger (FOMC) σ shortfall near scheduled releases
  isn't a gold quirk. Silver's is smaller, EURUSD's about gold's. Wiring `feat/calendar-source` (#63)
  into σ stays the owner's decision; nothing live changed.

## 4. Files

- `results/run.json`: every number above, per market, plus the BH family, the git head and the PREREG
  hash.
- `results/coefs.csv`: coefficients per market, fold, H and arm.
- `results/machinery.json`: the check from the merged commit.
- `results/counts.{md,json}`: no outcome.
- `ATTEMPTS.json`: prereg 1, machinery_checks 1, outcome_analyses 1.
- **No price and no per-row output is written.**
