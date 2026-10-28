"""The values research/breakout_fade adds, each with its PREREG.md id (all APPROVED
by the owner, 2026-10-28). The definitions themselves (1h candles, k, swings, X,
W, the horizon) are pullback_structure's, imported unchanged (B3)."""

import pandas as pd

STUDY = "breakout_fade"
INSTRUMENT = "XAGUSD"                                                  # B1
# B1's file key lives in fade_data.py, next to its data_guard call (research/test_data_guard.py)
DEV_START = pd.Timestamp("2016-01-01", tz="UTC")                      # B2
DEV_END_EXCL = pd.Timestamp("2024-01-01", tz="UTC")                   # B2
SIGMA_MODEL = "parquet2021_2023"                                       # B4: gold's pinned fit on silver's bars
SIGMA_MODEL_SHA256 = "dc0eb3c5fd31f1ee8f6e7a534b16930fbec453833aec9872b606afe963910dde"   # B4
SIGMA_H = 240                                                          # B3 (P9)
STOP_SIGMA = 1.0                                                       # B7
BOOT_B, BOOT_SEED = 10_000, 20261029                                   # B14
BH_Q = 0.05                                                            # B12
FAIL_LOWER = 0.02                                                      # B13: FAIL if the CI's lower bound is above -2 pp
MIN_EVENTS = 50                                                        # B13, B18
SHORT_DAY_ROWS = 1000                                                  # B16
RW_REPS, RW_TOL, RW_COVERAGE = 200, 0.01, (0.93, 0.97)                # B15
