"""The right `agents` package is imported, and it stays inside its boundaries."""

import ast
import sys
from pathlib import Path

import agents

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "agents"


def test_agents_is_the_repo_root_package():
    # backend/agents/ (old code) and a test directory named agents/ could both shadow it
    assert Path(agents.__file__).resolve() == PKG / "__init__.py"
    assert agents.__version__ == "0.1.0"


def _imports(path):
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def test_no_backend_legacy_or_llm_imports():
    banned = ("backend", "legacy", "services", "config.", "anthropic", "openai", "langchain_openai",
              "langchain_anthropic", "google.generativeai", "ib_insync", "redis", "psycopg2")
    for f in PKG.rglob("*.py"):
        for mod in _imports(f):
            assert not mod.startswith(banned), (f, mod)


def test_datafeed_only_through_query_and_constants():
    allowed = {"datafeed.store.query", "datafeed.ingest.xauusd_bars"}
    for f in PKG.rglob("*.py"):
        for mod in _imports(f):
            if mod.startswith("datafeed"):
                assert mod in allowed, (f, mod)


def test_no_llm_client_loaded():
    import agents.graph  # noqa: F401
    for mod in ("anthropic", "openai", "google.generativeai"):
        assert mod not in sys.modules
