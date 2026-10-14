# sigma_compare: report

PREREG `c75b24f`, committed before any code or result. Implementation and tests:
`1010e23`, with no real-data run before it. Implementation choices are in
[DECISIONS.md](DECISIONS.md). All tables are in
[results/summary_sigma.md](results/summary_sigma.md).

## 1. Verdict

**HAR on (L, S_hd, T) replaces the plug-in.** This is the pre-registered rule
applied literally. On CONF windows that don't cross a close, both challengers
beat the plug-in significantly on QLIKE at both horizons. The tie-break (both
qualify) picks HAR, which has the lower mean QLIKE.

| CONF, non-crossing | QLIKE plug-in | HAR | EWMA | DM HAR vs plug-in (BH p) | DM EWMA vs plug-in (BH p) |
|---|---:|---:|---:|---|---|
| H = 60 (n 68,040) | 0.3044 | **0.2493** | 0.2765 | −21.9 (5e-106), significant | −9.8 (7e-23), significant |
| H = 240 (n 58,920) | 0.2190 | **0.1838** | 0.2045 | −13.5 (2e-41), significant | −10.8 (2e-27), significant |

HAR lowers QLIKE by 18 % (60 min) and 16 % (240 min) against the plug-in. The
win is uniform:
- **Folds:** HAR is better in every fold (24/24 at both horizons) and better
  than EWMA in every fold.
- **Eras:** it holds in both eras (DM −16.1 / −17.3 at 60 min; −9.9 / −10.9 at
  240 min).
- **DEV:** it holds in the DEV data, which the rule doesn't use: 8/8 folds, DM
  −8.3 and −7.2.
- **log-MSE:** it also holds on log-MSE.

**Step 3** has fitted HAR on all pre-2024 data; see [PROPOSAL.md](PROPOSAL.md).
`sigma_over` itself is not changed.

## 2. Honesty note (from the PREREG)

- **CONF 2007–2020 is not independent.** L, S and T were selected on it in vol
  rounds 2 and 3, and round 3 already found that OLS on (L, S, T) forecasts
  log variance well there.
- **The plug-in's calibration** on DEV's test years had been looked at before
  (`tools/sigma_calibration`).
- **The independent test** is the sealed data from 2026-10-01 on.

## 3. Calibration (CONF, non-crossing; RV/h per row)

| H | candidate | mean | median | p10–p90 | ΣRV/Σh |
|---|---|---:|---:|---|---:|
| 60 | plug-in | 1.090 | 0.786 | 0.35–1.86 | 1.033 |
| 60 | HAR | **1.033** | 0.775 | 0.39–1.69 | 1.045 |
| 60 | EWMA | 1.133 | 0.838 | 0.40–1.92 | 1.020 |
| 240 | plug-in | 1.150 | 0.899 | 0.48–1.86 | 1.039 |
| 240 | HAR | **1.044** | 0.830 | 0.48–1.65 | 1.034 |
| 240 | EWMA | 1.109 | 0.875 | 0.48–1.78 | 1.039 |

- **Plug-in vs HAR:** the plug-in under-forecasts on average (mean RV/h is 9 %
  and 15 % above 1). HAR is closest to 1 and has the narrowest spread.
- **Medians below 1 are expected.** Realised variance is right-skewed, so a
  forecast that is right on average is above the typical value.

## 4. Path metric (report only; σ_px = close_ref·√h)

Share of windows where price touches +kσ or −kσ, CONF non-crossing. The
normal-theory value is in brackets.

| H | candidate | k = 0.5 (0.617) | k = 1 (0.317) | k = 1.5 (0.134) | k = 2 (0.046) |
|---|---|---|---|---|---|
| 60 | plug-in | 0.531 / 0.539 | 0.241 / 0.247 | 0.104 / 0.113 | 0.048 / 0.055 |
| 60 | HAR | 0.532 / 0.538 | 0.236 / 0.244 | 0.101 / 0.110 | 0.045 / 0.053 |
| 240 | plug-in | 0.556 / 0.559 | 0.262 / 0.273 | 0.118 / 0.130 | 0.056 / 0.066 |
| 240 | HAR | 0.544 / 0.547 | 0.246 / 0.258 | 0.106 / 0.118 | 0.049 / 0.058 |

