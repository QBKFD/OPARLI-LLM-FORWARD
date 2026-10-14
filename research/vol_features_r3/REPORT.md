# Volatility block, round 3: report

Mechanical fix of round 2: per-minute means instead of sums (features and
targets), W removed. PREREG `af72ba1` (before any model result). Code =
`run_r2.py` plus the listed changes ([results/diff_run_r2_vs_r3.txt](results/diff_run_r2_vs_r3.txt)).
All tables: [results/summary_r3.md](results/summary_r3.md). Decisions:
[DECISIONS.md](DECISIONS.md). **CONFIRMATION = `XAUUSD.txt` 2007–2020: second use
of this period, mechanical fix only.** No unseen confirmation was possible (no
1-min file for another instrument; `GC_FUTURES_1MIN.csv` is gold and starts
2025-12). No row at or after 2024-01-01 was used, and CONF stays below
2021-01-01 (all asserts passed).

## 0. Repo check (report only, nothing restored)

`git status`: 2 modified tracked files. `research/vol_features/REPORT.md` has 189
lines removed (emptied), and `notebooks/backtest.ipynb` has 3 lines changed. There
are no deleted tracked files. The CSVs missing from `database/ohlcv_data/`
(`XAUUSD_COMPLETE*.csv`, `xauusd_5year_1min.csv`, `xauusd_gap_filled.csv`) were
gitignored and never tracked, so git doesn't list them and can't restore them.
The folder was changed on 2026-09-29 at 00:02 and the report was emptied at
07:48, both outside any commit in this work.

## 1. Verdict

| Rule | CONFIRMATION all (24 half-years) | 2008–12 | 2013–20 | DEVELOPMENT |
|---|---|---|---|---|
| **R1** replace | **PASS**. y4 0.726 vs 0.517, y24 0.795 vs 0.553. M1H never better (DM +28.2 / +12.5) | PASS | PASS | PASS (0.575 vs 0.384; 0.621 vs 0.422) |
| **R2** no clock | **FAIL**. y4 hour gain **0.0119** (> 0.01); y24 0.0008. η² L 0.002, **S 0.049**, T 0.000 (all < 0.05) | FAIL (gain 0.0105) | FAIL (gain 0.0126) | FAIL (S η² **0.120**; gains 0.003 / 0.002) |
| **R4** distinctness | no flags (max L~T 0.367) | no flags | no flags | **L~T 0.607** |
| **R5** stability | **PASS**. L +, S +, T −, significant in 24/24 folds for both targets | PASS | PASS | PASS (8/8) |

MC beats M1H on R² in **24 of 24** CONF half-years and 8 of 8 DEV quarters, for
both targets.

## 2. Recommendation

**(L, S, T) is not recommended. R2 failed** (the rules allow no override). In
CONFIRMATION it failed on one criterion only: adding hour dummies to (L, S, T)
still gains 0.0119 R² at 4h, against a limit of 0.01. In DEVELOPMENT it failed on
S's η² (0.120). The fix removed the count artefact where it mattered: S's η² fell
from 0.153 to 0.049 in CONF, where the break was only partly closed before 2012.
It did not remove the clock that is left. §6 shows that clock is a log-scale
effect, which a mean-based fix can't reach.

## 3. Results (pooled; per era and per block in summary_r3.md)

R² against each fold's training mean / QLIKE (lower is better). The same rows
are used for every model: CONF 69,496 / 64,914 (test 59,844 / 56,007); DEV
14,608 / 13,874 (test 9,733 / 9,226).

| scope | target | M1 | M1H | **MC (L,S,T)** | MCH |
|---|---|---:|---:|---:|---:|
| CONF all | y4 | 0.493 / 0.311 | 0.517 / 0.302 | **0.726 / 0.168** | 0.738 / 0.165 |
| CONF 2008–12 | y4 | 0.413 / 0.384 | 0.433 / 0.376 | 0.753 / 0.129 | 0.763 / 0.125 |
| CONF 2013–20 | y4 | 0.532 / 0.271 | 0.558 / 0.262 | 0.713 / 0.190 | 0.725 / 0.187 |
| CONF all | y24 | 0.555 / 0.242 | 0.553 / 0.245 | **0.795 / 0.102** | 0.796 / 0.102 |
| CONF 2008–12 | y24 | 0.447 / 0.339 | 0.443 / 0.348 | 0.834 / 0.067 | 0.835 / 0.067 |
| CONF 2013–20 | y24 | 0.603 / 0.192 | 0.603 / 0.192 | 0.778 / 0.120 | 0.779 / 0.119 |
| DEV | y4 | 0.364 / 0.165 | 0.384 / 0.173 | 0.575 / 0.123 | 0.578 / 0.127 |
| DEV | y24 | 0.420 / 0.121 | 0.422 / 0.122 | 0.621 / 0.086 | 0.622 / 0.085 |

