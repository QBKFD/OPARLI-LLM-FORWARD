"""Triggers are pluggable. Anything that yields TriggerEvents can start runs:
a clock, a scanner alert, a manual call. None is chosen here."""

from __future__ import annotations

from typing import Iterable, Protocol

from backend.agents.schemas import TriggerEvent


class Trigger(Protocol):
    def events(self) -> Iterable[TriggerEvent]: ...


def drive(trigger: Trigger, graph) -> list[dict]:
    """One graph run per event, in order. Returns the final states."""
    from backend.agents.graph import run_once
    return [run_once(graph, ev) for ev in trigger.events()]


__all__ = ["Trigger", "TriggerEvent", "drive"]
