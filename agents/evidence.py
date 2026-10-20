"""Evidence refs, checked in code: every claim must point at something inside
the snapshot (docs/PROMPT_GUIDE.md §6).

evidence_ref forms:
  bars:<tf>:<start>..<end>   tf in the snapshot; start and end are bar open
                             times present in that series (ISO-8601, tz-aware);
                             start <= end
  level:<id>                 an id in snapshot.levels
  feature:<name>             a key in snapshot.features
  event:<id>                 an id in snapshot.events
  metric:<name>              a metric in snapshot.metrics with available = true
  chart:<tf>                 a chart attached to the snapshot (snapshot.charts)
  vote:<agent>:<round>       discussion only: a vote shown in this discussion
                             round (check_vote_ref)

A ref that doesn't resolve is kept, with valid=False and the reason. It is
never dropped silently, and it never counts as evidence.
"""

from __future__ import annotations

from datetime import datetime

from agents.schemas import Snapshot


def _ts(s: str) -> datetime:
    t = datetime.fromisoformat(s.strip())
    if t.tzinfo is None:
        raise ValueError(f"naive timestamp {s.strip()!r}")
    return t


def check_ref(ref: str, snap: Snapshot) -> str | None:
    """None if the ref resolves inside the snapshot, else the reason."""
    if not isinstance(ref, str) or not ref.strip():
        return "empty evidence_ref"
    kind, sep, rest = ref.strip().partition(":")
    if not sep or not rest:
        return f"evidence_ref {ref!r} is not <kind>:<target>"
    if kind == "bars":
        tf, sep, span = rest.partition(":")
        if tf not in snap.bars:
            return f"timeframe {tf!r} not in snapshot"
        a, sep2, b = span.partition("..")
        if not sep or not sep2:
            return "bars ref must be bars:<tf>:<start>..<end>"
        try:
            start, end = _ts(a), _ts(b)
        except ValueError as e:
            return f"bad timestamp: {e}"
        if start > end:
            return "start is after end"
        have = {bar.ts for bar in snap.bars[tf]}
        for x in (start, end):
            if x not in have:
                return f"no {tf} bar opens at {x.isoformat()} in the snapshot"
        return None
    if kind == "level":
        return None if rest in {lv.id for lv in snap.levels} else f"level {rest!r} not in snapshot"
    if kind == "feature":
        return None if rest in snap.features else f"feature {rest!r} not in snapshot"
    if kind == "event":
        return None if rest in {e.id for e in snap.events} else f"event {rest!r} not in snapshot"
    if kind == "metric":
        m = next((m for m in snap.metrics if m.name == rest), None)
        if m is None:
            return f"metric {rest!r} not in snapshot"
        return None if m.available else f"metric {rest!r} is unavailable: {m.reason}"
    if kind == "chart":
        return None if rest in {c.tf for c in snap.charts} else f"chart {rest!r} not in snapshot"
    return f"unknown evidence_ref kind {kind!r}"



def check_vote_ref(ref: str, shown: set[tuple[str, int]]) -> str | None:
    """None if `vote:<agent>:<round>` names a vote shown in this discussion
    round, else the reason."""
    kind, _, rest = ref.strip().partition(":")
    if kind != "vote":
        return f"{ref!r} is not a vote: ref"
    agent, sep, rnd = rest.rpartition(":")
    if not sep or not agent or not rnd.isdigit():
        return "vote ref must be vote:<agent>:<round>"
    if (agent, int(rnd)) not in shown:
        return f"vote {agent} round {rnd} was not shown in this discussion round"
    return None
