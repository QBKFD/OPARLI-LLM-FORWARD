"""research/news_vol (Study A): every fixed value of the PREREG, in one place."""
from __future__ import annotations

import pandas as pd

STUDY = "news_vol"
MARKET = "XAUUSD"                                                   # D1

# D1: dev only. Training expands from 2016-01-01; test folds are calendar years 2018..2023 (D5).
DEV_START = pd.Timestamp("2016-01-01", tz="UTC")
DEV_END_EXCL = pd.Timestamp("2024-01-01", tz="UTC")
FOLD_EDGES = pd.date_range("2018-01-01", "2024-01-01", freq="YS", tz="UTC")
EMBARGO = pd.Timedelta(hours=24)                                    # as sigma_compare

HORIZONS = (240, 60)                                                # D3: primary first
PRIMARY_H = 240
COVERAGE = 0.90                                                     # as sigma_compare

# D2: #60's usd_tier1 set; the file keys live in nv_data.py, next to the data_guard calls
# (database/historical_news/DATA_NOTES_events.md), High impact, USD, timed.
DATA_EVENTS = ("CPI m/m", "CPI y/y", "Core CPI m/m", "Non-Farm Employment Change")
FOMC_EVENTS = ("FOMC Statement", "Federal Funds Rate", "FOMC Press Conference")
TIER1 = DATA_EVENTS + FOMC_EVENTS
CALENDAR_SHA256 = "dfde970cdcd56b5782e9bc2c4f4cf2c9f6b49e9f05ee380d5b89ef8b0bdad82d"
CALENDAR_COMMIT = "0988ca3"                                         # merge of PR #60 on main

# D6-D9
NW_LAG = 8                                                          # as sigma_compare, both H
BOOT_B = 10_000
SEED = 20261101
BH_Q = 0.05

# 1c: machinery check on synthetic bars (PREREG section 7)
SYN_SEED = 20261101
SYN_PRICE0 = 1500.0
SYN_SIGMA_MIN = 1.0e-4                    # per-minute sd before profile, level and effect
SYN_AR = 0.98                             # hourly AR(1) of the log-variance level
SYN_AR_SD = 0.20
SYN_EFFECT_MULT = 4.0                     # variance multiplier from the event minute ...
SYN_EFFECT_MIN = 30                       # ... for this many minutes (every grouped event)
SYN_RECOVER = (0.5, 2.0)                  # Q1 estimate / generator's true difference must lie in this range
