# Touch rate on seen XAUUSD data (GEX level test, planning)

Made by `touch_rate.py` (its docstring has the rules, fixed before the count). **No GEX data and no outcomes:** only whether price touched a stand-in level after 05:00 UTC. Data: 2007–2023 (CONF 2007–2020, DEV 2021–2023) through the sealed research loaders.

- 202 expiry cycles, 1756 window days; 274 days skipped (fewer than 3 grid levels in the band: 263, no 20-day volatility: 10, no bar in the hour before 05:00 UTC: 1).
- Levels that price started on (not counted as touches): U 4.6%, M 3.1%, L 2.3%.

| Placement | Period | Level-days | Touched | q | Median \|z\| | Cycles for Δ = 15 pp |
|---|---|---|---|---|---|---|
| U: 3 nearest (upper bound) | CONF | 4,332 | 1,526 | 0.352 | 0.41 | 13.6 |
|  | DEV | 936 | 249 | 0.266 | 0.51 | 17.5 |
|  | all | 5,268 | 1,775 | 0.337 | 0.43 | 14.2 |
| M: near-the-money weighted (central) | CONF | 4,332 | 969 | 0.224 | 0.75 | 20.5 |
|  | DEV | 936 | 170 | 0.182 | 0.78 | 24.8 |
|  | all | 5,268 | 1,139 | 0.216 | 0.76 | 21.1 |
| L: uniform in the band (lower bound) | CONF | 4,332 | 770 | 0.178 | 1.01 | 25.3 |
|  | DEV | 936 | 138 | 0.147 | 1.00 | 30.1 |
|  | all | 5,268 | 908 | 0.172 | 1.01 | 26.0 |

**N (PREREG §9 rule): placement M, all years, q = 0.216 → 21.1 cycles; rounded up and at least 16: N = 22.**

Assumptions behind the cycle counts (PREREG §9): reject rate 0.5, 5 placebos, design effect 1.2, between-cycle variance 0.005, one-sided α = 0.025, power 0.80, W = 10, 3 levels a day.

Caveats: stand-in round levels, not real open interest; realized, not implied, volatility; US holidays not modelled in the expiry dates; XAUUSD spot, not GC futures.
