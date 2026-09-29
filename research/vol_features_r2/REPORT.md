# Volatility block, round 2: report

Pre-registration: [PREREG.md](PREREG.md) (commit `08e612a`, before any model
result; the data audit is `6d0bcf5`, before that). Decisions:
[DECISIONS.md](DECISIONS.md). All tables: [results/summary_r2.md](results/summary_r2.md).
**CONFIRMATION** (primary) = `XAUUSD.txt` 2007–2020. **DEVELOPMENT** = clean
parquet 2021–2023, already seen in round 1. No row at or after 2024-01-01 was
used, and CONF never read past 2020-12-31 (asserts on the loaders, bars, fold
frames, target windows and model samples all passed).

## 1. Verdict

| Rule | CONF all (24 half-years) | CONF 2008–12 | CONF 2013–20 | DEV (8 quarters) |
|---|---|---|---|---|
| **R1** C replaces 13+hours | **PASS**. y4: 0.710 vs 0.523; y24: 0.789 vs 0.564; DM +26.0 / +12.2 (M1H never better) | PASS | PASS | PASS (0.574 vs 0.385; 0.616 vs 0.422) |
| **R2** no clock | **FAIL**. S η² 0.153 (≥ 0.05); hour gain y4 0.0133 (> 0.01), y24 0.0011 | FAIL (S 0.105; gain 0.013) | FAIL (S 0.153; gain 0.013) | FAIL (S 0.123; gains 0.003 / 0.002 are fine) |
| **R3** keep W | **drop W**. y4 p = 0.24 (not better); y24: MC3 better, p = 0.013 | drop | drop (W helps y4 p = 0.007, hurts y24 p = 0.032) | drop (MC3 better at y4, p = 0.033) |
| **R4** distinctness | no flags (max \|ρ\| 0.378, L~T) | no flags | no flags | **L~T 0.607 flagged** |
| **R5** stability (MC) | **FAIL** on W→y4 only (2/24 folds significant, one of each sign). L, S, T: 24/24 significant with the same sign | PASS | PASS | PASS |

In CONF, L, S and T keep the same sign in every fold and for both targets: L +,
S +, T −. MC beats M1H in 23 of 24 CONF half-years on R² for both targets, and in
8 of 8 DEV quarters.

## 2. Recommended volatility block (strictly by the rules)

**Status quo: keep the 13 existing features.** R1 passes by a wide margin, but
R2 fails, and the pre-registered mapping says "R1 pass + R2 fail → not
recommended (clock contamination)". If R2 had passed, the block would be
**L, S, T** (R3 drops W). R2's failure has a narrow and identifiable cause
(§4, §7). It comes almost entirely from S, and specifically from the
**minute count of bars next to the daily break**, not from market seasonality.
Fixing it needs a new pre-registration and new data (2024–2025 is still sealed).

## 3. Results (pooled; per half-year in results/summary_r2.md)

R² against each fold's training mean / QLIKE (lower is better). Test rows are
the same for every model.

| scope | target | test rows | M1 | M1H | **MC** | MC3 | MCH |
|---|---|---:|---:|---:|---:|---:|---:|
| CONF all | y4_ds | 59,283 | 0.498 / 0.309 | 0.523 / 0.298 | **0.710 / 0.181** | 0.710 / 0.181 | 0.724 / 0.177 |
| CONF 2008–12 | y4_ds | 20,732 | 0.427 / 0.378 | 0.450 / 0.365 | 0.731 / 0.150 | 0.731 / 0.149 | 0.744 / 0.144 |
| CONF 2013–20 | y4_ds | 38,551 | 0.533 / 0.272 | 0.558 / 0.262 | 0.700 / 0.197 | 0.700 / 0.197 | 0.714 / 0.194 |
| CONF all | y24_ds | 55,520 | 0.565 / 0.236 | 0.564 / 0.238 | **0.789 / 0.105** | 0.790 / 0.105 | 0.790 / 0.105 |
| CONF 2008–12 | y24_ds | 18,980 | 0.475 / 0.316 | 0.473 / 0.324 | 0.824 / 0.073 | 0.825 / 0.072 | 0.824 / 0.073 |
| CONF 2013–20 | y24_ds | 36,540 | 0.605 / 0.194 | 0.605 / 0.194 | 0.774 / 0.122 | 0.774 / 0.122 | 0.775 / 0.122 |
| DEV | y4_ds | 9,733 | 0.364 / 0.165 | 0.385 / 0.173 | 0.574 / 0.122 | 0.575 / 0.123 | 0.577 / 0.127 |
| DEV | y24_ds | 9,226 | 0.420 / 0.121 | 0.422 / 0.122 | 0.616 / 0.086 | 0.621 / 0.086 | 0.618 / 0.086 |

Rows in the common sample: CONF 69,049 (y4) and 64,603 (y24); DEV 14,608 and
13,874. DM statistic (one-sided p for "A better"; negative favours A), Newey–West
lag 8 / 48:

| pair (A vs B) | CONF y4 | CONF y24 | DEV y4 | DEV y24 |
|---|---:|---:|---:|---:|
| M1H vs MC (R1) | +26.00 (1.00) | +12.23 (1.00) | +15.36 (1.00) | +7.87 (1.00) |
| MC vs MC3 (R3) | −0.71 (0.24) | +2.24 (0.99) | +1.84 (0.97) | +1.09 (0.86) |
| MCH vs MC | −23.16 (<1e-100) | −7.70 (7e-15) | −0.96 (0.17) | −1.96 (0.025) |
| M1H vs M1 | −19.12 (<1e-80) | +0.81 (0.79) | −2.81 (0.003) | −0.74 (0.23) |

