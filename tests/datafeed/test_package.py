"""Step 1: the package installs and imports from any working directory."""

import subprocess
import sys


def test_bars_before_imports_from_any_folder(tmp_path):
    # Run in a fresh interpreter from an unrelated folder, so neither the repo
    # root nor tests/ is on sys.path; only the installed package can satisfy it.
    out = subprocess.run(
        [sys.executable, "-I", "-c",
         "from datafeed.store.query import bars_before; import datafeed; print(datafeed.__file__)"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("datafeed/__init__.py")


def test_backend_not_on_path():
    assert not any(p.rstrip("/").endswith("/backend") for p in sys.path)
