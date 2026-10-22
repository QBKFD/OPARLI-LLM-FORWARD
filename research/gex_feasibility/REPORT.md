# GEX feasibility: report

## Verdict

**INCONCLUSIVE: a gate failed, no decision (rerun per PREREG)**

- **R = 8.19%** = |GEX| $4.72bn per 1 % move / D $57.60bn average daily GC futures dollar volume (20 trade dates).
- Rule (PREREG): R < 1 % drop; R > 5 % build the test; in between, later at low priority.
- Gates: FAILED, see below.

## Snapshot

- Snapshot: 2026-10-22T11:43:43.820Z to 2026-10-22T11:54:35.827Z (UTC); CME trade date 2026-10-22.
- Open-interest settlement date: **2026-10-21** (inferred: IBKR sends open interest without a date; inferred as the last trade date before the snapshot's trade date (CME publishes it after that date's settlement)).
- Expiries: 3 used, 3 targeted.
- Expiry → underlying (from its chain / from its contracts' details): 20261027 GCZ6 / GCZ6; 20261124 GCZ6 / GCZ6; 20261228 GCG7 / GCG7.
- GC futures asked for their option chains: GCV6, GCX6, GCZ6, GCF7, GCG7.
- Option contracts: 4324 in expiries 20261027, 20261124, 20261228; values live 4324 (100.0%), frozen 0 (0.0%), none 0 (0.0%).
- Underlyings: GCZ6 live, GCG7 live.
- GC futures open interest (check against CME's figure for 2026-10-21): GCZ6 313,137, GCG7 45,976.
- Probe file: `gex_probe_20261022T1143Z.json`, SHA-256 `ca54f914b2236b80c6a711f66d2e9a647349acce9a3668f3b618c346925342f5` (not committed: exchange data, public repo).
- Expiry times: ibkr: 4324. Most market data lines at once: 50.
- IBKR messages by code: {2104: 3, 2106: 2, 2119: 2, 2158: 1}.

## Attempts

- Attempt 1: 2026-10-22 10:43 UTC, exit 1, not used. "only 2 OG expiries at least 2 days away: ['20261027', '20261124']". The connection was fine, and no market data was read (the probe stopped during expiry discovery). Led to PREREG Amendment 1.
- Attempt 2: 2026-10-22 11:40 UTC, exit 4, not used. IBKR error 10197 (a competing live session): the chain was found (3 expiries, 4,326 contracts), and no market data was read.
- Attempt 3: 2026-10-22 11:43 UTC, exit 0, **used** (the first successful run). 4,324 options: 4,324 live, 0 frozen, 0 without data; 8 IBKR messages (all data-farm status notices); finished 11:54 UTC. File `gex_probe_20261022T1143Z.json`.

## Gates

| Gate | Result | Detail |
|---|---|---|
| G1 | pass | snapshot 2026-10-22T11:43:43.820Z, trade date 2026-10-22 |
| G2 | pass | 20 complete GC trade dates |
| G3 | pass | 4229 of 4324 listed contracts have open interest (97.8%) |
| G4 | pass | 16 contracts with a filled-in IV hold 0.4% of open interest |
| G5 | **FAIL** | median |Γ_ours/Γ_IBKR − 1| = 6.16% over 3797 contracts |
| G6 | pass | every selected expiry has an underlying price |

## Per expiry

| Expiry | Underlying | F (source) | Days | OI calls | OI puts | OI total | \|GEX\| per 1 % | Strikes listed (range) | Strikes quoted (range) | Contracts quoted |
|---|---|---|---|---|---|---|---|---|---|---|
| 20261027 | GCZ6 | 4,079.95 (mid) | 5.2 | 96,776 | 61,509 | 158,285 | $1.72bn | 625 (1,000.00–20,000.00) | 625 (1,000.00–20,000.00) | 1210 of 1250 |
| 20261124 | GCZ6 | 4,079.95 (mid) | 33.3 | 341,164 | 100,082 | 441,246 | $2.43bn | 907 (500.00–24,000.00) | 907 (500.00–24,000.00) | 1582 of 1814 |
| 20261228 | GCG7 | 4,113.80 (mid) | 67.3 | 49,919 | 22,792 | 72,711 | $565.30m | 630 (2,625.00–20,000.00) | 630 (2,625.00–20,000.00) | 1036 of 1260 |

- A strike is quoted when its call or its put has a positive bid or ask (PREREG); a contract is quoted when it has one itself.
- Front month (20261027): 23.55% of open interest, 36.56% of |GEX|; back months the rest.

## Top 10 strikes by open interest (calls + puts, all selected expiries)

| Strike | Open interest | Share |
|---|---|---|
| 20,000.00 | 39,753 | 5.9% |
| 15,000.00 | 36,703 | 5.5% |
| 5,000.00 | 22,103 | 3.3% |
| 5,500.00 | 21,273 | 3.2% |
| 6,000.00 | 18,822 | 2.8% |
| 4,500.00 | 18,428 | 2.7% |
| 4,900.00 | 15,507 | 2.3% |
| 8,000.00 | 14,609 | 2.2% |
| 4,800.00 | 12,091 | 1.8% |
| 4,300.00 | 11,844 | 1.8% |

- Together: 31.41% of 672,242 contracts of open interest.

## Sensitivities (cannot change the verdict)

- R, next 2 expiries only: 7.21%
- R, D = front GC month only (GCZ6): 8.38%
- R, IBKR's model gamma instead of ours: 8.20%
- Other GC option trading classes listed (not in R), with their expiry counts: G1M 1, G1R 1, G1T 2, G1W 1, G2M 1, G2R 1, G2T 1, G2W 1, G3M 1, G3T 1, G4M 1, G4R 1, G4W 1, G5R 1, OG1 1, OG2 1, OG4 1, OG5 1.

## D, by trade date

| Trade date | GC dollar volume |
|---|---|
| 2026-09-24 | $63.10bn |
| 2026-09-25 | $54.12bn |
| 2026-09-28 | $89.02bn |
| 2026-09-29 | $59.25bn |
| 2026-09-30 | $62.02bn |
| 2026-10-01 | $52.18bn |
| 2026-10-02 | $66.29bn |
| 2026-10-05 | $44.20bn |
| 2026-10-06 | $48.00bn |
| 2026-10-07 | $61.61bn |
| 2026-10-08 | $57.79bn |
| 2026-10-09 | $52.49bn |
| 2026-10-12 | $40.82bn |
| 2026-10-13 | $48.42bn |
| 2026-10-14 | $73.72bn |
| 2026-10-15 | $62.09bn |
| 2026-10-16 | $54.90bn |
| 2026-10-19 | $45.61bn |
| 2026-10-20 | $59.48bn |
| 2026-10-21 | $56.92bn |

## Post hoc: where G5's differences come from (NOT pre-registered; does not change the verdict)

Relative difference |Γ_ours/Γ_IBKR − 1|, Black-76 with IBKR's own IV and underlying price.

| Group | Contracts | Median difference |
|---|---|---|
| expiry 20261027 | 975 | 35.64% |
| expiry 20261124 | 1581 | 6.51% |
| expiry 20261228 | 1241 | 3.06% |
| 0–1 SD from the money | 587 | 0.56% |
| 1–2 SD from the money | 955 | 2.58% |
| 2–3 SD from the money | 1331 | 7.20% |
| 3–∞ SD from the money | 924 | 132.38% |

- Weighted by each contract's |GEX|, the median difference is 1.24%; contracts 2 SD or more from the money hold 6.53% of |GEX|.
- R with IBKR's model gamma instead of ours: 8.20%.

## Known limits

- One day; listed OG monthlies only (no weeklies, GLD or OTC options).
- |GEX| is unsigned, an upper bound on net dealer hedging; D is futures volume only.
- Post-seal data, read for magnitudes only (PREREG, "Seal"): no prices around strikes, no returns, no XAUUSD bars.
