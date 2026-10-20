"""Evidence check, in code: every claim must point at something inside the snapshot.

evidence_ref forms:
  bars:<tf>:<start>..<end>   tf in the snapshot; start and end are bar open
                             times present in that series (ISO-8601, tz-aware);
                             start <= end
  level:<id>                 an id in snapshot.levels
  feature:<name>             a key in snapshot.features
  event:<id>                 an id in snapshot.events
  metric:<name>              a metric in snapshot.metrics with available = true
  chart:<tf>                 a chart attached to the snapshot (snapshot.charts)

A claim whose ref doesn't resolve gets valid=False with the reason. It stays in
the turn (and in the log), but the judge never sees it.
"""

from __future__ import annotations

from datetime import datetime

from agents.schemas import Claim, Snapshot, Turn


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


def check_turn(turn: Turn, snap: Snapshot) -> Turn:
    """The same turn with every claim's valid / invalid_reason set by code."""
    claims = []
    for c in turn.claims:
        reason = check_ref(c.evidence_ref, snap)
        claims.append(Claim(text=c.text, evidence_ref=c.evidence_ref, valid=reason is None,
                            invalid_reason=reason))
    return Turn(**{**turn.model_dump(exclude={"claims"}), "claims": tuple(claims)})
