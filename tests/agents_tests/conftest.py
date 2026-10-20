"""Shared fixtures for the agents tests: the synthetic replay store, and a
watchdog for slow interpreter exits.

Layout notes:
- The old agent code, formerly backend/agents/ (also a package named
  `agents`), now lives in legacy/backend_v0/agents/. legacy/ is never
  collected (norecursedirs in pyproject.toml) and never on sys.path, so
  `import agents` can only load the repo-root package.
  test_agents_package.py::test_agents_is_the_repo_root_package checks this on
  every run.
- This directory is tests/agents_tests, not tests/agents. While
  tests/conftest.py existed (now legacy/tests_agents/conftest.py), pytest put
  tests/ on sys.path, and a tests/agents/ directory shadowed the real package
  as a namespace package. The name stays, so the shadow can't return if a
  tests/conftest.py is added again.
- pytest's default import mode puts each test directory (no __init__.py) on
  sys.path, so all test directories share one module namespace. Helper modules
  therefore have unique names (agents_testkit, agents_fakes), and test module
  names must not clash with tests/datafeed (no test_package.py, test_store.py
  or test_end_to_end.py here)."""

import faulthandler
import sys
from pathlib import Path

import pytest

from agents_testkit import make_replay_db


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
