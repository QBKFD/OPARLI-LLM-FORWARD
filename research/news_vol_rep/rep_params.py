"""research/news_vol_rep (Study A replication): every fixed value of the PREREG.

R2: the design is Study A's (research/news_vol, PREREG merged as 843b447, N1
approved 2026-11-02). Its constants are imported, not copied, so they cannot
drift; only the markets and the study name are new.
"""
from __future__ import annotations

import sys
from pathlib import Path

_NV = Path(__file__).resolve().parents[1] / "news_vol"
if str(_NV) not in sys.path:
    sys.path.insert(0, str(_NV))

import nv_params as A  # noqa: E402

STUDY = "news_vol_rep"
MARKETS = ("XAGUSD", "EURUSD")                                      # R1, in this order everywhere
STUDY_A_PREREG_COMMIT = "843b447"

# R1, R2: Study A's period, folds, horizons, events and statistics
DEV_START, DEV_END_EXCL, FOLD_EDGES, EMBARGO = A.DEV_START, A.DEV_END_EXCL, A.FOLD_EDGES, A.EMBARGO
HORIZONS, PRIMARY_H, COVERAGE = A.HORIZONS, A.PRIMARY_H, A.COVERAGE
DATA_EVENTS, FOMC_EVENTS, TIER1 = A.DATA_EVENTS, A.FOMC_EVENTS, A.TIER1
CALENDAR_SHA256, CALENDAR_COMMIT = A.CALENDAR_SHA256, A.CALENDAR_COMMIT
NW_LAG, BOOT_B, SEED, BH_Q = A.NW_LAG, A.BOOT_B, A.SEED, A.BH_Q

# R3: EURUSD's daily pause in New York time, [17:00, 17:15), and its Sunday open
# (17:15 NY), as its bar times show (DECISIONS R-N1). The metals' break is the
# sigma_compare calendar's 17:00-18:00.
FX_PAUSE_NY = (17 * 60, 17 * 60 + 15)

# R5: counted only (the limitation), never flagged
EUR_COUNT_CURRENCY, EUR_COUNT_IMPACT = "EUR", "High"

# Machinery check: Study A's generator and pass rule (its PREREG section 7)
SYN_SEED, SYN_PRICE0, SYN_SIGMA_MIN = A.SYN_SEED, A.SYN_PRICE0, A.SYN_SIGMA_MIN
SYN_AR, SYN_AR_SD = A.SYN_AR, A.SYN_AR_SD
SYN_EFFECT_MULT, SYN_EFFECT_MIN, SYN_RECOVER = A.SYN_EFFECT_MULT, A.SYN_EFFECT_MIN, A.SYN_RECOVER
