"""Shared fixtures for the agents tests: the synthetic replay store, and a
watchdog for slow interpreter exits.

Layout notes:
- The agents package is backend/agents/, imported as `backend.agents`. The
  old agent code (also a package named `agents`) lives in
  legacy/backend_v0/agents/; legacy/ is never collected (norecursedirs in
  pyproject.toml) and never on sys.path.
  test_agents_package.py::test_agents_is_the_repo_root_package checks which
  package is loaded on every run.
- tests/ and every directory in it are packages (__init__.py), so pytest's
  default import mode puts the repo root on sys.path, not the test
  directories: test modules are `tests.agents.test_x`, helpers are imported
  as `tests.agents.agents_testkit`, and no test directory can shadow
  `backend.*`. (Before the move this directory was tests/agents_tests, so a
  tests/agents/ directory on sys.path couldn't shadow the root `agents`.)"""

import faulthandler
import sys
from pathlib import Path

import pytest

from tests.agents.agents_testkit import make_replay_db


# Slow-exit watchdog. Once, a run printed its summary and then took over 3 min
# to exit (agents/REPORT.md). pytest-timeout covers tests, not interpreter
# exit. So if the process is still alive EXIT_DUMP_S after pytest is done, dump
# every thread's stack to stderr to show what's blocking. A normal exit never
# reaches the timer.
EXIT_DUMP_S = 30


def pytest_unconfigure(config):
    faulthandler.dump_traceback_later(EXIT_DUMP_S, exit=False, file=sys.__stderr__)


OWNER_CONFIG = Path(__file__).resolve().with_name("owner_config_tests.toml")


@pytest.fixture(autouse=True)
def owner_config_env(monkeypatch):
    """Every agents test sees a test agents.toml (Meta rule quorum_v0) unless
    it sets or removes $AGENTS_CONFIG itself. Code has no default rule."""
    monkeypatch.setenv("AGENTS_CONFIG", str(OWNER_CONFIG))


@pytest.fixture(scope="session")
def replay_db(tmp_path_factory):
    return make_replay_db(tmp_path_factory.mktemp("replay"))
