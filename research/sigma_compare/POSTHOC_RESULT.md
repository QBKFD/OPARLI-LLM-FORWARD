# Post-hoc checks: result

The method is in POSTHOC.md, committed (`fb8ba0d`) before either check was run.
The script, `posthoc.py`, was committed (`0d13746`) before its single run. The
numbers are in `results/posthoc.json`. These are post-hoc checks, not
pre-registered tests.

**Gate: PASS.** HAR passes both checks, so the datafeed change is built. The
default fit is **txt2007_2023** (LONG).

## P1: frozen model on a later era (PASS)

HAR and its VolParams were fitted once on `XAUUSD.txt` 2007–2020 and then
scored, with no refit, on the parquet 2021–2023 (non-crossing windows).

| H | n | QLIKE plug-in | QLIKE HAR | DM (BH p) | mean RV/h, HAR / plug-in | RMS z, HAR / plug-in |
|---:|---:|---:|---:|---|---|---|
| 60 | 16,921 | 0.2722 | **0.2142** | −14.86 (6e-50) | 0.997 / 1.087 | 0.968 / 1.010 |
| 240 | 14,608 | 0.1987 | **0.1601** | −11.23 (2e-29) | 1.049 / 1.196 | 0.991 / 1.060 |

- Frozen for three years, HAR still lowers QLIKE by 21 % at 60 min and 19 % at
  240 min.
- Its fitted coefficients are close to the step 3 fit:
  - H = 60: β = (−0.234, 0.984, 0.447, −0.223), c = 1.270;
  - H = 240: β = (−0.139, 0.950, 0.266, −0.310), c = 1.196.

## P2: parquet calibration (PASS, favours LONG)

Walk-forward over the 8 DEV quarters (2022–2023), on the same 11,273 (60 min)
and 9,733 (240 min) non-crossing rows for both families. The two files hold
identical bars here.

| H | family | HAR mean RV/h | HAR RMS z | HAR QLIKE | plug-in QLIKE |
|---:|---|---:|---:|---:|---:|
| 60 | LONG (txt from 2007) | **0.998** | 0.973 | 0.209 | 0.264 |
| 60 | SHORT (parquet from 2021) | 0.994 | 0.968 | 0.196 | 0.245 |
| 240 | LONG | 1.044 | **1.000** | 0.157 | 0.196 |
| 240 | SHORT | 0.982 | 0.969 | 0.145 | 0.174 |

- **Score** (mean |ln| over the 4 numbers): LONG 0.0182, SHORT 0.0218, so
  **LONG is favoured.** The tie margin (0.005) wasn't needed: LONG is lower
  outright.
- **Pass:** all four of LONG's numbers (0.998, 0.973, 1.044, 1.000) are within
  [0.9, 1.1].

## How to read it

- **The default follows the calibration rule, as instructed.** On QLIKE, SHORT
  is better (0.196 vs 0.209 and 0.145 vs 0.157), because SHORT is fitted to the
  recent regime (bL ≈ 0.85–0.92 against ≈ 0.95–0.98). So "best calibrated" and
  "lowest QLIKE" point to different fits, and the margin on calibration is
  small. Both fits ship and are selectable by config, so SHORT is one setting
  away.
- **Already-seen numbers:** SHORT's HAR mean RV/h on DEV (0.994 / 0.982) had
  already been reported in REPORT.md. LONG's numbers, the RMS z-ratios and P1
  were new.
- **The plug-in on the same rows** under-forecasts variance (mean RV/h
  1.05–1.20) and does worse on QLIKE in every cell.
