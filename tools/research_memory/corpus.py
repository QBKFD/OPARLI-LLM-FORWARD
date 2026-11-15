"""The corpus: which tracked documents are indexed, with their status.

Rules live in corpus.toml (owner-approved). Files come from `git ls-files`
only, and their text is read from the build commit (`git show <commit>:path`),
so the index matches the commit recorded with it. Untracked files (data,
caches) can never enter. Everything here raises CorpusError on a rule
violation; the build stops.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

STATUSES = ("draft", "registered", "amended", "post hoc", "result", "owner decision", "decision log",
            "reference")
DEFAULT_RULES = Path(__file__).with_name("corpus.toml")


class CorpusError(RuntimeError):
    """A corpus rule is violated (unmapped file, stale entry, bad override)."""


@dataclass(frozen=True)
class SectionRule:
    path: str
    heading: re.Pattern[str]
    status: str


@dataclass(frozen=True)
class SkipRule:
    path: str
    heading: re.Pattern[str]
    reason: str


@dataclass(frozen=True)
class Rules:
    roots: tuple[str, ...]
    names: tuple[str, ...]
    exclude: tuple[str, ...]
    extra_paths: tuple[str, ...]
    extra_globs: tuple[str, ...]
    status_paths: dict[str, str]
    status_globs: dict[str, str]
    sections: tuple[SectionRule, ...] = field(default=())
    skips: tuple[SkipRule, ...] = field(default=())

    @classmethod
    def load(cls, path: Path = DEFAULT_RULES) -> "Rules":
        with open(path, "rb") as f:
            d = tomllib.load(f)
        status = d.get("status", {})
        rules = cls(
            roots=tuple(d["include"]["roots"]), names=tuple(d["include"]["names"]),
            exclude=tuple(d.get("exclude", {}).get("globs", [])),
            extra_paths=tuple(d.get("extra", {}).get("paths", [])),
            extra_globs=tuple(d.get("extra", {}).get("globs", [])),
            status_paths=dict(status.get("paths", {})), status_globs=dict(status.get("globs", {})),
            sections=tuple(SectionRule(s["path"], re.compile(s["heading"]), s["status"])
                           for s in d.get("section", [])),
            skips=tuple(SkipRule(s["path"], re.compile(s["heading"]), s["reason"])
                        for s in d.get("skip_section", [])))
        bad = sorted({v for v in [*rules.status_paths.values(), *rules.status_globs.values(),
                                  *(s.status for s in rules.sections)] if v not in STATUSES})
        if bad:
            raise CorpusError(f"unknown status {bad}; allowed: {list(STATUSES)}")
        return rules

    # ------------------------------------------------------------ selection

    def selects(self, path: str) -> bool:
        p = PurePosixPath(path)
        if p.suffix != ".md" or any(fnmatch.fnmatch(path, g) for g in self.exclude):
            return False
        if path in self.extra_paths or any(fnmatch.fnmatch(path, g) for g in self.extra_globs):
            return True
        return p.parts[0] in self.roots and any(fnmatch.fnmatch(p.name, n) for n in self.names)

    def select(self, tracked: list[str]) -> list[str]:
        chosen = sorted(p for p in tracked if self.selects(p))
        missing = sorted(set(self.extra_paths) - set(tracked))
        if missing:
            raise CorpusError(f"extra paths not tracked by git: {missing}")
        return chosen

    def status_of(self, path: str) -> str:
        if path in self.status_paths:
            return self.status_paths[path]
        for g, s in self.status_globs.items():
            if fnmatch.fnmatch(path, g):
                return s
        raise CorpusError(f"{path}: in the corpus but has no status in corpus.toml")

    def check_statuses(self, paths: list[str]) -> None:
        """Every corpus file has a status; every explicit status names a corpus file."""
        for p in paths:
            self.status_of(p)
        stale = sorted(set(self.status_paths) - set(paths))
        if stale:
            raise CorpusError(f"status entries for files not in the corpus: {stale}")
        for rule in (*self.sections, *self.skips):
            if rule.path not in paths:
                raise CorpusError(f"section rule for a file not in the corpus: {rule.path}")


# ------------------------------------------------------------------ metadata

def study_of(path: str) -> str:
    """research/<study>/... -> <study>; other research/ files -> research; docs -> docs."""
    parts = PurePosixPath(path).parts
    if parts[0] == "research":
        return parts[1] if len(parts) > 2 else "research"
    return parts[0]


def doc_type_of(path: str) -> str:
    """PREREG, REPORT, DECISIONS, README, PROPOSAL, ... (docs/decisions/* -> DECISIONS)."""
    p = PurePosixPath(path)
    if p.parent.as_posix() == "docs/decisions":
        return "DECISIONS"
    stem = p.stem.upper()
    return "DECISIONS" if stem.startswith("DECISIONS") else stem


@dataclass(frozen=True)
class Document:
    path: str
    text: str
    status: str
    study: str
    doc_type: str
    last_commit: str


# ----------------------------------------------------------------------- git

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def head_commit(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def tracked_files(repo: Path) -> list[str]:
    return [p for p in _git(repo, "ls-files", "-z").split("\0") if p]


def dirty_files(repo: Path, paths: list[str]) -> list[str]:
    """Corpus files whose working copy differs from HEAD (not indexed: HEAD is)."""
    if not paths:
        return []
    out = _git(repo, "status", "--porcelain", "-z", "--", *paths)
    return sorted(e[3:] for e in out.split("\0") if len(e) > 3)


def load_documents(repo: Path, rules: Rules, commit: str | None = None) -> tuple[str, list[Document]]:
    """(build commit, documents) for every corpus file at `commit` (default HEAD)."""
    commit = commit or head_commit(repo)
    paths = rules.select(tracked_files(repo))
    rules.check_statuses(paths)
    docs = []
    for p in paths:
        text = _git(repo, "show", f"{commit}:{p}")
        last = _git(repo, "log", "-1", "--format=%H", commit, "--", p).strip()
        docs.append(Document(path=p, text=text, status=rules.status_of(p), study=study_of(p),
                             doc_type=doc_type_of(p), last_commit=last))
    return commit, docs


__all__ = ["CorpusError", "DEFAULT_RULES", "Document", "Rules", "STATUSES", "SectionRule", "SkipRule", "dirty_files",
           "doc_type_of", "head_commit", "load_documents", "study_of", "tracked_files"]
