"""Experiments and their pre-registration.

A live run reads data from after the research seal (2026-10-01), and its
decisions are scored later. So a live run is allowed only inside a
pre-registered experiment: agents/experiments/<experiment_id>/PREREG.md must
exist (and not be empty) before the run. The experiment_id is stored on every
snapshot and decision of the run.

This checks the file on disk only. It doesn't check that the PREREG was
committed to git before the run.
"""

from __future__ import annotations

import re
from pathlib import Path

EXPERIMENTS_DIR = Path(__file__).resolve().parent / "experiments"
# a folder name: letters, digits, '.', '_', '-'; no path separators, no leading dot
EXPERIMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class ExperimentError(RuntimeError):
    pass


def prereg_path(experiment_id: str) -> Path:
    if not isinstance(experiment_id, str) or not EXPERIMENT_ID.fullmatch(experiment_id):
        raise ExperimentError(f"bad experiment_id {experiment_id!r}")
    return EXPERIMENTS_DIR / experiment_id / "PREREG.md"


def require_prereg(experiment_id: str) -> Path:
    p = prereg_path(experiment_id)
    if not p.is_file():
        raise ExperimentError(f"experiment {experiment_id!r} has no PREREG file at {p}")
    if p.stat().st_size == 0:
        raise ExperimentError(f"experiment {experiment_id!r}: {p} is empty")
    return p
