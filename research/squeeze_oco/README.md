# squeeze_oco: σ-timed OCO breakouts on XAUUSD

**Status: EXPLORATORY, Stage 1. Not a PREREG. Nothing here is evidence.**

- **Q1 (precondition):** does a run of small candles (small relative to σ) on the 15- or 30-minute chart
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

## Rules

- **Rule number one.** Every number, threshold, window, cost, seed or definition comes from the notebook's
  register. Code reads it through `need(id)`, which raises unless the entry is APPROVED. A missing value is
  added as OPEN or PROPOSED, with the reasoning, and work stops there.
- **The dev period only:** 2007-01-01 .. 2020-12-31 UTC. Every frame goes through `stage1.dev_guard`.
- **Data licence.** `XAUUSD.txt` is IBKR data, for the owner's own use only. Committed outputs show aggregates
  and charts only, never a price row.

## Running it

From the repo root, with the `[research]` extra plus `nbclient` and `ipykernel`, and with the untracked data in
place (`database/ohlcv_data/XAUUSD.txt` and `research/vol_features_r2/cache/`):

    python -m pytest research/squeeze_oco -q
    jupyter nbconvert --to notebook --execute --inplace research/squeeze_oco/01_vol_state.ipynb

## Stages

1. The vol state, described, and HAR's calibration on the dev period (descriptive). Blocked after step 1 until
   EVAL_GRID, DESCRIBE_QUANTILES and DESCRIBE_HOUR are approved.
2. Q1: the small-candle pattern against HAR, after its parameters are approved.
3. The squeeze definition and event counts.
4. An order/fill simulator, tested on synthetic data.
5. PREREG.
6. One look at untouched data.
