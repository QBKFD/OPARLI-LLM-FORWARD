# Decisions log: regime v2

Entries 1–10 were written before any result on real data.

1. **Branch base.** `research/regime-v2` was created from
   `research/trend-features` (0b5bea1).
2. **Own jump-model implementation (`jump.py`)** with numba (0.61.2, already in
   the backend venv). Nothing was installed. The dynamic program is exact
   Viterbi. The causal filter is its forward pass with a running-min offset,
   which doesn't change any decision. A synthetic smoke test (99.98 % recovery)
   was run before PREREG, and no real data was touched.
3. **Training labels for regime means are causal**, the same rule as on the test
   rows. That avoids an offline/causal mismatch between fitting the means and
   using them.
4. **Naming uses the model's own fitted (offline) training assignment** to
   compute the mean raw L per state. It is used only for the names; evaluation is
   on causal labels.
5. **λ selection is kept entirely inside the training window.** A separate
   `VolBlock` and scaler are fit on the fit part, with a 24h embargo before the
   validation slice. That is stricter than reusing the full-window `VolBlock`,
   whose profile would include validation minutes.
6. **When no λ reaches 24 bars:** choose the λ with the longest mean duration.
   Ties on R² go to the larger λ (more persistent).
7. **S2 "not significantly worse"** means that BASE-L3 is not significantly
   better, with DM one-sided p < 0.05 in its favour counting as worse.
8. **S5 is pooled per multiplier** (a per-fold DM on one half-year has little
   power). The per-fold directional pass rate is reported alongside.
9. **Strategy PnL in $/oz**, with the cost as stated ($0.30 per unit of |position
   change|, so $0.60 per round trip). Overlapping per-row trades; the HAC lag
   equals the holding period. Holding periods count bar rows (trading time).
10. **Parallelism.** `VolBlock` and feature construction run in the main process.
    Jump-model fits run per fold in worker processes. The seeds are fixed, so the
    results don't depend on scheduling.

## During and after the run
