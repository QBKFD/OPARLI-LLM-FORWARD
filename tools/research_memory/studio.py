"""LangGraph Studio entry point for the `research_memory` graph.

Registered in tools/langgraph_studio/langgraph.json next to `agents`. The
index ($RESEARCH_MEMORY_INDEX, else database/research_memory.sqlite) and the
owner's config ($RESEARCH_MEMORY_CONFIG) are read on use; without them a run
abstains with the reason. The real embedder needs the memory venv
(requirements/requirements-memory.lock) and the fetched model.

Input: {"question": "...", "k": 5}.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:      # `langgraph dev` loads this file by path; tools/ is not an installed package
    sys.path.insert(0, str(REPO))

from tools.research_memory.graph import make_default_graph  # noqa: E402


def make_graph():
    index = os.environ.get("RESEARCH_MEMORY_INDEX")
    return make_default_graph(REPO, Path(index) if index else None)
