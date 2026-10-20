"""Render a round-1 prompt from a snapshot (docs/PROMPT_GUIDE.md §3.1).

Pure: the same prompt file, snapshot and config always give the same
RenderedPrompt. What each placeholder renders is fixed here and is the same
for every agent. The owner decides which placeholders a prompt uses.

- A placeholder whose snapshot slot is marked unavailable renders
  `[unavailable: <slot>: <reason>]`, never an empty list that would look like
  data. The rendered prompt lists those slots in `unavailable_used`.
- `{{charts}}` splits the user text: the charts go in as one ImagesPart at that
  position (the LLM client turns it into image blocks).
- `{{bars_table:<tf>}}` needs render.bars_rows.<tf> in agents.toml. Without
  it the agent abstains (RenderProblem); there is no default row count.
- experiment_id is never rendered (it is dropped from {{snapshot_json}}).

Discussion rendering (discussion.md) comes with the discussion subgraph.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.hashing import canonical_json
from agents.owner_config import ConfigProblem, OwnerConfig
from agents.prompting.files import PromptFile
from agents.prompting.placeholders import TOKEN, bars_tf, slot_of
from agents.schemas import ChartRef, Snapshot


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagesPart:
    charts: tuple[ChartRef, ...]


@dataclass(frozen=True)
class RenderedPrompt:
    agent: str
    system: str
    user: tuple[TextPart | ImagesPart, ...]
    unavailable_used: tuple[str, ...]          # slots rendered as unavailable

    def user_text(self) -> str:
        """The user content with images shown as [images: tf, ...] (for logs and Studio)."""
        return "".join(p.text if isinstance(p, TextPart) else
                       f"[images: {', '.join(c.tf for c in p.charts)}]" for p in self.user)


@dataclass(frozen=True)
class RenderProblem:
    reason: str


def unavailable_text(slot: str, why: str) -> str:
    return f"[unavailable: {slot}: {why}]"


def metrics_table(snap: Snapshot) -> str:
    """One line per metric: name | value | unit | available | reason."""
    lines = ["name | value | unit | available | reason"]
    for m in snap.metrics:
        value = "" if m.value is None else repr(m.value)
        lines.append(f"{m.name} | {value} | {m.unit} | {str(m.available).lower()} | {m.reason or ''}")
    return "\n".join(lines)


def bars_table(snap: Snapshot, tf: str, rows: int) -> str:
    """The last `rows` complete bars of tf, oldest first: open time, O, H, L, C."""
    series = [b for b in snap.bars.get(tf, ()) if b.complete][-rows:]
    lines = [f"{tf} bars (complete only, oldest first): ts | open | high | low | close"]
    lines += [f"{b.ts.isoformat()} | {b.open!r} | {b.high!r} | {b.low!r} | {b.close!r}" for b in series]
    return "\n".join(lines)


def _value(name: str, snap: Snapshot, cfg: OwnerConfig) -> str | ImagesPart | ConfigProblem:
    slot = slot_of(name)
    if slot is not None and slot in snap.unavailable:
        return unavailable_text(slot, snap.unavailable[slot])
    tf = bars_tf(name)
    if tf is not None:
        if tf not in snap.bars:
            return unavailable_text(f"bars:{tf}", "not in the snapshot")
        rows = cfg.bars_rows(tf)
        return rows if isinstance(rows, ConfigProblem) else bars_table(snap, tf, rows)
    if name == "as_of":
        return snap.as_of.isoformat()
    if name == "alert_json":
        return canonical_json(snap.alert)
    if name == "levels_json":
        return canonical_json(list(snap.levels))
    if name == "trigger_levels_json":
        return canonical_json(list(snap.trigger_levels))
    if name == "features_json":
        return canonical_json(snap.features)
    if name == "metrics_table":
        return metrics_table(snap)
    if name == "events_json":
        return canonical_json(list(snap.events))
    if name == "charts":
        return ImagesPart(snap.charts)
    if name == "snapshot_json":
        return canonical_json(snap.model_dump(mode="python", exclude={"experiment_id"}))
    raise KeyError(name)               # unreachable: parse_prompt admits only known names


def render_round1(prompt: PromptFile, snap: Snapshot, cfg: OwnerConfig) -> RenderedPrompt | RenderProblem:
    """Fill a round-1 prompt from the snapshot. A problem (a missing
    render.bars_rows entry) is returned, not raised."""
    if prompt.kind != "round1" or prompt.system is None:
        return RenderProblem(f"prompt_invalid: {prompt.name}.md is not a round-1 prompt")
    parts: list[TextPart | ImagesPart] = []
    unavailable: list[str] = []
    text, pos = "", 0
    for m in TOKEN.finditer(prompt.user):
        text += prompt.user[pos:m.start()]
        pos = m.end()
        name = m.group(1)
        v = _value(name, snap, cfg)
        if isinstance(v, ConfigProblem):
            return RenderProblem(v.reason)
        slot = slot_of(name)
        if slot is not None and slot in snap.unavailable:
            unavailable.append(slot)
        if isinstance(v, ImagesPart):
            if text:
                parts.append(TextPart(text))
                text = ""
            if v.charts:
                parts.append(v)
        else:
            text += v
    text += prompt.user[pos:]
    if text:
        parts.append(TextPart(text))
    return RenderedPrompt(agent=prompt.name, system=prompt.system, user=tuple(parts),
                          unavailable_used=tuple(dict.fromkeys(unavailable)))


__all__ = ["ImagesPart", "RenderProblem", "RenderedPrompt", "TextPart", "bars_table", "metrics_table",
           "render_round1", "unavailable_text"]
