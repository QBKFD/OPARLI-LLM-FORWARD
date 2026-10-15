# scoring

Outcome of a bracketed XAUUSD trade, walked forward on 1-min bars.

**Status: built, not used yet.** The agents' risk node is still a pass-through,
and nothing calls `score` in production.

```python
from datafeed.features import vol_block as vb
from scoring.outcome import read_bars, score, score_neutral

model = vb.load_sigma_model()                        # HAR, SHA-256 checked
state = vb.read_state(model.params, "XAUUSD", entry_time)
bars = read_bars("XAUUSD", entry_time, 240)          # store bars of the holding period
# neutral bracket: risk.bracket with the HAR sigma over the holding period (stop k·σ, target 2k·σ)
ns = score_neutral(entry_time=entry_time, direction="long", entry_price=entry, k=1.0,
                   max_hold_minutes=240, bars=bars, model=model, state=state)
# ns.bracket, ns.sigma (incl. crosses_close), ns.outcome
# the agent's own stop/target, on the same bars
agent = score(entry_time=entry_time, direction="long", entry_price=entry,
              stop=agent_stop, target=agent_target, max_hold_minutes=240, bars=bars)
```

- **`score_neutral` uses the HAR sigma:**
  `vol_block.sigma_over(model, state, max_hold_minutes)`.
- **Constraints that follow:**
  - the holding period must be a fitted horizon (60 or 240 min);
  - the state must be as of the entry time;
  - the state must come from `model.params`.
  Anything else raises.

## What `score` returns

`Outcome`:
- `reason`: `target`, `stop` or `timeout`.
- `exit_time`: the open time of the exit bar.
- `exit_price`.
- `r_multiple`.
- `mae`, `mfe`: price units ≥ 0, plus `mae_r` and `mfe_r`.
- `bars_used`.
- `gap`: True when the stop filled at a gapped open.

## Rules

- **Bars used:** only bars that open at or after `entry_time` and end by
  `entry_time + max_hold`. A bar that opened before entry is never used, even
  when entry is mid-minute.
- **Both levels in one bar:** if a bar's range holds both the stop and the
  target, it is a **stop**.
- **Gaps:**
  - A bar that opens beyond the stop exits at that **open**.
  - A bar that opens beyond the target exits at the **target**, with no credit
    for the better open.
- **Timeout:** the exit is the close of the last bar inside the holding period.
- **R** = (exit − entry) / |entry − stop| for a long, mirrored for a short.
- **MAE / MFE:** the bars before the exit bar count in full. In the exit bar the
  order of high and low is unknown:
  - MAE takes the adverse extreme (the exit price at a stop);
  - MFE takes only what is certain (the target at a target exit, else the
    bar's open).
- **Inconsistent bars:** a bar whose high is below its open or close (or low
  above) is widened to hold them. The clean parquet has none before 2024.
- **Errors:**
  - no bar in the holding period raises `NoBarsError`;
  - inconsistent inputs raise `ValueError`. These are: stop or target on the
    wrong side, a direction other than long/short, a non-positive or
    non-finite price, an H that is not a positive int, a naive time, or bars
    that don't increase in time.

## Imports

- `scoring/` imports only the stdlib, `risk/`, `datafeed.store` and
  `datafeed.features`. The last is imported inside `score_neutral`, so
  `score()` stays stdlib-only.
- Nothing in `datafeed/` or `risk/` imports `scoring/`.

Both rules are checked by `tests/scoring_tests/test_scoring_package.py`. The
decisions are in `DECISIONS.md`.

Tests: `pytest tests/scoring_tests`. The real-bar cases use the research
parquet and skip without it.
