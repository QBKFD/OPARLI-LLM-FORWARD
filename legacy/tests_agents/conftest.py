"""
Pytest configuration for the OPARLI test suite.

Puts `backend/` on the import path (the backend modules import each other with
absolute names like `services.foo`, so `backend/` must be a root), and sets a
sentinel DATABASE_URL.

The sentinel is never connected to: `get_database()` only stores the URL, and
`create_pool()` is never called, so any code that tries to open a cursor raises
and is handled by that call site's error path. That is deliberate — it means
these tests fail loudly if a code path that is supposed to be DB-free starts
depending on the database, instead of silently connecting to a developer's
local Postgres.
"""

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

os.environ.setdefault("DATABASE_URL", "postgresql://sentinel:sentinel@127.0.0.1:1/sentinel")
