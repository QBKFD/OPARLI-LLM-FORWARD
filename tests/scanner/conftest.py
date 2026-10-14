"""The scanner is a script in backend/, not a package. Load it by file path as
module `deterministic_scanner`, so tests can `import deterministic_scanner`
without putting backend/ on sys.path: a bare `pytest` collects tests/datafeed
in the same process, and its test_backend_not_on_path guards that datafeed
never depends on backend/. The scanner imports only pandas at module level
(services.tws_connector and ib_insync are imported lazily by run_live)."""

import importlib.util
import sys
from pathlib import Path

_SCANNER = Path(__file__).resolve().parents[2] / "backend" / "deterministic_scanner.py"

if "deterministic_scanner" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("deterministic_scanner", _SCANNER)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules["deterministic_scanner"] = _mod
    _spec.loader.exec_module(_mod)
