"""scoring/ import rules (static AST check) and installation."""

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def imports(path: Path):
    package = path.relative_to(ROOT).parent.parts
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - node.level + 1]
                yield ".".join(base + ((node.module,) if node.module else ()))
            else:
                yield node.module


def allowed_in_scoring(mod: str) -> bool:
    top = mod.split(".")[0]
    return (top in sys.stdlib_module_names or top in ("scoring", "risk")
            or any(mod == p or mod.startswith(p + ".") for p in ("datafeed.store", "datafeed.features")))


def test_scoring_imports_only_stdlib_risk_the_store_and_features():
    files = sorted((ROOT / "scoring").rglob("*.py"))
    assert files
    bad = [f"{p.relative_to(ROOT)}: {m}" for p in files for m in imports(p) if not allowed_in_scoring(m)]
    assert bad == []


def test_datafeed_and_risk_do_not_import_scoring():
    files = sorted((ROOT / "datafeed").rglob("*.py")) + sorted((ROOT / "risk").rglob("*.py"))
    bad = [f"{p.relative_to(ROOT)}: {m}" for p in files for m in imports(p)
           if m == "scoring" or m.startswith("scoring.")]
    assert bad == []


def test_scoring_installs_and_loads_no_third_party_module(tmp_path):
    # score() is stdlib-only; vol_block (numpy, pandas) is imported inside score_neutral
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys, scoring.outcome as o; print(o.__file__);"
         "print(sorted(m for m in ('numpy', 'pandas') if m in sys.modules))"],
        cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    path, loaded = out.stdout.strip().splitlines()
    assert path.endswith("scoring/outcome.py") and loaded == "[]"
