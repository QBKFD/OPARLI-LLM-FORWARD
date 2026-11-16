"""Markdown -> chunks: one per heading, split under a size cap.

- A chunk's heading path is every enclosing heading joined by " > "
  (e.g. "breakout_fade: pre-registration > 6. Amendment 1 (2026-10-28)").
  Text before the first heading has the heading "(top of file)".
- Headings inside fenced code blocks (``` or ~~~) are text, not headings.
- A section longer than MAX_CHARS is split on blank lines; a table longer
  than that is split by rows, and every piece repeats the table's header and
  separator rows. A single overlong line is cut at a space. Every piece keeps
  the same heading path (that is what a citation names) and records its part.
- Status: the file's status, unless a section override (corpus.toml) matches
  any heading in the path; subsections inherit it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from tools.research_memory.corpus import Document, SectionRule, SkipRule

MAX_CHARS = 1800        # ~450 tokens: inside bge-small's 512-token window with the header lines
TOP = "(top of file)"
SEP = " > "
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    path: str
    study: str
    doc_type: str
    status: str
    heading_path: str
    heading: str            # the innermost heading (the last element of heading_path)
    part: int               # 1-based
    n_parts: int
    text: str               # the body of this piece (without the heading lines)
    last_commit: str

    def embed_text(self) -> str:
        """What is embedded: path and heading path give the body its context."""
        return f"{self.path}\n{self.heading_path}\n\n{self.text}"


def clean_heading(raw: str) -> str:
    """Heading text as shown and cited: markdown emphasis and code ticks removed."""
    return re.sub(r"\s+", " ", raw.replace("**", "").replace("`", "")).strip()


def sections(text: str) -> list[tuple[list[str], str]]:
    """[(heading path, body)] in file order; empty bodies are dropped."""
    out: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    in_fence = False

    def flush() -> None:
        b = "\n".join(body).strip("\n")
        if b.strip():
            out.append(([h for _, h in stack] or [TOP], b))
        body.clear()

    for line in text.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            body.append(line)
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), clean_heading(m.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            body.append(line)
    flush()
    return out


def strip_sections(text: str, patterns: list[re.Pattern[str]]) -> tuple[str, int]:
    """(text without the matched sections, number removed). A section runs from
    a heading matching any pattern to the next heading of the same or a higher
    level; headings inside code fences are ignored."""
    out: list[str] = []
    skip_level: int | None = None
    removed = 0
    in_fence = False
    for line in text.splitlines(keepends=True):
        if _FENCE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING.match(line.rstrip("\n"))
        if m:
            level = len(m.group(1))
            if skip_level is not None and level <= skip_level:
                skip_level = None
            if skip_level is None and any(p.search(clean_heading(m.group(2))) for p in patterns):
                skip_level, removed = level, removed + 1
        if skip_level is None:
            out.append(line)
    return "".join(out), removed


def apply_skips(doc: Document, skips: tuple[SkipRule, ...]) -> Document:
    """The document without its skipped sections. A rule that matches no heading
    raises ValueError (stale or mistyped)."""
    mine = [r for r in skips if r.path == doc.path]
    if not mine:
        return doc
    text = doc.text
    for rule in mine:
        text, n = strip_sections(text, [rule.heading])
        if n == 0:
            raise ValueError(f"skip_section {rule.heading.pattern!r} matches no heading in {doc.path}")
    return Document(path=doc.path, text=text, status=doc.status, study=doc.study, doc_type=doc.doc_type,
                    last_commit=doc.last_commit)


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _blocks(body: str) -> list[str]:
    """Paragraphs: split on blank lines, but a fenced block stays whole."""
    blocks, cur, in_fence = [], [], False
    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        if not line.strip() and not in_fence:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _split_long_line(line: str, cap: int) -> list[str]:
    out = []
    while len(line) > cap:
        cut = line.rfind(" ", 0, cap)
        cut = cut if cut > cap // 2 else cap
        out.append(line[:cut])
        line = line[cut:].lstrip()
    return [*out, line] if line else out


def _split_block(block: str, cap: int) -> list[str]:
    """A block over the cap: a table by rows (header repeated), else by lines."""
    lines = block.splitlines()
    header: list[str] = []
    if len(lines) >= 2 and _is_table_row(lines[0]) and _TABLE_SEP.match(lines[1]):
        header, lines = lines[:2], lines[2:]
    room = cap - sum(len(h) + 1 for h in header)
    pieces, cur, size = [], [], 0
    for line in lines:
        for seg in _split_long_line(line, max(room, 200)):
            if cur and size + len(seg) + 1 > room:
                pieces.append("\n".join(header + cur))
                cur, size = [], 0
            cur.append(seg)
            size += len(seg) + 1
    if cur:
        pieces.append("\n".join(header + cur))
    return pieces


def split_body(body: str, cap: int = MAX_CHARS) -> list[str]:
    """Pack paragraphs into pieces of at most ~cap characters."""
    pieces, cur = [], ""
    for block in _blocks(body):
        parts = [block] if len(block) <= cap else _split_block(block, cap)
        for part in parts:
            if cur and len(cur) + 2 + len(part) > cap:
                pieces.append(cur)
                cur = ""
            cur = f"{cur}\n\n{part}" if cur else part
    if cur:
        pieces.append(cur)
    return pieces


def section_status(doc: Document, path: list[str], overrides: tuple[SectionRule, ...]) -> str:
    for rule in overrides:
        if rule.path == doc.path and any(rule.heading.search(h) for h in path):
            return rule.status
    return doc.status


def chunk_document(doc: Document, overrides: tuple[SectionRule, ...] = (), cap: int = MAX_CHARS) -> list[Chunk]:
    chunks = []
    for path, body in sections(doc.text):
        hp = SEP.join(path)
        status = section_status(doc, path, overrides)
        pieces = split_body(body, cap)
        for i, piece in enumerate(pieces, 1):
            digest = hashlib.sha1(f"{doc.path}\n{hp}".encode()).hexdigest()[:10]
            chunks.append(Chunk(chunk_id=f"{doc.path}#{digest}-{i}", path=doc.path, study=doc.study,
                                doc_type=doc.doc_type, status=status, heading_path=hp, heading=path[-1],
                                part=i, n_parts=len(pieces), text=piece, last_commit=doc.last_commit))
    return chunks


def unmatched_overrides(docs: list[Document], overrides: tuple[SectionRule, ...]) -> list[SectionRule]:
    """Overrides that match no heading of their file (a stale or mistyped rule)."""
    by_path = {d.path: d for d in docs}
    out = []
    for rule in overrides:
        doc = by_path.get(rule.path)
        heads = [h for p, _ in sections(doc.text) for h in p] if doc else []
        if not any(rule.heading.search(h) for h in heads):
            out.append(rule)
    return out


__all__ = ["Chunk", "MAX_CHARS", "SEP", "TOP", "apply_skips", "chunk_document", "clean_heading", "section_status", "sections",
           "split_body", "strip_sections", "unmatched_overrides"]
