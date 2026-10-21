"""Import rules: risk/ imports only the stdlib, itself and backend.datafeed.features;
nothing in datafeed/ imports risk/. A static (AST) check of every module, so
an import inside a function counts too; importlib calls are not seen."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def imports(source: str, package: tuple[str, ...]):
    """(module, names) for every import; relative imports resolved against package.
    names is None for a plain `import x`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, None
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[:len(package) - node.level + 1]
                mod = ".".join(base + ((node.module,) if node.module else ()))
            else:
                mod = node.module
            yield mod, [a.name for a in node.names]


def allowed_in_risk(mod: str, names) -> bool:
    top = mod.split(".")[0]
    return (top in sys.stdlib_module_names or mod == "backend.risk" or mod.startswith("backend.risk.")
            or mod == "backend.datafeed.features" or mod.startswith("backend.datafeed.features.")
            or (mod == "backend.datafeed" and names is not None and set(names) == {"features"}))


def imports_risk(mod: str, names) -> bool:
    return mod == "backend.risk" or mod.startswith("backend.risk.")


def violations(folder: str, bad):
    out = []
    for path in sorted((ROOT / folder).rglob("*.py")):
        package = path.relative_to(ROOT).parent.parts
        for mod, names in imports(path.read_text(), package):
            if bad(mod, names):
                out.append(f"{path.relative_to(ROOT)}: {mod} {names or ''}")
    return out


def test_risk_imports_only_stdlib_and_datafeed_features():
    assert list((ROOT / "backend" / "risk").rglob("*.py"))
    assert violations("backend/risk", lambda m, n: not allowed_in_risk(m, n)) == []


def test_datafeed_does_not_import_risk():
    assert list((ROOT / "backend" / "datafeed").rglob("*.py"))
    assert violations("backend/datafeed", imports_risk) == []


def test_the_checks_have_teeth():
    src = ("import math\nimport numpy\nfrom backend.datafeed.store import query\n"
           "from backend.datafeed import features\nfrom backend.datafeed.features.vol_block import Sigma\n"
           "from . import sizing\nfrom ._check import positive\n")
    bad = [m for m, n in imports(src, ("backend", "risk")) if not allowed_in_risk(m, n)]
    assert bad == ["numpy", "backend.datafeed.store"]
    src = "def f():\n    from backend.risk.sizing import position_size\nimport backend.risk\nimport backend.riskless\n"
    assert sorted(m for m, n in imports(src, ("backend", "datafeed")) if imports_risk(m, n)) == \
        ["backend.risk", "backend.risk.sizing"]
