"""risk installs, imports from any folder, and loads no third-party module."""

import subprocess
import sys


def test_risk_imports_from_any_folder_stdlib_only(tmp_path):
    # fresh interpreter in an unrelated folder: only the installed package can satisfy it
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys, backend.risk as risk, backend.risk.bracket, backend.risk.sizing; print(risk.__file__);"
         "print(sorted(m for m in ('numpy', 'pandas', 'backend.datafeed') if m in sys.modules))"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    path, loaded = out.stdout.strip().splitlines()
    assert path.endswith("risk/__init__.py")
    assert loaded == "[]"
