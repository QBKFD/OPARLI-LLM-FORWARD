"""tools/ isn't an installed package: put the repo root on sys.path (at the
end, so it never shadows an installed package) for `import tools.status`."""

import sys
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.append(ROOT)
