"""Research may not read a data file the manifest marks "unused by any study",
unless that study's committed PREREG names it.

    from data_guard import assert_may_read
    assert_may_read(path, study="gex_levels")        # raises DataGuardError

Guarded files: every entry of backend/datafeed/store/manifest.json with
"status": "unused by any study" (today the 2026-02 .. 2026-09 IBKR backfill),
by file name, including variants of it (e.g. "<name>.rejected.parquet").
Any other file passes; the date seal stays in sealing.py. research/test_data_guard.py
also checks statically that no research code mentions a guarded file without
calling assert_may_read.

Who says a PREREG names a file:

1. **The experiment registry, when it exists.** SEALING.md is being turned into
   an experiment registry. The interface it plugs into: a module in research/,
   named in REGISTRY_MODULES (`experiment_registry` or `registry`), that defines

       prereg_data_files(study: str) -> frozenset[str] | None

   It returns the manifest keys (e.g.
   "database/ohlcv_data/XAUUSD_ibkr_1min_20260201_20260930.parquet") that
   the study's committed PREREG declares. It returns None for a study the
   registry doesn't know. If the module exists but lacks the function, every
   guarded read fails: a broken plug-in never falls back silently.
2. **Until then, the fallback.** research/<study>/PREREG.md must be tracked
   by git, have no uncommitted change, and contain the file's manifest key on
   one of its lines. If git can't answer, the read is refused.
"""

from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research"
MANIFEST = ROOT / "backend" / "datafeed" / "store" / "manifest.json"
UNUSED = "unused by any study"
REGISTRY_MODULES = ("experiment_registry", "registry")


class DataGuardError(PermissionError):
    pass


def guarded() -> dict[str, str]:
    """file name -> manifest key, for every entry no study may use by default."""
    files = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    return {Path(k).name: k for k, e in files.items() if e.get("status") == UNUSED}


def guarded_key(path) -> str | None:
    name = Path(path).name
    for gname, key in guarded().items():
        stem = gname[: -len(Path(gname).suffix)] if Path(gname).suffix else gname
        if name == gname or name.startswith(stem + "."):
            return key
    return None


def registry():
    """The registry module (with prereg_data_files), or None when none exists."""
    for name in REGISTRY_MODULES:
        if not (RESEARCH / f"{name}.py").exists() and not (RESEARCH / name / "__init__.py").exists():
            continue
        import sys
        if str(RESEARCH) not in sys.path:
            sys.path.insert(0, str(RESEARCH))
        mod = importlib.import_module(name)
        if not callable(getattr(mod, "prereg_data_files", None)):
            raise DataGuardError(f"research/{name} exists but defines no prereg_data_files(study); "
                                 f"see research/data_guard.py for the interface")
        return mod
    return None


def _git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def prereg_names(study: str, key: str) -> tuple[bool, str]:
    """(allowed, how it was decided)."""
    reg = registry()
    if reg is not None:
        files = reg.prereg_data_files(study)
        if files is None:
            return False, f"the experiment registry doesn't know study {study!r}"
        return key in files, f"the experiment registry ({reg.__name__})"
    prereg = RESEARCH / study / "PREREG.md"
    rel = prereg.relative_to(ROOT).as_posix()
    if not prereg.is_file():
        return False, f"no registry yet, and {rel} doesn't exist"
    try:
        tracked = _git("ls-files", "--error-unmatch", rel).returncode == 0
        clean = _git("diff", "--quiet", "HEAD", "--", rel).returncode == 0
    except OSError:
        return False, "no registry yet, and git can't be run to check the PREREG"
    if not (tracked and clean):
        return False, f"no registry yet, and {rel} is not committed as it is"
    named = any(key in line for line in prereg.read_text(encoding="utf-8").splitlines())
    return named, f"no registry yet; {rel} (committed)"


def assert_may_read(path, study: str) -> None:
    key = guarded_key(path)
    if key is None:
        return
    ok, how = prereg_names(study, key)
    if not ok:
        raise DataGuardError(f"{Path(path).name} is {UNUSED!r} ({key}): study {study!r} may read it only once "
                             f"its committed PREREG names it. Decided by: {how}.")