DM statistic (one-sided p for "A better"; negative favours A), Newey–West lag 8 /
48:

| A vs B | CONF y4 | CONF y24 | DEV y4 | DEV y24 |
|---|---:|---:|---:|---:|
| M1H vs MC (R1) | +28.21 (1.00) | +12.46 (1.00) | +15.47 (1.00) | +8.37 (1.00) |
| MCH vs MC | −23.31 (<1e-100) | −7.83 (2e-15) | −0.87 (0.19) | −1.61 (0.053) |
| MC vs M1 | −30.62 (<1e-200) | −12.66 (5e-37) | −16.46 (3e-61) | −8.31 (5e-17) |
| M1H vs M1 | −19.32 (<1e-80) | +1.63 (0.95) | −2.80 (0.003) | −0.81 (0.21) |

## 4. Clock table (η², max over training windows; CONF / DEV)

| feature | η² | | feature | η² |
|---|---:|---|---|---:|
| **L** | 0.002 / 0.001 | | atr_ratio | 0.498 / 0.471 |
| **S** | **0.049 / 0.120** | | atr_percentile | 0.367 / 0.350 |
| **T** | 0.000 / 0.000 | | rv_ratio_short_long | 0.169 / 0.119 |
| log mRV_1h | 0.018 / 0.082 | | rv_ratio_short_mid | 0.165 / 0.123 |
| y4_ds (target) | 0.015 / 0.017 | | atr | 0.120 / 0.171 |
| y24_ds (target) | 0.003 / 0.002 | | bb_width_percentile | 0.081 / 0.061 |
| y4 raw | 0.273 / 0.480 | | rv_15 | 0.072 / 0.081 |
| y24 raw | 0.004 / 0.007 | | bb_width | 0.044 / 0.048 |
| | | | rv_ratio_mid_long | 0.042 / 0.020 |
| **Hour-dummy gain, y4 / y24** | | | parkinson_vol | 0.021 / 0.030 |
| MC → MCH (CONF) | 0.0119 / 0.0008 | | rv_60 | 0.007 / 0.011 |
| M1 → M1H (CONF) | 0.0238 / −0.0013 | | rv_240 | 0.001 / 0.002 |
| MC → MCH (DEV) | 0.0026 / 0.0015 | | rv_adjusted | 0.000 / 0.001 |
| M1 → M1H (DEV) | 0.0206 / 0.0022 | | | |

On the same scale, the 13 existing features gain twice as much from hour dummies
as (L, S, T), and 7 of them have η² > 0.05. The R2 limit is not met by either
block. The existing block misses it by more.

## 5. Deviations and surprises

- No deviation from PREREG. Non-listed code edits: the cache path, `sys.path`
  and output names (DECISIONS #7). One test tolerance was added (#8).
- **Round 2's exploratory prediction held exactly:** with means, S's clock in
  CONF drops under the limit, and the hour gain stays at about 0.012 because the
  target keeps its clock (η² 0.015).
- **DEV behaves differently:** S's η² is unchanged at 0.12. After 2012 the break
  is fully closed, so the count artefact was small there and the log effect
  (§6) dominates.
- The advantage of (L, S, T) over the 13 grew slightly compared with round 2
  (CONF y4 +0.209 against +0.187), and it now wins every block.

## 6. Exploratory (not confirmatory): `exploratory_r3.py`, first training window

- **Mean S by UTC hour** is about 0 during 06–17 UTC and negative in thin hours:
  CONF 19–23 UTC −0.23 to −0.44; DEV 19 UTC −0.52, 23 UTC −0.71, 00 UTC −0.77.
- **Jensen check:** on the level scale the clock is gone (η² of mean r_ds²: mRV_1h
  0.000 / 0.000, y4 0.007 / 0.000). Only log mRV shows it (0.018 / 0.082; y4 0.015
  / 0.017). The profile normalises the *mean* correctly. What is left is a
  distribution-shape effect: squared returns in thin hours are more skewed, so
  E[log mean] is lower there. A mean-based profile can't remove it.
- **Implication (hypothesis, not tested):** deseasonalize on the log scale,
  either with a profile of E[log mRV] per bucket, or by subtracting a training-window
  hour-of-day mean from S and from the target. Round 2's E3 showed that
  hour-demeaning S alone leaves the target's clock in place, so the target would
  have to be log-deseasonalized as well. That would need its own pre-registration
  and new data (2024–2025 is sealed).
