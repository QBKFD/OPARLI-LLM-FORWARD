"""The citation check and the final answer. Code only; no model is asked.

A claim passes when it has at least one citation and every citation names a
retrieved chunk: the same path, and a heading equal to that chunk's heading
or full heading path (whitespace collapsed, a leading "#"/"§" dropped).
If any claim fails, or there are no claims, the whole answer is
NOT_IN_DOCUMENTS and the reason is kept. All or nothing: a partly cited
answer is never shown.

Status comes from the chunk metadata, never from the model: every citation
is printed with its chunk's status, and a claim whose citations are all
drafts goes under DRAFT_HEADING, so a draft is never shown as a result.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tools.research_memory.chunk import Chunk
from tools.research_memory.store import Hit

NOT_IN_DOCUMENTS = "not in the documents"
DRAFT_HEADING = "From drafts (not registered):"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lstrip("#§").strip())


def _norm_path(s: str) -> str:
    return s.strip().removeprefix("./")


def resolve(citation: dict[str, str], chunks: list[Chunk]) -> list[Chunk]:
    """The retrieved chunks a citation names (several when a section was split)."""
    path, heading = _norm_path(citation.get("path", "")), _norm(citation.get("heading", ""))
    if not path or not heading:
        return []
    return [c for c in chunks if c.path == path and heading in (_norm(c.heading), _norm(c.heading_path))]


@dataclass(frozen=True)
class CheckedClaim:
    text: str
    citations: list[dict[str, Any]]     # path, heading, statuses, chunk_ids

    @property
    def draft_only(self) -> bool:
        return all(c["statuses"] == ["draft"] for c in self.citations)


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    reason: str | None
    claims: list[CheckedClaim] = field(default_factory=list)


def check_claims(claims: list[dict[str, Any]], hits: list[Hit]) -> CheckResult:
    if not claims:
        return CheckResult(False, "no claims: the model found no answer in the retrieved chunks")
    chunks = [h.chunk for h in hits]
    checked = []
    for i, claim in enumerate(claims, 1):
        text = str(claim.get("text", "")).strip()
        if not text:
            return CheckResult(False, f"claim {i} is empty")
        refs = claim.get("citations") or []
        if not refs:
            return CheckResult(False, f"claim {i} has no citation")
        out = []
        for ref in refs:
            found = resolve(ref, chunks)
            if not found:
                return CheckResult(False, f"claim {i} cites {ref.get('path')!r} § {ref.get('heading')!r}, "
                                          "which is not a retrieved chunk")
            out.append({"path": found[0].path, "heading": found[0].heading_path,
                        "statuses": sorted({c.status for c in found}), "chunk_ids": [c.chunk_id for c in found]})
        checked.append(CheckedClaim(text, out))
    return CheckResult(True, None, checked)


def _label(c: dict[str, Any]) -> str:
    return f"[{c['path']} § {c['heading']} — {'/'.join(s.upper() for s in c['statuses'])}]"


def render(result: CheckResult) -> str:
    """The answer text: cited claims, drafts apart, or NOT_IN_DOCUMENTS."""
    if not result.ok:
        return NOT_IN_DOCUMENTS
    main = [c for c in result.claims if not c.draft_only]
    drafts = [c for c in result.claims if c.draft_only]
    lines = [f"- {c.text} " + " ".join(_label(r) for r in c.citations) for c in main]
    if drafts:
        lines += ([""] if lines else []) + [DRAFT_HEADING]
        lines += [f"- {c.text} " + " ".join(_label(r) for r in c.citations) for c in drafts]
    return "\n".join(lines)


__all__ = ["CheckResult", "CheckedClaim", "DRAFT_HEADING", "NOT_IN_DOCUMENTS", "check_claims", "render", "resolve"]
