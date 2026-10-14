"""tests/agents_tests runs under tests/, so pytest also loads tests/conftest.py,
which puts backend/ first on sys.path for the old tests. backend/agents/ is a
package too, so `import agents` could load the old code. Take backend/ off the
path, as tests/datafeed does. test_agents_package.py checks which `agents`
package was loaded.

Naming. This directory is NOT called tests/agents: pytest's default import mode
puts tests/ on sys.path (because of tests/conftest.py), and a tests/agents/
directory then shadows the real package as a namespace package `agents`.
Helper modules have unique names (agents_testkit, agents_fakes) for the same
reason: every test directory shares one module namespace."""

import faulthandler
import sys
from pathlib import Path

import pytest

_BACKEND = str(Path(__file__).resolve().parents[2] / "backend")
sys.path[:] = [p for p in sys.path if p != _BACKEND]

from agents_testkit import make_replay_db  # noqa: E402


# Slow-exit watchdog. Once, a run printed its summary and then took over 3 min
# to exit (agents/REPORT.md). pytest-timeout covers tests, not interpreter
# exit. So if the process is still alive EXIT_DUMP_S after pytest is done, dump
# every thread's stack to stderr to show what's blocking. A normal exit never
# reaches the timer.
EXIT_DUMP_S = 30


def pytest_unconfigure(config):
    faulthandler.dump_traceback_later(EXIT_DUMP_S, exit=False, file=sys.__stderr__)


@pytest.fixture(scope="session")
def replay_db(tmp_path_factory):
    return make_replay_db(tmp_path_factory.mktemp("replay"))
