# Regime model v2 + Stage 1 test: report

Question: do **causal** regime labels from a jump model on the locked volatility
block carry information beyond simple baselines? PREREG `712a23d` (before any
result). Decisions: [DECISIONS.md](DECISIONS.md). All tables (every fold, every
labeling, strategies, JSON of decisions): [results/summary_regime.md](results/summary_regime.md).

**Honesty note:** CONFIRMATION = `XAUUSD.txt` 2007–2020. The volatility features
were selected on this period, so this is **not a fully independent test**. The
independent test is the sealed 2024+ data at the very end. DEVELOPMENT = parquet
2021–2023 (already seen). No row at or after 2024-01-01 was used, and CONF stays
below 2021-01-01 (all asserts passed).

## 1. Verdict

| Rule | CONF all (24 half-years) | 2008–12 | 2013–20 | DEV (8 quarters) |
|---|---|---|---|---|
| **S1** JM-A > BASE-0 (O1) | **PASS** (R² 0.572, DM −24.5) | PASS | PASS | PASS (0.305) |
| **S2** JM-A > L terciles (key) | **FAIL.** L3 is *significantly better* on O1 (0.640 vs 0.572, DM −6.37, p < 1e-10); O2 tie (p = 0.35 / 0.65) | FAIL (0.667 vs 0.529) | FAIL (0.628 vs 0.591) | FAIL (0.388 vs 0.305) |
| **S3** vs ADX>25 | JM-A ≫ ADX on O1 (ADX R² ≈ 0). On O2 ADX 0.32 % vs JM 0.03 %, n.s. (p = 0.11) | ADX better on O2 (p = 0.007) | n.s. | JM ≫ ADX on O1; O2 n.s. |
| **S4** usability | **PASS.** Mean duration 319 bars, 1.39 switches/month | PASS | PASS | PASS (104 bars, 4.4/month) |
| **S5** robustness (λ×0.5, λ×2) | **PASS** (same S1/S2 verdicts at both; per-fold direction: S1 92 % / 88 %, S2 63 % / 50 %) | PASS | PASS | PASS |
| **S6** Z24 helps | PASS on O2 only (R² 0.23 % vs 0.03 %, p = 0.035); O1 no | PASS (O2) | FAIL | FAIL |

L3 beats JM-A on O1 in 16 of 24 CONF half-years and 7 of 8 DEV quarters. JM-A
beats BASE-0 in 22 of 24.

## 2. Recommendation (strict mapping)

**"The jump model adds nothing over L terciles; use BASE-L3."** S1 passed and
S2 failed, in every era and in DEV. The simple labeling isn't just as good, it
is *better*: significantly so for 24h and 4h volatility, and tied for directional
efficiency. S4 and S5 pass, so the jump model works as designed (stable,
causal, robust). It is simply the wrong tool for this job.

## 3. Results

Pooled OOS R² ×100 against BASE-0 (the same rows for every labeling):

| scope | outcome | rows | JM-A | JM-B | **L3** | ADX25 | CONT | K=2 | K=4 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| CONF | O1 y24_ds | 55,956 | 57.16 | 56.67 | **64.00** | −0.05 | 79.62 | 46.02 | 57.16 |
| CONF | O2 E24 | 56,318 | 0.03 | 0.23 | 0.10 | 0.32 | 0.37 | 0.03 | −0.04 |
| CONF | O3 y4_ds | 59,755 | 48.91 | 48.66 | **57.13** | 0.04 | 72.73 | 39.26 | 49.20 |
| 2008–12 | O1 | | 52.85 | 50.63 | 66.71 | −0.09 | 83.50 | | |
| 2013–20 | O1 | | 59.11 | 59.40 | 62.77 | −0.02 | 77.88 | | |
| DEV | O1 | 9,226 | 30.48 | 23.63 | **38.81** | −0.45 | 62.24 | 23.90 | 26.77 |
| DEV | O2 | 9,226 | −0.11 | 0.09 | 0.52 | 0.27 | 0.06 | 0.44 | 0.73 |
| DEV | O3 | 9,733 | 26.10 | 21.41 | **36.29** | −0.03 | 57.43 | 20.00 | 25.02 |

DM (one-sided p for "A better"; negative favours A), CONF:

| A vs B | O1 | O2 | O3 |
|---|---:|---:|---:|
| JM-A vs BASE-0 | −24.52 (<1e-100) | −0.15 (0.44) | −52.80 (≈0) |
| **L3 vs JM-A** | **−6.37 (9e-11)** | −0.39 (0.35) | −15.85 (7e-57) |
| JM-A vs ADX25 | −24.53 (<1e-100) | +1.21 (0.89) | −52.74 (≈0) |
| JM-B vs JM-A | +0.61 (0.73) | −1.81 (0.035) | +0.65 (0.74) |
| CONT vs JM-A | −19.02 (6e-81) | −1.20 (0.12) | −37.92 (≈0) |

