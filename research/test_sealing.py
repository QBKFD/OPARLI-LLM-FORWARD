"""Tests for the global seal.  python -m pytest research/test_sealing.py -q"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sealing import GLOBAL_SEAL, assert_before_global_seal  # noqa: E402


def test_global_seal_fires_at_and_after_2026_10_01():
    for bad in ["2026-10-01 00:00", "2027-03-15 12:00"]:
        with pytest.raises(AssertionError, match="SEALED"):
            assert_before_global_seal(pd.DatetimeIndex([bad], tz="UTC"))
    with pytest.raises(AssertionError, match="SEALED"):
        assert_before_global_seal(np.array([GLOBAL_SEAL.value]))
    with pytest.raises(AssertionError, match="SEALED"):
        assert_before_global_seal(pd.DatetimeIndex(["2026-10-01 00:00"]))          # naive = UTC


def test_global_seal_allows_earlier_data():
    assert_before_global_seal(pd.DatetimeIndex(["2026-09-30 23:59", "2024-06-01"], tz="UTC"))
    assert_before_global_seal(np.array([], dtype=np.int64))


# --------------------------------------------------------------------------- per-instrument periods (2026-10-28)

from sealing import INSTRUMENTS, SEAL_REGISTRY, assert_period  # noqa: E402


@pytest.mark.parametrize("sym", INSTRUMENTS)
def test_every_instrument_has_dev_holdout_and_the_seal(sym):
    assert SEAL_REGISTRY[sym]["dev"] == (pd.Timestamp("2016-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
    assert SEAL_REGISTRY[sym]["holdout"] == (pd.Timestamp("2024-01-01", tz="UTC"), GLOBAL_SEAL)
    assert_period(sym, pd.DatetimeIndex(["2016-01-01", "2023-12-31 23:59"], tz="UTC"), "dev")
    assert_period(sym, pd.DatetimeIndex(["2024-01-01", "2026-09-30 23:59"], tz="UTC"), "holdout")
    with pytest.raises(AssertionError, match="outside its dev"):
        assert_period(sym, pd.DatetimeIndex(["2024-01-01"], tz="UTC"), "dev")       # holdout under dev
    with pytest.raises(AssertionError, match="outside its dev"):
        assert_period(sym, pd.DatetimeIndex(["2015-12-31 23:59"], tz="UTC"), "dev")
    with pytest.raises(AssertionError, match="SEALED"):
        assert_period(sym, pd.DatetimeIndex(["2026-10-01"], tz="UTC"), "holdout")


def test_unknown_instrument_or_period_fails():
    with pytest.raises(AssertionError, match="not in the seal registry"):
        assert_period("BTCUSD", pd.DatetimeIndex(["2020-01-01"], tz="UTC"), "dev")
    with pytest.raises(AssertionError, match="period"):
        assert_period("XAGUSD", pd.DatetimeIndex(["2020-01-01"], tz="UTC"), "sealed")
