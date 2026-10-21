"""requirements.lock pins every dependency (with hashes) and agrees with pyproject.toml."""

import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+)")


def _lock():
    pins, hashed, current = {}, {}, None
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        m = PIN.match(line)
        if m:
            current = canonicalize_name(m.group(1))
            pins[current] = m.group(2)
            hashed[current] = 0
        elif line.strip().startswith("--hash=sha256:") and current:
            hashed[current] += 1
        elif line.strip() and not line.lstrip().startswith("#") and not line.startswith("    "):
            raise AssertionError(f"unexpected line in requirements.lock: {line!r}")
    return pins, hashed


def _pyproject_requirements():
    proj = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    reqs = list(proj["dependencies"])
    for extra in proj["optional-dependencies"].values():
        reqs += extra
    return [Requirement(r) for r in reqs]


def test_every_lock_line_is_pinned_and_hashed():
    pins, hashed = _lock()
    assert len(pins) > 40
    assert all(n > 0 for n in hashed.values()), [p for p, n in hashed.items() if n == 0]


def test_every_direct_dependency_is_locked_at_its_pin():
    pins, _ = _lock()
    for r in _pyproject_requirements():
        (spec,) = r.specifier
        assert spec.operator == "==", f"{r} is not pinned in pyproject.toml"
        assert pins.get(canonicalize_name(r.name)) == spec.version, r


def test_build_backend_is_locked():
    pins, _ = _lock()
    assert {"setuptools", "wheel"} <= set(pins)


def test_cutoff_caps_hold():
    pins, _ = _lock()
    for line in (ROOT / "requirements.cutoff.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            r = Requirement(line)
            assert Version(pins[canonicalize_name(r.name)]) in r.specifier, r