**Every labeling is far below CONT** (continuous OLS on L, S_hd, T, R² 0.80). Any
discrete labeling throws information away.

## 4. Regimes (JM-A, CONF test rows)

| regime | time share | mean run (bars) | mean L | mean S_hd | mean T |
|---|---:|---:|---:|---:|---:|
| CALM | 65.6 % | 498 | −1.16 | −0.02 | −0.04 |
| NORMAL | 28.5 % | 202 | −0.59 | −0.07 | −0.16 |
| STRESSED | 5.9 % | 145 | +0.44 | −0.35 | −0.02 |

Mean duration is 319 bars and switches run at 1.39 per month. Causal labels
agree with offline (Viterbi) labels on 91.9 % of test rows. The lag from an
offline switch to the causal label is median 4 and mean 22.5 bars (137 switches,
2 unmatched within 240). Chart: [figures/regimes_2008_2011_2013_2020.png](figures/regimes_2008_2011_2013_2020.png).
STRESSED marks the 2008 crisis (Sep–Dec; that panel shows fold 1's *training*
labels), Aug–Sep 2011, the April 2013 crash and March 2020. White gaps are rows
left unlabeled (never filled).

## 5. λ

Chosen λ (JM-A, CONF, per half-year): 200, 10, 100, 200, 200, 200, 100, 100, 100,
100, 100, 200, 200, 100, 200, 2, 50, 100, 50, 100, 100, 50, 100, 100. That is
**100 in 12 folds and 200, the top of the grid, in 7**. DEV: 20, 10, 50, 20, 50,
200, 200, 100. Fixed-λ sensitivity (CONF O1 R² ×100): λ 0 → 31.5, 0.5–10 → 38–39,
20 → 42.2, 50 → 53.5, **100 → 59.0**, 200 → 56.6. The selection objective moves
toward persistence, and even the best fixed λ stays below L3 (64.0). Per-version
λ lists are in `results/lambda_*.csv`.

## 6. Secondary: strategies by regime (CONF, BH-FDR 10 %)

- **MR-4** loses in every regime after costs: −$0.63 (CALM, t −21.7), −$0.50
  (NORMAL), −$0.50 (STRESSED) per trade. Its gross edge is about 0, and the
  $0.60 round trip dominates.
- **TSMOM-24:** −$0.55 in CALM (t −3.2, significant); NORMAL and STRESSED are
  n.s.
- **No difference between regimes is significant** (all 6 pairwise tests
  p >= 0.05; the smallest is MR-4 NORMAL − CALM, p = 0.054). The regimes don't
  change how these two strategies behave. DEV is the same, with no significant
  differences.

## 7. Deviations, surprises, bugs

- **Deviation (DEV only):** in folds 1–3 the 12-month validation slice left no
  fit part or less than 6 months, so the last third of the training window was
  used (DECISIONS #11). CONF followed PREREG exactly.
- **Bugs:** two crashes, both before any result (DECISIONS #12). The four
  old-classifier bugs are avoided by construction (no RF, lagged causal labels,
  λ chosen on validation with a direction-neutral objective, no fill).
- **Surprise 1:** the terciles don't just tie, they win. **Surprise 2:** a λ of
  0 (k-means on the 3 features) is the worst option (31.5 %), so the 3-feature
  clustering itself is the problem (see §8). **Surprise 3:** Z24 helps the jump
  model only on directional efficiency, and only a little. ADX has no
  volatility information at all (R² ≈ 0).

## 8. Exploratory (not confirmatory): `exploratory_regime.py`

**Why do terciles win?** The candidates were persistence/lag, or *dilution*: the
equal-weight distance on (L, S_hd, T) lets S_hd and T shape the states, while
future volatility depends mainly on L. A jump model on **L alone** (JM-L), with
λ chosen by the same procedure, reaches CONF O1 R² **0.630** (JM-A 0.572; DM
JM-L vs JM-A −6.81, p ≈ 0), statistically level with L3 (0.640, p = 0.08). At λ =
0 it gets 0.629, so persistence neither helps nor hurts. On O3 L3 still beats
JM-L (0.571 vs 0.545, p ≈ 0). In DEV, JM-L 0.414 vs L3 0.388 (n.s.). **Almost the
whole gap is dilution.** Clustering on three standardized features blurs the one
feature that matters. The regime layer's job (bucketing current volatility) is
done as well or better by quantiles of L, and the continuous features carry much
more (CONT 0.80).
