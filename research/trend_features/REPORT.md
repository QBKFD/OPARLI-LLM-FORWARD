# Trend features: report

Question: does direction-neutral trend strength predict whether price moves
cleanly or chops over the next 4h / 24h? PREREG `8d89ef4` (before any model
result). Decisions: [DECISIONS.md](DECISIONS.md). All tables, including every
fold: [results/summary_trend.md](results/summary_trend.md). **CONFIRMATION** =
`XAUUSD.txt` 2007–2020 (first use for the trend question). **DEVELOPMENT** =
parquet 2021–2023. No row at or after 2024-01-01 was used, and CONF stays below
2021-01-01 (all asserts passed). Phase A (volatility block locked, `VolBlock`)
is in `research/vol_block/`.

## 1. Verdict

| Rule | CONF all (24 half-years) | 2008–12 | 2013–20 | DEV |
|---|---|---|---|---|
| **R0** information (MV+MT > MV) | **PASS** at E24 (R² 1.09 % vs 0.38 %, DM −2.97, p = 0.0015). E4: no. M1+: fails (E24 p = 0.058) | MT pass, M1+ pass | **both fail** (MT E24 p = 0.061) | MT pass at E24 (p = 0.030); M1+ fails |
| **R1** replacement | **PASS** (M1+ never significantly better; E24 0.0109 vs 0.0087) | **FAIL** (E24: MV+M1+ 1.52 % vs MV+MT 1.00 %) | PASS | PASS |
| **R2a** η² < 0.05 | **PASS** (Z4 0.016, Z24 0.001, Z120 0.000) | PASS | PASS | PASS |
| **R2b** MT gain <= M1+ gain | **FAIL**: E4 0.00180 vs 0.00194 (ok), **E24 −0.00056 vs −0.00063** (fails by 0.00007) | FAIL | PASS | FAIL (both gains negative) |
| **R4** distinctness | no flags (max \|ρ\| 0.179, Z4~Z24) | none | none | none |
| **R5** stability (MV+MT) | Z24→E24 **− in 24/24 folds**; Z4→E24 + (8 significant); Z120→E24 − (10); Z4→E4 + (3); Z24, Z120 → E4: no signal | same signs | Z24→E24 − (16/16), rest no signal | weak |

## 2. Recommendation (strictly by the rules)

**MT is not recommended: R2b failed.** The mapping allows no override. The
failure is as thin as a pre-registered test can fail. At E24, hour dummies
*reduce* out-of-sample R² for both blocks (MT by 0.00056, the old block by
0.00063), and the relative rule has no tolerance. Neither block carries clock
information worth anything, and MT's own η² is ≤ 0.016. The substance is:
MT adds a small, significant amount of information over volatility at the 24h
horizon only, and the old trend block doesn't reach significance. That
information is concentrated in 2008–12 and is not significant in 2013–20. The
rules also didn't reach "no trend block" (R0 passed), so the outcome is "not
recommended, R2b failed". Adding MT would need an engineering decision like the
one made for the volatility block, validated on sealed 2024+ data.

## 3. Results (R² ×100 / MAE; same rows for every model)

Rows: CONF E4 70,171 (test 58,836), E24 64,083 (54,459); DEV 14,568 (9,693) and
13,842 (9,194).

| scope | target | MV | M1+ | MT | MV+M1+ | **MV+MT** | MV+MTH |
|---|---|---:|---:|---:|---:|---:|---:|
| CONF all | E4 | 0.23 / .0857 | −0.18 / .0851 | −0.07 / .0851 | 0.08 / .0858 | 0.20 / .0857 | 0.28 / .0858 |
| CONF all | E24 | 0.38 / .0413 | 0.78 / .0405 | 0.93 / .0405 | 0.87 / .0412 | **1.09** / .0411 | 1.04 / .0412 |
| 2008–12 | E24 | −0.28 / .0405 | 2.10 / .0392 | 1.51 / .0393 | 1.52 / .0401 | 1.00 / .0403 | 1.12 / .0403 |
| 2013–20 | E24 | 0.70 / .0417 | 0.14 / .0413 | 0.65 / .0411 | 0.55 / .0418 | 1.13 / .0416 | 1.00 / .0416 |
| DEV | E4 | −0.11 / .0870 | −0.32 / .0871 | 0.08 / .0869 | −0.48 / .0872 | −0.03 / .0870 | −0.36 / .0871 |
| DEV | E24 | 0.03 / .0415 | −0.53 / .0413 | 0.32 / .0412 | −0.39 / .0413 | 0.74 / .0412 | 0.62 / .0412 |

