"""The scanner is a script in backend/, not a package: put backend/ on sys.path
so the tests can `import deterministic_scanner`. Its live paths import
services.tws_connector and ib_insync lazily, so importing it needs only pandas."""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)
