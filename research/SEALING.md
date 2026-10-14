# Data sealing

## History

- Until 2026-09-29, all data from **2024-01-01** onward was sealed (every study
  under `research/` asserted timestamps < 2024-01-01).
- **2024-01-01 to 2026-01-30** is used for the levels confirmation test,
  `research/levels_confirm/` (pre-registration commit: `305d1d0`),
  by decision of the owner. After that test it is spent: seen data.

## Current seal

**NEW SEALED PERIOD: all data from 2026-10-01 onward, reserved for the final
strategy test. Nothing may read it until then.**

Enforced by `research/sealing.py` (`assert_before_global_seal`), which every
loader calls from now on, and tested in `research/test_sealing.py`.
