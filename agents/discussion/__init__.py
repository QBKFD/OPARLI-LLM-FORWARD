"""The discussion (docs/AGENTS_ARCHITECTURE.md §5.2–5.3): N simultaneous rounds
in which each LLM agent sees the others' votes and may change its own only by
citing new snapshot evidence. Replaces the bull/bear debate."""

from agents.discussion.node import discussants_of, make_discussion
from agents.discussion.rule import NO_NEW_REF, ONLY_VOTE_REFS, apply_change_rule, new_snapshot_refs
from agents.discussion.subgraph import (Discussant, build_discussion_graph, content_only, no_discussion,
                                        run_discussion, safe_discuss, speakers_of)

__all__ = ["Discussant", "NO_NEW_REF", "ONLY_VOTE_REFS", "apply_change_rule", "build_discussion_graph",
           "content_only", "discussants_of", "make_discussion", "new_snapshot_refs", "no_discussion",
           "run_discussion", "safe_discuss", "speakers_of"]
