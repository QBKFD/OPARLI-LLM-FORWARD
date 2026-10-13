"""tests/agents runs under tests/, so pytest also loads tests/conftest.py,
which puts backend/ first on sys.path for the old tests. backend/agents/ is a
package too, so `import agents` could load the old code. Take backend/ off the
path, as tests/datafeed does. test_agents_package.py checks which `agents`
package was loaded.

There is no __init__.py in this directory on purpose: with one, pytest would
import this directory itself as a package named `agents`. Test module names
must therefore not clash with tests/datafeed (no test_package.py, test_store.py,
test_end_to_end.py here)."""

import sys
from pathlib import Path

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
sys.path[:] = [p for p in sys.path if p != _BACKEND]
