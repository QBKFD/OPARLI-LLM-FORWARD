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
