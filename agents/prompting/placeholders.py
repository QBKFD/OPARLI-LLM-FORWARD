"""Placeholder names, where each may be used, and the snapshot slot it reads
(docs/PROMPT_GUIDE.md §3).

A placeholder is `{{name}}`, or `{{bars_table:<tf>}}`, with no spaces inside
the braces. Round-1 files (visual, technical, sentiment, gex) may use only
ROUND1 names, and discussion.md only DISCUSSION names. That keeps round 1
blind by construction: no round-1 placeholder renders another agent's vote.
"""

from __future__ import annotations

import re
from typing import Callable

from agents.schemas import TIMEFRAMES

# name -> the snapshot slot it renders (None: always available)
ROUND1: dict[str, str | None] = {
    "as_of": None,
    "alert_json": "alert",
    "levels_json": "levels",
    "trigger_levels_json": "levels",
    "features_json": "features",
    "metrics_table": "metrics",
    "events_json": "events",
    "charts": "charts",
    "snapshot_json": None,
}
BARS_TABLE = "bars_table"                      # {{bars_table:<tf>}}, tf in TIMEFRAMES
DISCUSSION: tuple[str, ...] = ("agent", "round", "max_rounds", "own_previous_vote_json", "other_votes_json",
                               "change_rule")

TOKEN = re.compile(r"\{\{([a-z0-9_]+(?::[a-z0-9]+)?)\}\}")


class MalformedPlaceholder(ValueError):
    pass


def find_placeholders(text: str) -> list[str]:
    """Every placeholder in text, in order (with repeats). Any other `{{` or
    `}}` (spaces inside, an unclosed brace, an empty name) raises
    MalformedPlaceholder, so a typo can't pass as plain text."""
    names = TOKEN.findall(text)
    rest = TOKEN.sub("", text)
    if "{{" in rest or "}}" in rest:
        i = rest.find("{{") if "{{" in rest else rest.find("}}")
        raise MalformedPlaceholder(f"malformed placeholder near {rest[max(0, i - 10):i + 20]!r}; "
                                   "use {{name}} with no spaces")
    return names


def is_round1(name: str) -> bool:
    if name in ROUND1:
        return True
    tf = bars_tf(name)
    return tf is not None and tf in TIMEFRAMES


def is_discussion(name: str) -> bool:
    return name in DISCUSSION


def bars_tf(name: str) -> str | None:
    """The timeframe of a {{bars_table:<tf>}} placeholder, else None."""
    head, sep, tf = name.partition(":")
    return tf if head == BARS_TABLE and sep else None


def slot_of(name: str) -> str | None:
    """The snapshot slot a round-1 placeholder reads (None: always there)."""
    return None if bars_tf(name) is not None else ROUND1.get(name)


def allowed_for(kind: str) -> Callable[[str], bool]:
    if kind == "round1":
        return is_round1
    if kind == "discussion":
        return is_discussion
    raise ValueError(f"unknown prompt kind {kind!r}")


__all__ = ["BARS_TABLE", "DISCUSSION", "MalformedPlaceholder", "ROUND1", "TOKEN", "allowed_for", "bars_tf",
           "find_placeholders", "is_discussion", "is_round1", "slot_of"]
