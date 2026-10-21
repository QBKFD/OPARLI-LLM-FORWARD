"""No module of the scanner or the level engine grows past 1,000 lines."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 1000


def test_scanner_modules_stay_under_the_limit():
    files = sorted((ROOT / "backend" / "scanner").rglob("*.py")) + sorted((ROOT / "backend" / "levels").rglob("*.py"))
    assert files
    sizes = {f.relative_to(ROOT).as_posix(): len(f.read_text().splitlines()) for f in files}
    assert {f: n for f, n in sizes.items() if n > LIMIT} == {}
