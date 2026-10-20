"""health.json: the schema shared by the writer (watchdog side) and the readers
(TUI, --once). Stdlib only.

    {"schema_version": 1, "generated_ts": "2026-10-17T12:00:00Z",
     "thresholds_sha256": "...",
     "components": [{"name": "gateway", "status": "green", "reason": "...",
                     "ts": "2026-10-17T12:00:00Z", "deployed": true}, ...]}

components is a list in COMPONENTS order, one entry per component, always all
ten. status is green, yellow or red; deployed=false means the component isn't
part of this deployment (shown as OFF, status green).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable, Literal, cast

Status = Literal["green", "yellow", "red"]
STATUSES: tuple[Status, ...] = ("green", "yellow", "red")
COMPONENTS: tuple[str, ...] = ("gateway", "feed", "redis", "recorder", "scanner", "journal",
                               "backups", "disk", "memory", "agents")
SCHEMA_VERSION = 1


class HealthFileError(ValueError):
    """health.json content that doesn't match the schema."""


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: Status
    reason: str
    ts: str                      # ISO-8601 UTC, when it was measured
    deployed: bool = True


@dataclass(frozen=True)
class Health:
    generated_ts: str            # ISO-8601 UTC, when the writer ran
    components: tuple[ComponentHealth, ...]
    thresholds_sha256: str = ""
    schema_version: int = SCHEMA_VERSION
    note: str | None = field(default=None, compare=False)   # reader-side banner, never written

    def by_name(self) -> dict[str, ComponentHealth]:
        return {c.name: c for c in self.components}


def worst(statuses: Iterable[Status]) -> Status:
    """red > yellow > green; green for an empty input."""
    rank = {s: i for i, s in enumerate(STATUSES)}
    return max(statuses, key=lambda s: rank[s], default="green")


def overall(h: Health) -> Status:
    """The worst status over the deployed components."""
    return worst(c.status for c in h.components if c.deployed)


def to_json(h: Health) -> str:
    d = asdict(h)
    d.pop("note")
    return json.dumps(d, sort_keys=True, indent=1)


def _str(d: dict[str, object], key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str):
        raise HealthFileError(f"{key} must be a string, got {v!r}")
    return v


def from_json(text: str) -> Health:
    """Parse and validate. Raises HealthFileError (or json.JSONDecodeError, a
    ValueError) on anything that isn't a complete, valid health.json."""
    d = json.loads(text)
    if not isinstance(d, dict):
        raise HealthFileError("not a JSON object")
    if d.get("schema_version") != SCHEMA_VERSION:
        raise HealthFileError(f"schema_version {d.get('schema_version')!r}, expected {SCHEMA_VERSION}")
    raw = d.get("components")
    if not isinstance(raw, list):
        raise HealthFileError("components must be a list")
    comps = []
    for c in raw:
        if not isinstance(c, dict):
            raise HealthFileError(f"component entry {c!r} is not an object")
        status = _str(c, "status")
        if status not in STATUSES:
            raise HealthFileError(f"{c.get('name')!r}: status {status!r}")
        deployed = c.get("deployed", True)
        if not isinstance(deployed, bool):
            raise HealthFileError(f"{c.get('name')!r}: deployed must be true/false")
        comps.append(ComponentHealth(_str(c, "name"), cast(Status, status), _str(c, "reason"),
                                     _str(c, "ts"), deployed))
    names = [c.name for c in comps]
    if sorted(names) != sorted(COMPONENTS):
        raise HealthFileError(f"components {names}, expected {list(COMPONENTS)}")
    return Health(_str(d, "generated_ts"), tuple(comps), _str(d, "thresholds_sha256"))
