# Probe attempts

Every probe run, in order (PREREG.md, "One snapshot decides"). `analyze.py`
copies these lines into REPORT.md's "Attempts" section. Add a line for every
run, used or not.

- Attempt 1: 2026-10-22 10:43 UTC, exit 1, not used. "only 2 OG expiries at least 2 days away: ['20261027', '20261124']". The connection was fine, and no market data was read (the probe stopped during expiry discovery). Led to PREREG Amendment 1.
- Attempt 2: 2026-10-22 11:40 UTC, exit 4, not used. IBKR error 10197 (a competing live session): the chain was found (3 expiries, 4,326 contracts), and no market data was read.
- Attempt 3: 2026-10-22 11:43 UTC, exit 0, **used** (the first successful run). 4,324 options: 4,324 live, 0 frozen, 0 without data; 8 IBKR messages (all data-farm status notices); finished 11:54 UTC. File `gex_probe_20261022T1143Z.json`.
