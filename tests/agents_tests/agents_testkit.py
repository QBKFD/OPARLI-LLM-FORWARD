"""Synthetic replay data for the agents tests, recorded through the datafeed's
own replay path.

The generator and the recording live in tools/langgraph_studio/studio.py (the
LangGraph Studio entry point replays the same data). It's a script, not part
of the agents package, so it's loaded by file path as module
`langgraph_studio`, the way tests/scanner loads the scanner. This module
keeps the names the tests import and skips when pandas isn't installed."""

import importlib.util
import sys
from pathlib import Path

import pytest

STUDIO_PATH = Path(__file__).resolve().parents[2] / "tools" / "langgraph_studio" / "studio.py"


def load_studio():
    """tools/langgraph_studio/studio.py as module `langgraph_studio` (loaded once)."""
    mod = sys.modules.get("langgraph_studio")
    if mod is None:
        spec = importlib.util.spec_from_file_location("langgraph_studio", STUDIO_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["langgraph_studio"] = mod
        spec.loader.exec_module(mod)
    return mod


_studio = load_studio()
DATA_START, DATA_END = _studio.SYNTH_START, _studio.SYNTH_END
synthetic_rows = _studio.synthetic_rows

__all__ = ["DATA_END", "DATA_START", "load_studio", "make_replay_db", "synthetic_rows"]


def make_replay_db(directory, rows=None):
    """rows -> parquet -> datafeed ReplaySource -> InMemoryBus -> Recorder -> SQLite.
    The same path the datafeed uses, so the bars are kind 'replay'."""
    pytest.importorskip("pandas")
    return _studio.make_replay_db(directory, rows)
