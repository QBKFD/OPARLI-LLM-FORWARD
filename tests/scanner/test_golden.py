"""The committed golden test: the scanner's alerts, live-started replays and
--date replay rows are what main produced before the restructure.

The pinned digests were computed on main @ 1b7d5ff (backend/deterministic_scanner.py)
and on backend.scanner with the same helpers (tests/scanner/golden_digest.py);
both gave these values. The synthetic part runs everywhere (CI); the real-data
part needs the untracked parquet and skips without it.

A change here is a behaviour change: never re-pin a digest to make this pass.
The full golden replay (Telegram, journal, both live bar sources) is described in
docs/history/RESTRUCTURE_PLAN.md, section 2.
"""
from pathlib import Path

import pandas as pd
import pytest

import backend.scanner as ds
from backend.scanner.sources import replay
from tests.levels.levels_data import bars
from tests.scanner.golden_digest import DAYS, day_digest, synthetic_digest

SYNTHETIC = "4b2fa2be6fca3aec7742356a41886f2a3bd09695ce9de1e6723517c2864987b4"
PINNED = {
    "2023-03-12": "531e2371c8a582e3ca72591e4f9f69174a8edb7b80c484ddcb21aa5d8501851c",
    "2023-03-13": "75194da1b95aeca61004991e86b21d1f97e9c333c46bcf6ba5e5d49eefb49d4c",
    "2023-06-15": "f0e261b3e463bf0c868481fc66645c0a0d809dfd343a2e8defae62080608651a",
    "2023-11-05": "531e2371c8a582e3ca72591e4f9f69174a8edb7b80c484ddcb21aa5d8501851c",
    "2023-11-06": "780a2a36a014eb0ddac27e86a5db3330356446ae2081d49478d5f6d4f6839021",
    "2023-11-23": "592e8dd9c0260bb4d045ed5a1e4dc263e3b4155a6038a9576255f7a4621f4089",
    "2023-12-25": "04a02426d51d3b7c2a34180a036e6473bb4a4042d5b2d95b69f9f07cf5958335",
    "2024-03-08": "047ef09b6bb94d1d05b32d5abcabf44c3c2ab0cf66428e51dd221874e583e644",
    "2024-03-10": "531e2371c8a582e3ca72591e4f9f69174a8edb7b80c484ddcb21aa5d8501851c",
    "2024-03-11": "431273f923e5d5b7c297abd7089215dc0aca269206a3871b4fad7f587d92e7ee",
    "2024-03-29": "531e2371c8a582e3ca72591e4f9f69174a8edb7b80c484ddcb21aa5d8501851c",
    "2024-03-31": "2171eb59c41b4189e8112de8189447ed30975251f05aea8605b7975cdc1b2df5",
    "2024-07-04": "0e12832c39883b953e1a68c62c9cd45a2f511028da81573576fa2066ff43ff25",
    "2024-11-03": "891c71ab59e7149d95ac99069de009ef0ec3218cde65eed4286acc9ae7c68fff",
    "2024-11-04": "69f3ea6983b4c1f973c4e2afe65f64ab6cd3d23a02b7a9dd6b0c7db18814446b",
    "2024-12-24": "dc81df464a6a8ff83a01d9a9420ddf73bafdf866df7fc4ce9caa5e97d68e5504",
    "2024-12-25": "271859c431b51f3189597443b095e5900a2c0956a7e84a4a2faa6e3f9aa823d2",
    "2025-01-01": "e76b9e4e03eb9cac0532d73b9d043e2849d7b7b53131759f5fdb6738e21f1861",
    "2025-03-09": "281ceba88790052a31dc817838a979494ed225db61061c706c1f0e79ad6026c5",
    "2025-03-10": "7e806f8f48d9b6cb7897df71b70182aee0e3c9f0857c444636266e720a8d4e0b",
    "2025-06-15": "ff0d373c829b775707ffa22b0ff89904ac1957b750768ccc566be775cbab6b02",
    "2025-06-16": "c26e6c8215fff4f31f24933c1967a7f684ab41faedb68a4c0ae6414a2b4c2be6",
    "2025-07-03": "cd23f7170887d8a99940aa70120b755103ed6cd5e51ea0273e4bcf77e02ec8b6",
    "2025-07-04": "9ae30057a0166a3c5c2582fc3a0d03a3362af0b7bea92964cfa70e181615a693",
    "2025-10-14": "027410eb1e87319363caccadfe98a271ab8f540d80d7eb36ab563d02aab5b6fd",
    "2025-10-26": "391e44532ba53de2271766710604fd59a5d8d79b7465ec411a41fbd9004e8830",
    "2025-11-02": "531e2371c8a582e3ca72591e4f9f69174a8edb7b80c484ddcb21aa5d8501851c",
    "2025-11-03": "60588bcddb6b11a9f691e9cc7b67052d9ab755fb2b23ebcae2b13d95405ddffd",
}


def test_synthetic_golden():
    assert synthetic_digest(ds, bars(days=21)) == SYNTHETIC


def test_every_golden_day_is_pinned():
    assert list(PINNED) == DAYS


@pytest.fixture(scope="module")
def parquet_bars():
    if not Path(ds.PARQUET).exists():
        pytest.skip("historical parquet not present (untracked)")
    return ds.load_data()


@pytest.mark.parametrize("day", DAYS)
def test_real_day_golden(day, parquet_bars, monkeypatch):
    monkeypatch.setattr(replay, "load_data", lambda: parquet_bars)     # read the parquet once, not per day
    assert day_digest(ds, parquet_bars, day) == PINNED[day]