DM (one-sided p for "A better"; Newey–West lag 8 / 48):

| A vs B | CONF E4 | CONF E24 | DEV E4 | DEV E24 |
|---|---:|---:|---:|---:|
| MV+MT vs MV (R0) | +1.43 (0.92) | **−2.97 (0.0015)** | −0.97 (0.17) | −1.87 (0.030) |
| MV+M1+ vs MV (R0) | +2.11 (0.98) | −1.57 (0.058) | +2.81 (1.00) | +0.63 (0.73) |
| MV+M1+ vs MV+MT (R1) | +1.69 (0.95) | +0.87 (0.81) | +3.12 (1.00) | +1.89 (0.97) |
| MT vs M1+ | −1.68 (0.047) | −0.64 (0.26) | −3.02 (0.001) | −1.41 (0.079) |

At E24, MV+MT beats MV in 18/24 CONF half-years and 7/8 DEV quarters. At E4 it
does so in 9/24.

## 4. Clock (η², max over training windows; CONF / DEV)

| new | η² | old | η² |
|---|---:|---|---:|
| Z4 | 0.016 / 0.010 | adx | 0.023 / 0.008 |
| Z24 | 0.001 / 0.002 | adx_percentile | 0.023 / 0.010 |
| Z120 | 0.000 / 0.001 | di_bias / ema_slope / price_vwap_dist | ≤ 0.004 |
| L / S_hd / T | 0.002 / 0.000 / 0.000 | \|di_bias\| | **0.046** / 0.025 |
| (S before hour-demeaning) | 0.049 / 0.120 | \|ema_slope\| / \|price_vwap_dist\| | 0.018 / 0.012 |
| E4 / E24 (targets) | 0.014 / 0.002 | | |

Hour-dummy R² gains (CONF, E4 / E24): MT +0.0018 / −0.0006; M1+ +0.0019 /
−0.0006; MV+MT +0.0008 / −0.0005. No block has meaningful clock content here.
S_hd does its job (η² 0).

## 5. Correlations (R4)

Among MT: max |ρ| 0.179 (Z4~Z24). Between MT and MV: max 0.170 (Z120~T); DEV
0.271 (Z120~L). The three horizons of Z are nearly independent of each other and
of volatility.

## 6. Deviations and surprises

- No deviation from PREREG. One test was fixed before the run (DECISIONS #10).
- **Trends exhaust:** the Z24 coefficient for E24 is *negative* in 24/24 CONF
  folds and 16/16 in 2013–20. A clean past 24h predicts a choppier next 24h. Z4
  goes the other way (+), so short-term cleanness persists a little.
- **Higher volatility → more chop:** L is negative for both targets (24/24).
- **The information fades:** strong in 2008–12, not significant in 2013–20 (for
  both blocks).
- **MAE disagrees with R²:** models with MV have slightly better R² but *worse*
  MAE (CONF E24 MV+MT .0411 vs MT .0405). E is bounded and skewed. The volatility
  terms improve squared error in the tails and move the typical forecast. For a
  regime model, where the typical case matters, this argues against reading the
  R² gains too strongly.
- R² values are 0.2–1.1 %, which matches the prior ("well below 0.05").

## 7. Exploratory (declared in PREREG, not a decision rule)

**Does signed direction predict the sign of the next 24h return?** Walk-forward
OLS of sign(r24) on [di_bias, signed Z24], CONF: hit rate **50.3 %** (NW t 0.46,
p = 0.32; 57,157 test rows; 50.3 % of test days were up). **No.** By era:
2008–12 53.1 % (p = 0.004), 2013–20 48.8 % (below 50 %). The 2008–12 figure
comes with the 2009–11 bull market, so the intercept likely picked up drift
rather than skill. The same holds for each regressor alone (di_bias 50.5 %,
Z24_signed 50.3 %).
