"""Prompt files: parse and load agents/prompts/<agent>.md (docs/PROMPT_GUIDE.md §2).

The owner writes the files; this code only reads them. A file that can't be
used gives a PromptProblem, never a fallback text. The caller turns it into an
abstain with `problem.reason` (`prompt_invalid: <file>: <problem>`).

Format:
    ---
    agent: visual             required, = the file name
    version: 2026-10-20.1     required, free text; "TEMPLATE" = not filled in yet
    description: ...          optional, never sent to the model
    ---
    ## system                 round-1 files only, first, no placeholders
    ...
    ## user                   required
    ...

- The two `---` lines and the `## system` / `## user` markers must be exactly
  those strings on their own line.
- `<!-- ... -->` comments are removed before anything else (between the
  front-matter and the first section, and inside sections), so notes in a
  prompt are never sent to the model. Placeholders inside a comment don't
  count.
- Leading and trailing blank lines of a section are trimmed.
- The SHA-256 is over the file's raw bytes, so every change, comments included,
  changes it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents.prompting.placeholders import MalformedPlaceholder, allowed_for, find_placeholders

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
ROUND1_AGENTS = ("visual", "technical", "sentiment", "gex")
DISCUSSION = "discussion"
PROMPT_NAMES = ROUND1_AGENTS + (DISCUSSION,)
TEMPLATE_VERSION = "TEMPLATE"
FRONT_KEYS_REQUIRED = ("agent", "version")
FRONT_KEYS = FRONT_KEYS_REQUIRED + ("description",)
Kind = Literal["round1", "discussion"]
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_FRONT_LINE = re.compile(r"([a-z_]+):(.*)")


class PromptInvalid(ValueError):
    """The file can't be used; the message says why."""


@dataclass(frozen=True)
class PromptFile:
    name: str                      # file stem: visual, technical, sentiment, gex, discussion
    kind: Kind
    version: str
    description: str | None
    system: str | None             # None for discussion.md
    user: str
    placeholders: tuple[str, ...]  # in order of first use, in ## user
    sha256: str                    # of the raw file bytes
    path: str


@dataclass(frozen=True)
class PromptProblem:
    file: str
    message: str
    sha256: str | None = None      # set when the file exists, so the epoch still sees its bytes
    path: str = ""                 # where it was looked for (shown by the check command, not in the epoch)

    @property
    def reason(self) -> str:
        return f"prompt_invalid: {self.file}: {self.message}"


def kind_of(name: str) -> Kind:
    if name == DISCUSSION:
        return "discussion"
    if name in ROUND1_AGENTS:
        return "round1"
    raise ValueError(f"unknown prompt {name!r}; expected one of {list(PROMPT_NAMES)}")


