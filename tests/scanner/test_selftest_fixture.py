"""The scanner selftest on the committed synthetic fixture, so it runs in CI
without the real (IBKR, own-use-only) data.

The scanner itself is unchanged (on hold): the test points the replay
loader's PARQUET at the fixture with monkeypatch. When the hold lifts, the
loader takes the path itself (SCANNER_PARQUET) and CI runs the selftest on
this file (docs/decisions/feat-data-manifest.md, DM8)."""
from pathlib import Path

import pandas as pd

from backend.datafeed.store import manifest
from backend.scanner import selftest
from backend.scanner.sources import replay

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "tests" / "fixtures" / "xauusd_synthetic_selftest.parquet"


def test_selftest_passes_on_the_synthetic_fixture(monkeypatch, capsys):
    assert manifest.verify(SYNTHETIC) == "tests/fixtures/xauusd_synthetic_selftest.parquet"
    monkeypatch.setattr(replay, "PARQUET", SYNTHETIC)
    assert selftest.selftest() is True
    assert "SELFTEST: ALL PASSED" in capsys.readouterr().out


def test_the_fixture_carries_the_real_data_cases(monkeypatch, tmp_path, capsys):
    """Without the 2024 and 2025 windows the live-vs-replay cases fail: the
    fixture, not luck, makes them pass."""
    cut = tmp_path / "cut.parquet"
    df = pd.read_parquet(SYNTHETIC)
    df[df["timestamp"] < pd.Timestamp("2024-01-01", tz="UTC")].to_parquet(cut, index=False)
    monkeypatch.setattr(replay, "PARQUET", cut)
    assert selftest.selftest() is False
    assert "live vs replay" in capsys.readouterr().out
