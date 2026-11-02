"""touch_rate.py's calendar and arithmetic (no data needed)."""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import touch_rate as tr  # noqa: E402


@pytest.mark.parametrize("ym, want", [
    ((2026, 10), date(2026, 10, 27)),    # IBKR: OGX6 expires 2026-10-27
    ((2026, 12), date(2026, 12, 28)),    # IBKR: 2026-12-28
    ((2027, 1), date(2027, 1, 26)),      # IBKR: 2027-01-26
    ((2026, 9), date(2026, 9, 24)),      # 4th-last business day is Friday 25th -> Thursday 24th (the rule)
])
def test_og_expiry(ym, want):
    assert tr.og_expiry(*ym) == want


def test_og_expiry_ignores_holidays_a_known_approximation():
    # IBKR: 2026-11-24 (Thanksgiving, the 26th, isn't a business day). Weekends only gives the 25th.
    assert tr.og_expiry(2026, 11) == date(2026, 11, 25)


def test_trade_date_rolls_at_18_new_york():
    idx = pd.DatetimeIndex(["2026-10-21 21:59", "2026-10-21 22:00", "2026-10-22 05:00"], tz="UTC")
    assert list(tr.trade_date(idx)) == [np.datetime64("2026-10-21"), np.datetime64("2026-10-22"),
                                        np.datetime64("2026-10-22")]


@pytest.mark.parametrize("p0, step", [(650, 10), (1300, 25), (2000, 25), (4100, 50)])
def test_round_step_scales_with_price(p0, step):
    assert tr.step_for(p0) == step


def test_cycles_needed_matches_the_prereg_table():
    assert tr.cycles_needed(0.3) == pytest.approx(15.7, abs=0.05)
    assert tr.cycles_needed(0.2) == pytest.approx(22.7, abs=0.05)
