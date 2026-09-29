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

11. **Deviation (DEV only), found when the first run crashed before any
    result.** In DEV the first training window is 12 months (2021), so the
    pre-registered "validation = last 12 months" leaves no fit part (DEV fold 1)
    or < 6 months (folds 2–3). For those folds only, the validation slice is the
    last third of the training window (triggered when the 12-month rule would
    leave < 182 days to fit on). CONF, where the decision rules are judged, is
    untouched: its first training window is 24 months, which leaves a 12-month
    fit part. Also reported in REPORT §7.

12. **Two crashes before any result was produced**, both fixed in the scoring
    code: (a) the DEV validation fallback (#11); (b) a `.to_numpy()` call on a
    boolean numpy array from a `DatetimeIndex` comparison. No verdict or metric
    had been computed when either happened.

13. **`run_regime.py` frozen after its first complete run** (2026-09-29 18:33).
    `tables_regime.py` is reporting only. It uses its own markdown formatter
    because `tabulate` isn't installed and nothing is installed.

14. **λ at the top of the grid.** In CONF the chosen λ (JM-A) is 200, the
    largest grid value, in 7 of 24 folds, and 100 in 12. The pre-registered grid
    is kept. The λ-grid sensitivity (O1 R² falls from λ = 100 to λ = 200) makes it
    unlikely that a larger λ would change S2.

15. **White gaps in the regime chart** are rows with no label: `S_hd` is missing
    because the bar had < 50 % of its minutes. They are shown as gaps, never
    filled.

16. **Exploratory L-only check** (`exploratory_regime.py`), added after the
    results. It is labelled exploratory in the report.