def _front_matter(lines: list[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0] != "---":
        raise PromptInvalid("must start with a '---' front-matter line")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise PromptInvalid("front-matter has no closing '---' line") from None
    front: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        m = _FRONT_LINE.fullmatch(line)
        if not m:
            raise PromptInvalid(f"bad front-matter line {line!r} (expected 'key: value')")
        key, value = m.group(1), m.group(2).strip()
        if key not in FRONT_KEYS:
            raise PromptInvalid(f"unknown front-matter key {key!r}; allowed: {list(FRONT_KEYS)}")
        if key in front:
            raise PromptInvalid(f"front-matter key {key!r} appears twice")
        if not value:
            raise PromptInvalid(f"front-matter key {key!r} is empty")
        front[key] = value
    for key in FRONT_KEYS_REQUIRED:
        if key not in front:
            raise PromptInvalid(f"front-matter key {key!r} is missing")
    return front, end + 1


def _sections(lines: list[str], kind: Kind) -> dict[str, str]:
    markers = [(i, line[3:]) for i, line in enumerate(lines) if line in ("## system", "## user")]
    names = [n for _, n in markers]
    want = ["system", "user"] if kind == "round1" else ["user"]
    if names != want:
        found = ", ".join(f"## {n}" for n in names) or "none"
        raise PromptInvalid(f"sections must be {', '.join('## ' + n for n in want)}, in that order; found {found}")
    head = "\n".join(lines[:markers[0][0]])
    if "<!--" in _COMMENT.sub("", head):
        raise PromptInvalid("a '<!--' comment before the first section is not closed")
    if _COMMENT.sub("", head).strip():
        raise PromptInvalid("text before the first section (only comments and blank lines may go there)")
    out = {}
    for k, (i, name) in enumerate(markers):
        end = markers[k + 1][0] if k + 1 < len(markers) else len(lines)
        text = "\n".join(lines[i + 1:end])
        if "<!--" in _COMMENT.sub("", text):
            raise PromptInvalid(f"## {name}: a '<!--' comment is not closed")
        text = _COMMENT.sub("", text).strip("\n")
        if not text.strip():
            raise PromptInvalid(f"## {name} is empty")
        out[name] = text
    return out


def parse_prompt(raw: bytes, name: str) -> PromptFile:
    """Parse one prompt file's bytes. Raises PromptInvalid with the reason."""
    kind = kind_of(name)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise PromptInvalid("not valid UTF-8") from None
    if text.startswith("﻿"):
        raise PromptInvalid("starts with a byte-order mark; save it as plain UTF-8")
    lines = text.splitlines()
    front, body_start = _front_matter(lines)
    if front["agent"] != name:
        raise PromptInvalid(f"front-matter agent {front['agent']!r} doesn't match the file name {name!r}")
    if front["version"] == TEMPLATE_VERSION:
        raise PromptInvalid(f"the unfilled template (version: {TEMPLATE_VERSION}); write the prompt and set a version")
    sec = _sections(lines[body_start:], kind)
    try:
        in_system = find_placeholders(sec["system"]) if "system" in sec else []
        used = find_placeholders(sec["user"])
    except MalformedPlaceholder as e:
        raise PromptInvalid(str(e)) from None
    if in_system:
        raise PromptInvalid("## system contains a placeholder; placeholders go in ## user only")
    allowed = allowed_for(kind)
    for p in used:
        if not allowed(p):
            other = "discussion" if kind == "round1" else "round-1"
            raise PromptInvalid(f"placeholder {{{{{p}}}}} is unknown here (or a {other} placeholder)")
    return PromptFile(name=name, kind=kind, version=front["version"], description=front.get("description"),
                      system=sec.get("system"), user=sec["user"], placeholders=tuple(dict.fromkeys(used)),
                      sha256=hashlib.sha256(raw).hexdigest(), path="")


def load_prompt(name: str, prompts_dir: str | Path | None = None) -> PromptFile | PromptProblem:
    """Read and parse agents/prompts/<name>.md (or prompts_dir/<name>.md).
    Never raises for a bad or missing file: returns the PromptProblem."""
    kind_of(name)                                  # an unknown name is a programming error
    path = Path(prompts_dir or PROMPTS_DIR) / f"{name}.md"
    fname = f"{name}.md"
    # Reasons never include the path: they enter the epoch, which must be the
    # same for the same files in any checkout. `path` says where it looked.
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return PromptProblem(fname, "not found", path=str(path))
    except OSError as e:
        return PromptProblem(fname, f"unreadable ({type(e).__name__})", path=str(path))
    sha = hashlib.sha256(raw).hexdigest()
    try:
        pf = parse_prompt(raw, name)
    except PromptInvalid as e:
        return PromptProblem(fname, str(e), sha, str(path))
    return PromptFile(**{**pf.__dict__, "path": str(path)})


def load_all(prompts_dir: str | Path | None = None) -> dict[str, PromptFile | PromptProblem]:
    """Every prompt the graph knows, by name, loaded or with its problem."""
    return {name: load_prompt(name, prompts_dir) for name in PROMPT_NAMES}


__all__ = ["DISCUSSION", "PROMPTS_DIR", "PROMPT_NAMES", "PromptFile", "PromptInvalid", "PromptProblem",
           "ROUND1_AGENTS", "TEMPLATE_VERSION", "kind_of", "load_all", "load_prompt", "parse_prompt"]
