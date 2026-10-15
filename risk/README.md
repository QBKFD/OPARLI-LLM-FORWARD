# risk

Stop/target bracket and position sizing for XAUUSD.

**Status: built, not used yet.** Nothing imports it. The agents' risk node is
still a pass-through (`agents/nodes/risk.py`), and the scanner doesn't use it
either.

| Module | API |
|---|---|
| `bracket.py` | `bracket(*, entry, direction, sigma, k) -> Bracket(direction, entry, stop, target, k, sigma)`. For a long, stop = entry − k·σ and target = entry + 2k·σ (`REWARD_RISK = 2`); a short is mirrored. |
| `sizing.py` | `position_size(*, equity, risk_pct, entry, stop, usd_per_unit_move, size_increment) -> float`. Size = equity × risk_pct/100 ÷ (\|entry − stop\| × usd_per_unit_move), rounded **down** to a multiple of `size_increment`. |

## Units and rules

- **sigma** is in price units over the trade's horizon:
  `datafeed.features.vol_block.sigma_over(params, state, H).sigma_px`.
  - That sigma is an **engineering decision, not a research result**.
  - Its check on pre-2024 data is in `tools/sigma_calibration/RESULT.md`:
    - close to 1 inside market hours;
    - too wide for 4h windows that cross a close;
    - stop touches (the path) not measured.
- **k** has no default. The caller chooses it, and it must not be tuned on
  results.
- **risk_pct** is in **percent**: 0.5 means 0.5 % of equity. Anything above
  `MAX_RISK_PCT = 5` raises.
- **`XAUUSD_USD_PER_UNIT_MOVE = 1.0` and `XAUUSD_SIZE_INCREMENT = 1.0` are
  UNVERIFIED.** Before any live use, check them against IBKR contract details
  for CMDTY XAUUSD (`multiplier`, `minSize`, `sizeIncrement`). They are never
  applied implicitly: every argument is required and keyword-only.
- **Errors raise `ValueError`.** That covers direction other than long/short
  (including "none"), stop == entry, and any input that is not a finite number
  > 0. A long whose stop would be ≤ 0, or a short whose target would be ≤ 0,
  also raises.
- **A budget below one increment** gives size 0.0, which is not an error.
- **Sizing is exact.** It works in Decimal on each input's shortest repr, so a
  float difference such as 2058.59 − 2038.59 = 20.000000000000227 can't cost
  an increment.

## Import rules

- risk/ imports only the stdlib, itself and `datafeed.features`.
- Nothing in datafeed/ imports risk/.

`tests/risk_tests/test_import_rules.py` enforces both rules with a static AST
check. At runtime, `import risk` loads no third-party module.

## How the pieces fit (later)

```python
from datafeed.features import vol_block as vb
from risk.bracket import bracket
from risk.sizing import XAUUSD_SIZE_INCREMENT, XAUUSD_USD_PER_UNIT_MOVE, position_size

state = vb.read_state(params, "XAUUSD", t)          # params: a frozen VolParams
if state.reason is None:
    sig = vb.sigma_over(params, state, 240)
    # sig.crosses_close: the agents decide whether such a trade is allowed
    b = bracket(entry=state.close_ref, direction="long", sigma=sig.sigma_px, k=k)
    size = position_size(equity=equity, risk_pct=0.5, entry=b.entry, stop=b.stop,
                         usd_per_unit_move=XAUUSD_USD_PER_UNIT_MOVE,
                         size_increment=XAUUSD_SIZE_INCREMENT)
```

## Open

- Verify the two XAUUSD constants.
- Choose k, the horizon H, and the training window for the live `VolParams`
  (`vol_block.fit` takes it explicitly; anything ending after 2026-10-01 is
  refused).
- Decide whether trades whose horizon crosses the daily break or the weekend
  are allowed (the agents decide).

Tests: `pytest tests/risk_tests`.
