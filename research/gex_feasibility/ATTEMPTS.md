# Probe attempts

Every probe run, in order (PREREG.md, "One snapshot decides"). `analyze.py`
copies these lines into REPORT.md's "Attempts" section. Add a line for every
run, used or not.

- Attempt 1: 2026-10-22 10:43 UTC, exit 1, not used. "only 2 OG expiries at least 2 days away: ['20261027', '20261124']". The connection was fine, and no market data was read (the probe stopped during expiry discovery). Led to PREREG Amendment 1.
