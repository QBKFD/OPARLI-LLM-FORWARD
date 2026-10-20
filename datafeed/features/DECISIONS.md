# Decisions: HAR behind `sigma_over` (feat/sigma-har, overnight 2026-10-15)

Made unattended. The rule was the conservative choice on any decision, logged
here.

1. **Gate.** The change was built only because both post-hoc checks passed.
   - P1: HAR frozen on 2007–2020 still beats the plug-in on 2021–2023.
   - P2: its calibration on 2022–2023 lies within [0.9, 1.1].
   - The checks were defined before they were run, because the instruction
     didn't define them. Details: `research/sigma_compare/POSTHOC.md` and
     `POSTHOC_RESULT.md`, commits fb8ba0d and de17ffc on the local, unpushed
     branch `research/sigma-compare`.

2. **Branch base: `origin/main` (05fe838).** Local `main` is behind it at
   9042270, and `main` itself is not touched.

3. **Default model: `txt2007_2023`.** This is the fit the parquet calibration
   check favoured (P2 score 0.0182 vs 0.0218, mean |ln| of mean RV/h and the
   RMS z-ratio at 60 and 240 min).
   - The parquet fit (`parquet2021_2023`) had the lower QLIKE on 2022–2023, so
     the two criteria disagree, and the calibration margin is small.
   - The instruction says to follow the calibration. A tie would also have
     gone to the txt fit, which the study validated walk-forward.
   - Select the parquet fit with `DATAFEED_SIGMA_MODEL=parquet2021_2023`.

4. **The SHA-256 is checked twice on every load:**
   - against the hash stated in the file;
   - against a value pinned in `vol_block.SIGMA_MODELS`.
   So a file that is edited and re-hashed still fails. The files are copied
   byte for byte from `research/sigma_compare/model/`.

5. **Unknown model names raise**, whether passed as an argument or set in
   `$DATAFEED_SIGMA_MODEL`. There's no silent fall-back to the default.

6. **Window = the next H market-open minutes,** as fitted. `open_minutes` is
   always H, and `crosses_close` is True when the window spans closed
   minutes. The search stops after 7 days without H open minutes, and then
   raises.

7. **Only H = 60 and 240.** Any other H raises, including 60.0, True and
   strings. There is no interpolation.

8. **The plug-in is kept as `sigma_plugin`,** with unchanged maths, for
   monitoring and comparison. Its callers were renamed mechanically:
   - the plug-in tests;
   - `tools/sigma_calibration/calibrate.py`. That tool was rerun:
     `results/calibration.csv` is unchanged byte for byte, and all 200
     cross-checks pass.
   - `tools/sigma_calibration/RESULT.md` still says `sigma_over`, as at the
     time of its run, and is left as written.

9. **`sigma_over` now takes a `SigmaModel`,** not `VolParams`. That breaks the
   old signature on purpose, so a forgotten caller fails loudly instead of
   getting the other sigma. On `origin/main` the only callers were the tests,
   the tool and the `risk/README.md` example, and all three were updated.
   Nothing in `agents/` calls it yet.

10. **The state must come from `model.params`.** This is documented but not
    enforced: `VolState` doesn't record which `VolParams` produced it, and
    changing that would touch every `VolState` user.

11. **Tests pin the production code to the research reference.** Golden values
    from `research/sigma_compare/har_sigma.py` cover both models, crossing and
    non-crossing windows, mid-minute t and both DST seasons. They must match
    exactly (bit for bit).

## Owner review (2026-10-15)

12. **The default model is now `parquet2021_2023`** (owner's decision;
    supersedes #3). Reasons:
    - QLIKE is the study's primary metric, and it favours the parquet fit on
      the 2022–2023 out-of-sample quarters (0.196 vs 0.209 at 60 min, 0.145 vs
      0.157 at 240 min);
    - it comes from the same data source as live;
    - its calibration gap to the txt fit (P2 score 0.022 vs 0.018) is within
      noise.
    `txt2007_2023` stays selectable with `DATAFEED_SIGMA_MODEL=txt2007_2023`.

    Note (added when applying): `research/vol_features/DATA_AUDIT_LONG.md`
    found the parquet identical to `XAUUSD.txt` close for close for 2020–2023.
    So what separates the two fits is the training window (2021–23 against
    2007–23), not the data source. The QLIKE reason is unaffected.

13. **#10 is now enforced** (supersedes #10).
    - `VolParams.sha256` (the SHA-256 of `to_json()`) identifies a parameter
      set. `state_at`, and so `read_state`, stamps it on every `VolState` as
      `params_sha256`, a new last field defaulting to None. Positional
      construction is unchanged.
    - `sigma_over` raises when the state's hash differs from
      `model.params.sha256`. A hand-built state without a hash also raises.
    - `sigma_plugin` takes its `VolParams` explicitly and doesn't check the
      hash; it is unchanged.
    - Tested:
      - a state from the txt params is refused by the parquet model;
      - an unstamped state is refused;
      - the hash survives a JSON round trip and changes with the content.
