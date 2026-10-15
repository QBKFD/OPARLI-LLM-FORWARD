# Sigma calibration check: result

**This is an engineering check, not a research result.** Sigma over H minutes is
an engineering decision (owner, 2026-10-14; `datafeed/features/vol_block.py`).
The method is in `calibrate.py`'s docstring, and it was committed (e11c7a5) before
this single run. Data: the research parquet before 2024-01-01, which is already
seen (it was research development data). Fit on 2021, evaluate 2022–2023 at hourly labels.
Numbers: [results/calibration.csv](results/calibration.csv).

The 200 random (t, H) pairs agreed with `sigma_over(state_at(...))`. No bar falls
outside `_market_open`.

## Result

`ratio` = √mean(z²), with z = (log move from the last close before t to the
last close before t+H) / predicted sigma. A value of 1 means calibrated. For
reference, a normal distribution has cover1 = 0.683 and cover2 = 0.954.

| H (min) | crosses close | n | ratio | 2022 | 2023 | cover1 | cover2 | rv_ratio | median σ (USD) |
|---:|:--:|---:|---:|---:|---:|---:|---:|---:|---:|
| 15 | no | 11,287 | **0.967** | 0.971 | 0.964 | 0.775 | 0.959 | 0.832 | 1.51 |
| 60 | no | 11,273 | **0.980** | 0.967 | 0.992 | 0.768 | 0.956 | 0.906 | 2.77 |
| 240 | no | 9,733 | **1.006** | 0.981 | 1.031 | 0.746 | 0.950 | 0.916 | 6.00 |
| 240 | yes | 1,890 | **0.831** | 0.700 | 0.944 | 0.881 | 0.976 | 0.544 | 4.63 |
| 1440 | yes (always) | 11,332 | **1.041** | 0.982 | 1.096 | 0.721 | 0.939 | 0.871 | 14.87 |

The reading guide was fixed before the run:
- **Close to 1 (0.9–1.1):** every window that stays in market hours (15, 60 and
  240 min, both years), and 24h. The 24h windows always cross the daily break,
  so their moves include the gaps that sigma leaves out.
- **Noticeably off:** 240 min windows that cross a close, at 0.83. Realised moves
  are smaller than sigma, so brackets would be wider than needed. In 2022 the
  value is 0.70, which the guide calls "far from 1"; in 2023 it is 0.94.

## What else it shows (descriptive)

- **The typical move is smaller than σ, and the tails are heavier.** About 75 %
  of moves lie within ±1σ (normal: 68 %), and about 95 % within ±2σ. So the RMS
  matches, but the distribution is peaked. That matters for where a k·σ stop sits.
- **The pooled variance level is 8–17 % high** (rv_ratio 0.83–0.92 in market
  hours). rv_ratio weights high-volatility windows more, while `ratio` weights
  every window equally. A possible explanation, **not tested**: the plug-in
  assumes the current level persists, but volatility mean-reverts, which is
  what research's negative T coefficient points to. So σ runs high after
  volatile spells.

## What it does not cover

- **The path.** A stop is hit by the path, not by the move to the end of the
  window. Touch probabilities for k·σ stops were not measured.
- **Mid-hour starts.** Labels fall on the hour and the daily break starts on the
  hour, so 15 and 60 min windows here never cross a close. Live t can be
  mid-hour.
- **Other fits.** There was one fit (2021) and one evaluation span. The windows
  overlap, so there are no confidence intervals.
- **Live bars.** The check used vendor parquet bars, not live bid/ask-midpoint
  bars.
