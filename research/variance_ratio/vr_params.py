"""Every value research/variance_ratio uses, with its plan id (PLAN.md section of
PREREG.md). EXPLORATORY, descriptive: there is no hypothesis and no pass/fail."""

import pandas as pd

STUDY = "variance_ratio"
MARKETS = ("XAUUSD", "XAGUSD", "EURUSD", "USDJPY")                     # V1
DEV_START = pd.Timestamp("2016-01-01", tz="UTC")                       # V2
DEV_END_EXCL = pd.Timestamp("2024-01-01", tz="UTC")                    # V2
KS_HOURS = (2, 4, 8, 24)                                               # V3
BASES = {"1h": 60, "5min": 5}                                          # V3: base return, minutes
TAIL_MIN = {"1h": 15, "5min": 5}       # V4: a bin's close is its last bar, which must fall in the bin's last TAIL_MIN
GAP_MIN = 15                           # V4: no gap longer than this between 1-min bars inside a return
SHORT_DAY_ROWS = 1000                  # V4: a trade date (17:00 New York roll) with fewer rows is short
ROLL_NY_HOUR = 17                      # V4
GOLD_SEAM = pd.Timestamp("2026-02-01", tz="UTC")                       # V4: outside dev; counted anyway
SESSIONS_UTC = (("Asia", 22, 7), ("London", 7, 12), ("Overlap", 12, 16), ("NewYork", 16, 22))   # V5: [start, end) UTC hours
SIGMA_MODEL = "parquet2021_2023"                                       # V6: gold's pinned HAR fit, on every market
SIGMA_MODEL_SHA256 = "dc0eb3c5fd31f1ee8f6e7a534b16930fbec453833aec9872b606afe963910dde"
SIGMA_H = 240                                                          # V6
NEWS_EVENTS = ("CPI m/m", "CPI y/y", "Core CPI m/m", "FOMC Press Conference", "FOMC Statement",
               "Federal Funds Rate", "Non-Farm Employment Change")    # V7: usd_tier1 (DATA_NOTES_events.md), High impact
CALENDAR_COMMIT = "12f65c6"                                            # V7: data/macro-calendar (PR #60), anchor check PASS
CALENDAR_SHA256 = "dfde970cdcd56b5782e9bc2c4f4cf2c9f6b49e9f05ee380d5b89ef8b0bdad82d"   # V7: PR #60's manifest entry
BH_Q = 0.05                                                            # V8