(Values are up / down. EWMA is in the tables file.)

- **All candidates** touch less often than normal theory at k ≤ 1.5. At k = 1
  the rate is about 0.24–0.27 against 0.317. At k = 2 they are at or slightly
  above theory. The moves are peaked with fat tails, as the calibration tool
  had already seen.
- **For brackets:** a stop at k ≤ 1·σ is hit less often than Brownian theory
  says, and a stop at 2σ about as often.
- **HAR vs plug-in:** at 240 min, HAR's touch rates are a little lower than
  the plug-in's (by 0.007–0.016).

## 5. Windows crossing a close (reported only)

- **CONF:** HAR beats the plug-in at both horizons (DM −2.1, p 0.017 at 60 min;
  −4.6, p 2e-6 at 240 min). EWMA does not (+1.1 and −1.6).
- **DEV:** the samples are small (489 / 1,972 windows) and nothing is clearly
  significant at 240 min. All three forecasts are too high there (ΣRV/Σh
  0.58–0.75).
- **QLIKE is 1.3–5.6 × higher** than in non-crossing windows. σ leaves out the
  reopen gap by construction, and the minutes after the reopen behave
  differently. Whether such trades are allowed stays with the agents.

## 6. Fitted per fold (exploratory)

| | b0 | bL | bS_hd | bT | smearing c | EWMA half-life |
|---|---|---|---|---|---|---|
| CONF H = 60 | −0.23 .. −0.16 | 0.98 .. 1.00 | +0.42 .. +0.46 | −0.24 .. −0.19 | 1.14 .. 1.28 | 120 in every fold |
| CONF H = 240 | −0.14 .. −0.09 | 0.95 .. 0.97 | +0.24 .. +0.27 | −0.33 .. −0.23 | 1.09 .. 1.20 | 960 in every fold |
| DEV H = 60 | −0.28 .. −0.24 | 0.90 .. 0.92 | +0.47 .. +0.50 | −0.17 .. −0.12 | 1.25 .. 1.28 | 60 |
| DEV H = 240 | −0.23 .. −0.17 | 0.82 .. 0.86 | +0.30 .. +0.34 | −0.27 .. −0.19 | 1.23 .. 1.29 | 240–480 |

- **How HAR differs from the plug-in.** HAR is the plug-in's level (bL ≈ 1)
  plus two terms:
  - the last hour against the last 24h (S_hd, +);
  - mean reversion from the 24h against the 120h level (T, −).
- **Stability.** The coefficients barely move across 14 years of folds. bL is
  lower in 2021–23 (≈ 0.9 against ≈ 0.97), so there is more mean reversion in
  the recent data.

## 7. Process

- **Run once.** There were no deviations from the PREREG and no amendments.
- **Rerun.** A clean rerun from scratch reproduced the results exactly:
  `results_sigma.json` and `coefs.csv` are byte-identical, and the gzipped row
  files have identical contents.
- **Plug-in identity.** 2,400 CONF and 800 DEV sampled rows matched
  `sigma_over` to 1e-9, and none were skipped.
- **Seal.** All asserts passed: no row at or after 2024-01-01, CONF < 2021-01-01,
  every window ends by its period end. Nothing from the spent or sealed periods
  was read.
- **Tests.** 13 synthetic tests (no look-ahead, fold separation, rule logic,
  oracle check) pass, and the full root pytest passes too.

## 8. What this does not show

- Calendar effects (news, holidays): out of scope.
- Live bars (IBKR midpoints) and the period from 2024 on.
- Horizons other than 60 and 240 minutes. HAR coefficients exist only for these
  two.
- Whether HAR's better σ improves bracket outcomes (P&L). The path metric is
  descriptive only.
