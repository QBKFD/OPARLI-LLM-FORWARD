"""Every value of research/pullback_structure, each with its PREREG.md id.
All are APPROVED (owner, 2026-10-27; PREREG merged in 42675bb). Nothing else
in the study may hold a number of its own."""

import pandas as pd

STUDY = "pullback_structure"

# P1-P3: 4h candles, New York anchor, slot starts (NY hour) and the end of the last slot
CANDLE_EDGES_NY = (18, 22, 2, 6, 10, 14)       # P2
CANDLE_END_NY = 17                              # P2: the last candle ends at the daily break
MIN_SHARE = 0.5                                 # P3

K = 3                                           # P4
DEPTH_EDGES = (0.0, 0.5, 1.0, 1.5)              # P11: [0, 0.5), [0.5, 1.0), [1.0, 1.5), >= 1.5
X_SIGMA = 1.0                                   # P15
W = 5                                           # P15
W_ALT = (4, 5, 6)                               # P37: the audit compares W - 1 and W + 1
HORIZON = 30                                    # P17

SIGMA_MODEL = "parquet2021_2023"                # P9
SIGMA_MODEL_SHA256 = "dc0eb3c5fd31f1ee8f6e7a534b16930fbec453833aec9872b606afe963910dde"   # P9
SIGMA_H = 240                                   # P9

DATA_KEY = "database/ohlcv_data/XAUUSD.txt"     # P21
WARMUP_START = pd.Timestamp("2015-07-01", tz="UTC")   # P21
EVENTS_FROM = pd.Timestamp("2016-01-01", tz="UTC")    # P21, P22
DEV_END_EXCL = pd.Timestamp("2024-01-01", tz="UTC")   # P22: dev is 2016-01-01 .. 2023-12-31

BOOT_B = 10_000                                 # P28
BOOT_SEED = 20261027                            # P28
BH_Q = 0.05                                     # P29
MIN_EVENTS = 50                                 # P30
FIX_EXCESS = 0.05                               # P31, P32: 5 percentage points

RW_REPS = 200                                   # P33: 200 dev-sized samples
RW_MIN_FACTOR = 20                              # §5: >= 20 x the dev event count
RW_TOL = 0.01                                   # P33: |excess| <= 1 pp
RW_COVERAGE = (0.93, 0.97)                      # P33

GALLERY_SEED = 20261037                         # P37
GALLERY_PER_LABEL = 20                          # P37
GALLERY_AUDIT_EACH = 10                         # P37
GALLERY_BEFORE = 20                             # P37: candles before the swing low
GALLERY_AFTER = 30                              # P37: candles after the outcome
