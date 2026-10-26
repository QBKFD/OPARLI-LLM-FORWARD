# squeeze_oco: σ-timed OCO breakouts on XAUUSD

**Status: EXPLORATORY. Stage 1 done; Q1 set up (counts only), not run. Not a PREREG. Nothing here is evidence.**

- **Q1 (precondition):** does a run of small candles (small relative to σ) on the 15-minute chart (owner, 2026-10-26)
  predict a larger move over the next H minutes than the HAR model already forecasts?
- **Q2 (strategy):** do symmetric stop-entry (OCO) breakout orders, timed by HAR and/or the pattern, beat
  the same orders at untimed squeezes or at random times? Q2 is pursued only if Q1 shows the pattern adds
  information.

The motivation is the owner's discretionary observation; that motivates the hypothesis and is not evidence for it.

| File | What |
|---|---|
| `01_vol_state.ipynb` | Stage 1. It holds the question (verbatim), the **register**, the context (each claim with its file:line), the decision log, the dev-period guard, and the load / describe / calibrate steps. |
| `stage1.py` | The wiring. It holds no values: everything comes in from the register. Uses the repo's loader (`research/vol_features_r3/run_r3.py` `load_conf`), the manifest (`backend/datafeed/store/manifest.py`), the seal guards (`research/sealing.py`, `run_r3.assert_sealed`), `vol_block`, and `sigma_compare`'s post-hoc scoring (`posthoc.py`). |
| `test_stage1.py` | Tests on synthetic bars: the guard, no default values, state and sigma equal to `vol_block.state_at` / `sigma_over`, the warm-up rule, calibration running, the model SHA, the attempt counter. |
| `ATTEMPTS.json` | Outcome analyses so far: 0. Any analysis that looks at an outcome calls `stage1.bump_attempt()` first. |
| `PROPOSALS.md` | The options the owner chose the Q1 items from (decided 2026-10-26: see `Q1_SETUP.md`). |
| `q1_counts.py`, `results/q1_event_counts.json` | The event counts behind PROPOSALS.md: candles and hourly labels per year. No σ, no outcome, no price. Values read from the notebook's register. |
| `Q1_SETUP.md` | The owner's Q1 decisions, Claude's readings (committed before any count), the counts, the (q, r) the rule chose, things to see before the test, the proposed Newey–West lags, what is still OPEN. |
| `q1.py`, `test_q1.py` | Q1's option-B score (range / σ_tf from the model's seasonal mass), the walk-forward quantile threshold, the runs, the counts, the (q, r) rule. Tests with a synthetic model of known seasonal profile. No values inside. |
| `q1_runs.py`, `results/q1_run_counts.json` | The Q1 event counts per (q, r), per year, with clustering and score quantiles, and the rule's choice. Counts only: no forward return, no outcome, no price. |

## Rules

- **Rule number one.** Every number, threshold, window, cost, seed or definition comes from the notebook's
  register. Code reads it through `need(id)`, which raises unless the entry is APPROVED (or APPROVED-BY-RULE: set by an approved rule from counts it cites). A missing value is
  added as OPEN or PROPOSED, with the reasoning, and work stops there.
- **The dev period only:** 2012-01-01 .. 2020-12-31 UTC. 2007–2011 are excluded: a different feed or session
  convention, found by the data check (decision log #16; research/README.md, "Data eras in XAUUSD.txt"). Every
  frame goes through `stage1.dev_guard`.
- **Data licence.** `XAUUSD.txt` is IBKR data, for the owner's own use only. Committed outputs show aggregates
  and charts only, never a price row.

## Running it

From the repo root, with the `[research]` extra plus `nbclient` and `ipykernel`, and with the untracked data in
place (`database/ohlcv_data/XAUUSD.txt` and `research/vol_features_r2/cache/`):

    python -m pytest research/squeeze_oco -q
    python research/squeeze_oco/q1_runs.py
    jupyter nbconvert --to notebook --execute --inplace research/squeeze_oco/01_vol_state.ipynb

## Stages

1. The vol state, described, and HAR's calibration on the dev period (descriptive). **Done, on 2012–2020.**
   - The data check on 2007–2020 found a different era in 2007–2011, which the owner excluded.
   - Steps 2–4 then ran (summary in the notebook's "What Stage 1 found").
2. Q1: the small-candle pattern against HAR.
   - **Set up:** 15 min; option B with a per-New-York-hour quantile threshold; q = 0.20 and r = 4 by the owner's rule (349 runs); H = 240 / 60; NW lag 8; pinned β with only the indicator's γ per fold.
   - **Not run.** It waits for Q1_FOLDS and Q1_AUG_ESTIMATOR (PROPOSED); see `Q1_SETUP.md` section 10.
3. The squeeze definition and event counts.
4. An order/fill simulator, tested on synthetic data.
5. PREREG.
6. One look at untouched data.
