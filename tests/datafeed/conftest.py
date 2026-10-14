"""tests/datafeed runs under tests/, so pytest also loads tests/conftest.py,
which puts backend/ on sys.path for the old agent tests. The datafeed package
must not depend on backend/, so take it back off the path for these tests.
DATABASE_URL (the old tests' sentinel) is left alone; datafeed never reads it."""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
sys.path[:] = [p for p in sys.path if p != _BACKEND]
