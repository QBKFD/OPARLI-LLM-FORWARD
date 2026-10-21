"""tools/status/requirements-status.lock is a verbatim subset of requirements.lock
(same versions, same hashes) and pins textual at the `status` extra's version."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY = re.compile(r"^([a-z0-9_.-]+)==(\S+) \\\n((?:    --hash=\S+(?: \\)?\n)+)", re.M)


def _entries(path):
    return {m.group(1): m.group(0) for m in ENTRY.finditer(path.read_text())}


def test_status_lock_is_a_verbatim_subset_of_the_main_lock():
    sub, full = _entries(ROOT / "tools/status/requirements-status.lock"), _entries(ROOT / "requirements.lock")
    assert sub and set(sub) <= set(full)
    assert {k: v for k, v in full.items() if k in sub} == sub


def test_status_lock_pins_the_extras_textual():
    extra = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["optional-dependencies"]["status"]
    assert extra == ["textual==8.2.8"]
    assert "textual==8.2.8 \\" in (ROOT / "tools/status/requirements-status.lock").read_text()