At 4h in CONF, hour dummies improve MC *significantly* (DM −23) but *slightly*
(+0.013 R²). With 59k rows, a small but steady effect is still highly
significant.

## 4. Clock check: hour-of-day η² (first training window, CONF / DEV)

| before deseasonalization | η² | after | η² |
|---|---:|---|---:|
| y4 raw (target) | 0.239 / 0.480 | **y4_ds** | 0.016 / 0.017 |
| y24 raw | 0.001 / 0.007 | y24_ds | 0.000 / 0.001 |
| log RV 1h (HAR) | 0.226 / 0.500 | log dsRV_1h | 0.056 / 0.084 (max 0.092) |
| log RV 24h / 120h | 0.000 / 0.008 | L = log dsRV_24h, T | 0.000–0.002 |
| | | **S** | **0.105 / 0.122 (max 0.153)** |
| | | W | 0.001 / 0.000 |

The 13 existing features (CONF / DEV): atr_ratio 0.498 / 0.444, atr_percentile
0.353 / 0.340, rv_ratio_short_long 0.167 / 0.103, rv_ratio_short_mid 0.164 /
0.119, atr 0.078 / 0.171, bb_width_percentile 0.081 / 0.059, rv_15 0.066 /
0.063, bb_width 0.042 / 0.047, rv_ratio_mid_long 0.039 / 0.016, parkinson_vol
0.019 / 0.028, rv_60 0.006 / 0.008, rv_240 0.000 / 0.002, rv_adjusted 0.000 /
0.001. Deseasonalization removes 93–96 % of the target's clock, and all of it
from L, T and W. S's remaining clock sits mostly in the bars at 22–23 UTC, just
after the break: those bars have 39–44 minutes on average instead of about 59, so
their 1-hour *sum* is smaller (§7 E1).

## 5. Data audit (full version: `research/vol_features/DATA_AUDIT_LONG.md`)

`XAUUSD.txt`: 6.97M rows before 2024, from 2003-05-06. It is **UTC in every
year** (the hourly range profile correlates best at shift 0 in both DST seasons,
the break sits at 21/22 UTC = 17:00 New York, and 2020–2023 match the clean
parquet close for close). Unsorted as stored. One duplicate timestamp (2004-04-30 00:00), where one copy is an
all-zero bar, the only bad OHLC row; it is outside the years used. No stale runs are longer than 30 minutes, prices
have 3 decimals, and the header line is corrupted. **Usable: 2007–2023** (2003–2006 fail the 90 %/90 % coverage rule;
2006 has 0.83). **Confirmation used 2007–2020** (14 years): 2007–08 train only,
then tests from 2009H1 to 2020H2. The 2001–2007 era has no test blocks. In
2008–2011 the daily break was only partly closed (25–40 bars in the break hour).

## 6. Deviations, surprises, bugs

- **No deviation from PREREG after it was committed.** The ambiguities
  resolved before it are in DECISIONS #1–16 (dsRV in bar rows, sparse buckets set
  to the median, η² in every window, R5 judged on MC, and the rule-to-block
  mapping).
- **Bugs caught before use:** a NaN in the audit's timezone correlation, and a
  profile test that passed without testing anything (DECISIONS #6, #17).
- **Found in the repo:** round 1's `REPORT.md` is emptied in the working tree
  (the committed copy is intact and was left alone). Several CSVs were removed
  from `database/ohlcv_data/`. `XAUUSD.txt` has a corrupted header, and the clean
  parquet is a copy of its 2020–2023 rows.
- **Surprise 1:** the 13 features **break down in 2011H2** (y4: M1 R²
  −0.75, M1H QLIKE 1.63; MC R² 0.665). The dollar-scaled `atr` and friends went outside their
  training range during the 2011 gold spike (round 1 finding 2). MC is scale-free
  and held up.
- **Surprise 2:** the gap between C and the 13 is *larger* in the older era
  (2008–12: +0.28 / +0.35 R²) than in 2013–20 (+0.14 / +0.17).
- **Surprise 3:** W (the wick ratio) adds nothing out of sample in CONF and hurts
  slightly at 24h, even though it was distinct in round 1.
- R² here isn't comparable to round 1, because the targets are now
  deseasonalized.

## 7. Amendments (exploratory, not confirmatory): `exploratory_r2.py`

Same CONF folds and rows. The η² values are the maximum over training windows.

- **E1 where S's clock is:** mean S by UTC hour stays between −3.18 and −3.52 for
  00–19 UTC and drops at 20–23 UTC (−3.57, −3.59, **−4.19**, −3.87). The bar's minute count has η² 0.41:
  22 UTC bars average 38.7 minutes and 23 UTC bars 43.9.
- **E2 causes:** computing S from **per-minute means** instead of sums brings
  its η² from 0.153 to **0.041** (< 0.05). The level dsRV_1h (without the log) has
  η² 0.006, against 0.092 for log dsRV_1h. So the rest is a log/Jensen effect,
  and the log hits hardest in short, sparse bars.
- **E3 fixes, R² at y4 / y24:** MC 0.7103 / 0.7892. MC with S_mean
  **0.7169 / 0.7912**. MC with S demeaned by training-window hour (η² 0 by
  construction) 0.7120 / 0.7900 (DM against MC −7.8 / −8.4, significantly
  better). **In every variant, hour dummies still add 0.012 at 4h**, because the
  target itself keeps η² ≈ 0.016 (short windows next to the break). R2's R²
  test can only pass if the target is count-adjusted too.
- **Suggested round 3 (to pre-register, confirm on the sealed 2024–2025):** block
  L, S_mean, T, with per-minute-mean targets, and R2 applied unchanged. W stays
  out.
