"""backend.levels is pure: it imports no IO, env, clock or network module,
and reads no clock."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "backend" / "levels"
ALLOWED = {"__future__", "typing", "collections", "pandas", "backend.levels"}


def _imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ""


def test_levels_imports_only_pandas_and_itself():
    files = sorted(PKG.rglob("*.py"))
    assert files
    bad = [(f.name, m) for f in files for m in _imports(f)
           if not any(m == a or m.startswith(a + ".") for a in ALLOWED)]
    assert bad == []


def test_levels_reads_no_clock_env_or_files():
    for f in PKG.rglob("*.py"):
        src = f.read_text()
        for word in ("Timestamp.now", "datetime.now", "time.time", "environ", "open(", "getenv"):
            assert word not in src, (f.name, word)
