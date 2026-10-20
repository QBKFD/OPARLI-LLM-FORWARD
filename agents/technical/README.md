# agents/technical

The Technical metric table ([docs/AGENTS_ARCHITECTURE.md](../../docs/AGENTS_ARCHITECTURE.md)
§4.3). The snapshot builder computes it for every snapshot, and two consumers
share it:
- the Technical-LLM reads it through `{{metrics_table}}`;
- the deterministic Technical v1 agent (agents v1) will read
  `snapshot.metrics`, through `metric(rows, name)`.

| Metric | Timeframes | Definition | Unit |
|---|---|---|---|
| `atr14_<tf>` | 15m, 1h, 4h | ATR(14), Wilder (seed: mean of the first 14 TRs) | $ |
| `atr14_<tf>_sigma` | 15m, 1h, 4h | ATR / `sigma_px_60` | σ |
| `rsi14_<tf>` | 15m, 1h, 4h | RSI(14), Wilder; no losses → 100; a flat series → unavailable | 0–100 |
| `ema_spread_<tf>` | 15m, 1h, 4h | (EMA9 − EMA21) / `sigma_px_60`; EMA seeded with the mean of the first n | σ |
| `sigma_px_60`, `sigma_px_240` | — | HAR σ of the price change over 60 / 240 min (`agents/features.py`) | $ |
| `L`, `S_hd`, `T` | — | the vol state (datafeed `vol_block`) | — |
| `dist_sigma_<level_id>` | — | (last complete 1m close − level) / `sigma_px_240` | σ |
| `volume`, `vwap` | — | always unavailable: no volume source (CFD midpoint bars) | — |

**Rules:**
- **Complete candles only.**
- **Never imputed:** too few candles, no σ, or a non-finite value gives
  `available = false` and the reason.
- **Scale invariance:** the σ-relative metrics and RSI don't change when
  prices and σ are scaled together (tested).

```
pytest tests/agents_tests/test_metrics.py
```

The RSI arithmetic is checked against the StockCharts RSI(14) worked example.
