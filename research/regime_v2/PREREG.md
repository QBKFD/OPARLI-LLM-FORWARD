# Pre-registration: regime model v2 + Stage 1 test

Committed **before any result on real data**, and never edited afterwards.
Research only. Nothing in `regime_classifier/` or `backend/` changes.

## Question

Do **causal** regime labels from a jump model on the locked volatility block
carry information about future outcomes beyond simple baselines, above all
beyond terciles of L?

## Honesty note (repeated in the report)

The volatility features were selected using 2007–2020 (vol rounds 2 and 3). So
CONFIRMATION here is **not a fully independent test**. The independent test is
the sealed 2024+ data at the very end.

## Data, rows, seal

- **CONFIRMATION:** `XAUUSD.txt` 2007–2020 (UTC). Folds: training rows
  2007-01-01 <= t <= test start − 24h (expanding), 24 half-year tests 2009H1 to
  2020H2. Eras: 2008–12 and 2013–20.
- **DEVELOPMENT:** the clean parquet 2021–2023 (2020 warm-up), 8 quarterly tests,
  training from 2021-01-01.
- **SEALED:** no timestamp >= 2024-01-01 anywhere (CONF < 2021-01-01). Asserts
  run on the loaders, bars, frames, target windows and samples, and a test checks
  that the assert fires.
- **Rows:** 1H bars labelled by close time t. A row enters a labeling only if all
  of that labeling's inputs are finite. Warm-up rows and rows with missing
  features get **no label** (UNKNOWN). Nothing is ever filled.

## Part A: model

- **Features:** version A = (L, S_hd, T) from `research/vol_block` (`VolBlock` fit
  on the training 1-min data). Version B = (L, S_hd, T, Z24), with Z24 as in the
  trend study.
- **Standardisation:** mean and std from the training rows, then clip to ±4.
- **Jump model:** minimise Σ‖x_t − μ_{s_t}‖² + λ Σ 1[s_t ≠ s_{t−1}], K = 3,
  10 k-means++ initialisations (seeded), coordinate descent (exact Viterbi, then
  centroid update, repeated until the labels stop changing, at most 100
  iterations). The best objective is kept. Implementation: `jump.py`.
- **Causal labels:** the forward pass of the dynamic program only (argmin of the
  accumulated cost at t, from data up to t). The filter runs continuously over
  the complete rows from the period start, so test labels carry their training
  history. **Only causal labels are evaluated.** Offline (Viterbi) labels are
  used only for the causal-vs-offline agreement in S4.
- **Naming:** states are sorted by their mean raw L over the training rows (the
  model's own fitted training assignment) → CALM < NORMAL < STRESSED. For K = 2 /
  K = 4, the ranks are R1..RK in the same order. No Hungarian matching, no naming
  by returns.
- **λ selection (per fold, per version):** grid {0, 0.5, 1, 2, 5, 10, 20, 50,
  100, 200}. The validation slice is the last 12 months of the training window
  (val_start = train_end − 12 months). The fit part is the training rows with t
  <= val_start − 24h (embargo). For the selection stage, a separate `VolBlock` is
  fit on the fit part's 1-min data only, and its profile also gives y24_ds.
  Scaler: fit part only. For each λ: fit on the fit part, run causal labels over
  fit part + validation, take the regime means of y24_ds from the fit-part rows
  (by causal label) as forecasts for the validation rows, and compute the
  validation R² against the fit-part mean. Choose the λ with the highest R² among
  those whose mean causal-label duration on the validation rows is >= 24 bars
  (ties → the larger λ). If no λ reaches 24 bars, choose the λ with the longest
  mean duration. Then refit on the full training window (new `VolBlock`, new
  scaler) with the chosen λ. The chosen λ is reported per fold.
- **Sensitivity (report only):** K = 2 and K = 4 (same selection procedure),
  and every λ in the grid at K = 3.

## Part B: Stage 1 test

- **Outcomes (strictly after t):** O1 = y24_ds (vol round 3 definition, with the
  fold's full-window profile), O2 = E24 (trend study definition), O3 = y4_ds.
- **Scoring:** a labeling forecasts each test row with the **training-window
  mean of the outcome for that row's label** (labels on the training rows come
  from the same causal rule). A label never seen in training falls back to the
  unconditional training mean. Pooled OOS R² against the unconditional training
  mean (BASE-0). DM on squared errors, Newey–West lag 48 (O1, O2) / 8 (O3),
  one-sided. Reported pooled, per era, per fold and for DEV.
- **Labelings:** JM-A, JM-B; BASE-0; BASE-L3 (terciles of L, thresholds = the
  training 1/3 and 2/3 quantiles); BASE-ADX (repo ADX(14) on 1H > 25; also 20
  and 30 as sensitivity); CONT (OLS on continuous L, S_hd, T, an upper
  reference). All are scored on the same rows per outcome (the intersection).

## Decision rules (judged on CONFIRMATION)

- **S1 information:** JM-A beats BASE-0 on O1 (p < 0.05).
- **S2 value over simple buckets (key):** JM-A beats BASE-L3 on O1 or O2
  (p < 0.05), and BASE-L3 is not significantly better (p < 0.05) on the other.
- **S3 vs ADX (report):** JM-A vs BASE-ADX(25) on O2 and O1, with DM both ways.
- **S4 usability:** the mean duration of JM-A causal test labels is >= 24 bars
  (pooled: test rows / label runs within test blocks). Also reported: switches
  per month, time share per regime, causal-vs-offline agreement on test rows,
  and average lag (bars from an offline switch to the causal label reaching the
  same state, capped at 240; unmatched switches counted separately).
- **S5 robustness:** refit every fold with λ × 0.5 and λ × 2. For each multiplier,
  re-evaluate S1 and S2 pooled. S5 passes if both multipliers reproduce the main
  S1 and S2 verdicts. Also reported: the per-fold pass rate of the directional
  conditions (R²(JM) > R²(BASE-0) on O1; R²(JM) > R²(BASE-L3) on O1 or O2).
- **S6 Z24 ablation:** JM-B beats JM-A on O1 or O2 (p < 0.05) → include Z24,
  otherwise not.
- **Recommendation mapping:**
  - S1 fails → "JM-A carries no information about future volatility; not
    recommended". BASE-L3 vs BASE-0 is reported for reference.
  - S1 passes, S2 fails → "the jump model adds nothing over L terciles; use
    BASE-L3".
  - S1, S2, S4 and S5 pass → "use the jump model": JM-B if S6 passes, else JM-A.
  - S1 and S2 pass but S4 or S5 fails → "jump model not usable or robust; use
    BASE-L3".

## Secondary (reported, not decision rules; BH-FDR across all cells)

Two fixed 1H strategies, CONF test rows, with the regime being the JM-A causal
label at entry:
- **TSMOM-24:** at each row t, position = sign(ln C_t − ln C_{t−24 rows}), held
  24 rows. PnL = position × (C_{t+24} − C_t) − 2 × $0.30 (entry and exit, $0.30
  per unit of |position change|).
- **MR-4:** position = −sign(ln C_t − ln C_{t−4 rows}), held 4 rows. PnL the same
  way.

Per regime: mean PnL per trade ($/oz), HAC t (Newey–West lag = holding rows).
Across regimes: pairwise differences of means with HAC t. Benjamini–Hochberg FDR
at 10 % across all of these tests.

## Exploratory (declared now)

K-sensitivity and λ-grid tables (above), and per-regime profiles of the outcomes.
Everything else added later is labelled exploratory in the report.

## Determinism

Jump model seeds: 1000 · fold + 100 · version + 10 · K + λ index. Tests use numpy
seed 12345.
