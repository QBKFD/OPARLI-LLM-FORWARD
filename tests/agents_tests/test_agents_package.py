"""The right `agents` package is imported, and it stays inside its boundaries."""

import ast
import subprocess
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


# The official Claude SDK is allowed in exactly one module, which imports it
# lazily (agents v2, job 3). Every other LLM client stays banned everywhere.
SDK_MODULE = PKG / "llm" / "anthropic_client.py"


def test_no_backend_legacy_or_llm_imports():
    banned = ("backend", "legacy", "services", "config.", "anthropic", "openai", "langchain_openai",
              "langchain_anthropic", "google.generativeai", "ib_insync", "redis", "psycopg2")
    for f in PKG.rglob("*.py"):
        for mod in _imports(f):
            if f == SDK_MODULE and (mod == "anthropic" or mod.startswith("anthropic.")):
                continue
            assert not mod.startswith(banned), (f, mod)


def test_the_sdk_module_imports_it_lazily_only():
    """In anthropic_client.py the SDK import is inside functions, never at module level."""
    tree = ast.parse(SDK_MODULE.read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any(isinstance(n, ast.Import) and any(a.name.startswith("anthropic") for a in n.names)
                   or isinstance(n, ast.ImportFrom) and (n.module or "").startswith("anthropic") for n in top)


def test_datafeed_only_through_query_and_constants():
    # vol_block: the snapshot features (agents/features.py, agents v2 job 5)
    allowed = {"datafeed.store.query", "datafeed.ingest.xauusd_bars", "datafeed.features.vol_block"}
    for f in PKG.rglob("*.py"):
        for mod in _imports(f):
            if mod.startswith("datafeed"):
                assert mod in allowed, (f, mod)


def test_no_llm_client_loaded():
    """Importing the graph and the whole LLM layer loads no LLM SDK. Checked in a
    fresh interpreter: other tests in this process may import the SDK's types."""
    code = ("import sys, agents.graph, agents.llm, agents.llm.anthropic_client; "
            "bad = [m for m in ('anthropic', 'openai', 'google.generativeai') if m in sys.modules]; "
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], check=True, cwd=ROOT, timeout=60)
