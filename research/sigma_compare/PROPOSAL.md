# Proposal: HAR sigma in place of the plug-in in `vol_block.sigma_over`

**Status: proposal only (step 3). Nothing in `datafeed/`, `risk/` or `agents/`
is changed.**

The basis is [REPORT.md](REPORT.md) §1. On CONF, HAR on (L, S_hd, T) beats the
plug-in on QLIKE:
- by 18 % at 60 min and 16 % at 240 min;
- in 24/24 folds, in both eras, and in DEV 8/8.

## The fitted model

`research/sigma_compare/model/sigma_har_v1.json`:
- **Written by:** `fit_final.py`. A rerun reproduces it byte for byte.
- **sha256** `1af79da80ce8ae4de483ff09113150ed361e7d95221f00385e361f2bdbffc1cd`.
  The hash covers the canonical JSON of every other field.
- **Data:** `XAUUSD.txt` from 2007-01-01 to 2023-12-29, 6,045,394 bars. That is
  all usable pre-2024 data; the parquet holds the same data from 2020. The file
  records the input file's sha256 as well.
- **Contents:**
  - the `VolParams` (seasonal profile and S_hd hour means), fitted on the same
    bars;
  - per horizon, the OLS coefficients and the smearing factor.

| H | b0 | bL | bS_hd | bT | smearing c | training rows |
|---:|---:|---:|---:|---:|---:|---:|
| 60 | −0.2354 | 0.9782 | 0.4574 | −0.2143 | 1.2717 | 99,386 |
| 240 | −0.1416 | 0.9418 | 0.2731 | −0.3029 | 1.2052 | 98,999 |

These sit inside or next to the per-fold ranges. bL is slightly lower than in
the CONF folds because 2021–23 is now included, where bL was 0.82–0.92.

## Proposed change (sketch, not applied)

In `datafeed/features/vol_block.py`:

```python
@dataclass(frozen=True)
class SigmaModel:
    version: int
    sha256: str
    params: VolParams                 # the profile and hour means the model was fitted with
    horizons: dict[int, tuple]        # H -> (beta[4], smear_c)

def load_sigma_model(path) -> SigmaModel: ...    # raises unless the sha256 matches

def sigma_over(model: SigmaModel, state: VolState, H: int) -> Sigma:
    # H must be a fitted horizon (60, 240), else ValueError
    # window: the first H market-open minutes from t
    # var = P(t, H) · exp(beta · [1, L, S_hd, T]) · smear_c
    # Sigma(H, sigma_ret, sigma_px = close_ref·sigma_ret, open_minutes=H, crosses_close)
```

- **State:** it must come from `state_at` / `read_state` with `model.params`,
  so that L, S_hd and T use the profile and hour means the model was fitted with.
- **Plug-in:** the current function could stay, renamed `sigma_plugin`, for
  monitoring.
- **Reference implementation:** `har_sigma.py`.
  `test_proposed_sigma_equals_study_har_forecast` shows it reproduces the
  study's HAR forecasts (rel. 1e-9), on windows that cross a close and on ones
  that don't.

## Decisions for the owner

1. **Window semantics.**
   - Today `sigma_over` sums the calendar minutes [t, t+H), with closed minutes
     counting zero.
   - The study fitted and tested the next H *open* minutes.
   - The two agree whenever the window doesn't cross a close.
   - **Recommendation:** switch to open minutes, because that is what the
     coefficients mean. Keep `crosses_close`.
2. **Horizons.** Coefficients exist for 60 and 240 only.
   - **Recommendation:** refuse other H rather than interpolate. A new H needs
     its own fit, at least.
3. **Live VolParams.** Accepting this fixes them to the file's 2007–2023 fit,
   which settles that open item. How often to refit is not decided.
4. **Crossing windows.** On CONF, HAR beat the plug-in there too, but every
   forecast was too high in DEV (ΣRV/Σh 0.58–0.75). `crosses_close` keeps
   flagging these windows, and the agents decide.
5. **Packaging.** The JSON would need to ship with `datafeed`, as package data
   or a configured path, with the hash checked on load.
6. **Tests when applying:**
   - equality with `har_sigma.py` on fixtures;
   - the hash check;
   - refusal of unfitted H.

## Caveats (from the report)

- **The evidence is in-sample for the vol block.** CONF was used to select L,
  S and T, so it is not independent. The sealed data (2026-10-01 on) is the
  independent test.
- **Live IBKR midpoint bars were not tested.**
- **Path:** for every candidate, touches of ±k·σ are rarer than normal theory
  at k ≤ 1.5 and about at theory at k = 2. Keep this in mind when choosing k
  (not on results).
