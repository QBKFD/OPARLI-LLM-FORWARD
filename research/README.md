# research/

Pre-registered studies on XAUUSD, in the order they were run. Each one has:
- `PREREG.md`, committed before any result;
- `DECISIONS.md`;
- a `REPORT.md` whose first section is the verdict, under the pre-registered
  rules.

This index only summarises those verdicts. When it and a report differ, the
report is right.

## Data seal (read first)

`SEALING.md` is enforced by `sealing.py` (`assert_before_global_seal`, tested in
`test_sealing.py`):
- **Sealed:** everything from **2026-10-01** onward, reserved for the final
  strategy test. Nothing may read it.
- **Spent:** 2024-01-01 .. 2026-01-30, used once by `levels_confirm/`, and now
  seen data.
- Studies before `levels_confirm/` assert timestamps before 2024-01-01.

## Studies

| # | Study | Question | PREREG | Data | Status (per its REPORT.md) |
|---|---|---|---|---|---|
| 1 | [`vol_features/`](vol_features/REPORT.md) | Can 3 volatility features replace the 13 in the regime classifier? | `5840a48` | 1H bars, 2021–2023 (2020 warm-up) | **Not adopted.** D1 (redundancy) fails at 4h; the 3 match the 13 at 24h. Exploratory: the 4h gap is time-of-day information. |
| 2 | [`vol_features_r2/`](vol_features_r2/REPORT.md) | Round 2: block L, S, T (+W) vs the 13 + hour dummies | `08e612a` | CONF `XAUUSD.txt` 2007–2020; DEV parquet 2021–2023 | **Status quo by the rules.** R1 passes by far (4h R² 0.71 vs 0.52), but R2 (no clock) fails, and the mapping then says "not recommended". W dropped. |
| 3 | [`vol_features_r3/`](vol_features_r3/REPORT.md) | Round 3: mechanical fix (per-minute means, no W) | `af72ba1` | CONF 2007–2020 (second use); DEV 2021–2023 | **R2 fails narrowly** (4h hour gain 0.0119 vs a 0.01 limit). R1 and R5 pass: L +, S +, T − in 24/24 folds. |
| 4 | [`vol_block/`](vol_block/VOL_BLOCK_DECISION.md) | Lock a volatility block for the regime model | (decision, not a study) | rounds 2–3 | **Locked as an engineering decision**: L, S_hd, T (`VolBlock`). `VOL_BLOCK_DECISION.md` keeps confirmed and unconfirmed parts apart. |
| 5 | [`trend_features/`](trend_features/REPORT.md) | Does direction-neutral trend strength predict clean moves vs chop (4h / 24h)? | `8d89ef4` | CONF 2007–2020 (first use for trend); DEV 2021–2023 | **Not recommended.** R2b fails by 0.00007 at 24h. There is a small but significant 24h information gain (Z24: trend exhaustion). |
| 6 | [`regime_v2/`](regime_v2/REPORT.md) | Do causal jump-model regime labels on the locked block beat simple baselines? | `712a23d` | CONF 2007–2020 (not fully independent: the block was selected on it); DEV 2021–2023 | **"The jump model adds nothing over L terciles; use BASE-L3."** S1 passes, S2 fails everywhere: L terciles are significantly better. |
| 7 | [`levels/`](levels/REPORT.md) | Does gold behave differently at PDH/PDL/WH/WL than at placebo levels? | `f0e3fd4` | `XAUUSD.txt` 1-min; CONF 2007–2020, REPL 2021–2023 | **0 of 6 hypotheses supported.** PDH/PDL breaks continue *less* than placebo breaks (−7.3 pp): the opposite of the pre-registered direction. |
| 8 | [`levels_confirm/`](levels_confirm/REPORT.md) | Confirm: near-open PDH/PDL breaks fail more than distance-matched placebos | `305d1d0` | **2024-01-01 .. 2026-01-30** (previously sealed, now spent) | **REJECTED.** The effect is +11.3 pp [+4.0, +18.5]: near-open breaks continued *more*, not less. |
| 9 | [`levels_diag/`](levels_diag/REPORT.md) | Diagnostic of the levels results; exploratory only | plan `4e36123` (+ addenda `a851caf`, `a3a955c`) | 2007–2023 (seen) | **Stopped at machinery validation**, twice. NEAR A/A coverage is 0.875–0.905 under every bootstrap, outside 93–97 %. Q1–Q5 not run. |
| 10 | [`sigma_compare/`](sigma_compare/REPORT.md) | Which sigma forecast should risk/ and scoring use: the plug-in, HAR on (L, S_hd, T) or EWMA? | `c75b24f` (+ post-hoc amendment `f14102d`) | CONF `XAUUSD.txt` 2007–2020; DEV parquet 2021–2023 | **HAR replaces the plug-in.** QLIKE 0.249 vs 0.304 (60 min) and 0.184 vs 0.219 (240 min); 24/24 folds. Post hoc: still wins frozen on the parquet 2021–23 and when fitted on non-crossing rows only. |
| 11 | [`agents_v1/`](agents_v1/PREREG.md) | Does the Technical agent's direction beat always-long and always-short, and do its own SL/TP beat the neutral k·σ bracket? | **DRAFT** (not yet pre-registered) | Evidence: live shadow decisions after 2026-10-01 only; dev backtest 2007–2023 | **Plan only.** Design in `docs/AGENTS_TECHNICAL_DESIGN.md`. |
| 12 | [`level_target/`](level_target/SPEC.md) | Does a trade from a scanner-alerted level to the next level (stop beyond the touched level, 240-min cap) earn mean net R > 0, and more than random entry times with the same σ distances? | **SPEC only** (no PREREG, no code) | Develop `XAUUSD.txt` 2007–2020; holdout parquet 2021–2023 (one look); evidence: sealed forward test | **Specified, not started.** |

## Also here

- `_shared/regime_features/`: byte-identical copies of the regime feature
  engine and config from `regime_classifier/` (tag `legacy-agents-v0`; see its
  `PROVENANCE.md`). vol_features r1–r3, trend_features and regime_v2 load them
  from here.
- `sealing.py`, `SEALING.md`, `test_sealing.py`: the seal above.

## Running the tests

`pytest research` from the repo root, with the `[research]` extra installed.
Tests that need the untracked data (`database/ohlcv_data/`, the long-history
`XAUUSD.txt`, `research/vol_features_r2/cache`) skip themselves when it's
missing.
