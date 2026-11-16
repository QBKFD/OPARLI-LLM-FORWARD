"""The on-disk index: one SQLite file (git-ignored), rebuilt whole.

Tables: `meta` (key/value: embedder name, dimension, build commit, built at,
corpus SHA-256, counts) and `chunks` (the chunk metadata, text and its
float32 embedding). A build writes a temporary file and renames it over the
old one, so a failed build leaves the previous index intact. Retrieval loads
every embedding into memory once (~650 x 384 floats) and ranks by cosine.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from tools.research_memory.chunk import Chunk

SCHEMA_VERSION = 1
_COLS = ("chunk_id", "path", "study", "doc_type", "status", "heading_path", "heading", "part", "n_parts", "text",
         "last_commit")
_SCHEMA = f"""
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE chunks (
    rowid_     INTEGER PRIMARY KEY,
    {", ".join(f"{c} {'INTEGER' if c in ('part', 'n_parts') else 'TEXT'} NOT NULL" for c in _COLS)},
    embedding  BLOB NOT NULL,
    UNIQUE (chunk_id)
);
"""


class IndexProblem(RuntimeError):
    """The index is missing, from another schema, or built with another embedder."""


def write_index(path: Path, chunks: list[Chunk], embeddings: np.ndarray, meta: dict[str, object]) -> None:
    if len(chunks) != len(embeddings):
        raise ValueError(f"{len(chunks)} chunks but {len(embeddings)} embeddings")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        con.executescript(_SCHEMA)
        rows = [(*(getattr(c, k) for k in _COLS), np.asarray(e, dtype=np.float32).tobytes())
                for c, e in zip(chunks, embeddings)]
        con.executemany(f"INSERT INTO chunks ({', '.join(_COLS)}, embedding) VALUES "
                        f"({', '.join('?' * (len(_COLS) + 1))})", rows)
        full = {"schema_version": SCHEMA_VERSION, **meta}
        con.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                        [(k, json.dumps(v)) for k, v in full.items()])
        con.commit()
    finally:
        con.close()
    os.replace(tmp, path)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float

    def to_dict(self) -> dict[str, object]:
        return {**asdict(self.chunk), "score": round(self.score, 6)}


class Index:
    def __init__(self, chunks: list[Chunk], matrix: np.ndarray, meta: dict[str, object]) -> None:
        self.chunks, self.matrix, self.meta = chunks, matrix, meta

    @classmethod
    def load(cls, path: Path) -> "Index":
        if not path.exists():
            raise IndexProblem(f"no index at {path}: run `python -m tools.research_memory build`")
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            meta = {k: json.loads(v) for k, v in con.execute("SELECT key, value FROM meta")}
            if meta.get("schema_version") != SCHEMA_VERSION:
                raise IndexProblem(f"index schema {meta.get('schema_version')} is not {SCHEMA_VERSION}: rebuild")
            rows = con.execute(f"SELECT {', '.join(_COLS)}, embedding FROM chunks ORDER BY rowid_").fetchall()
        finally:
            con.close()
        dim = int(meta["dim"])
        chunks = [Chunk(**dict(zip(_COLS, r[:-1]))) for r in rows]
        matrix = (np.stack([np.frombuffer(r[-1], dtype=np.float32) for r in rows]) if rows
                  else np.zeros((0, dim), np.float32))
        return cls(chunks, matrix, meta)

    def search(self, query_vec: np.ndarray, k: int, embedder_name: str) -> list[Hit]:
        if embedder_name != self.meta.get("embedder"):
            raise IndexProblem(f"index built with {self.meta.get('embedder')!r}, query embedder is "
                              f"{embedder_name!r}: rebuild")
        if not self.chunks or k <= 0:
            return []
        scores = self.matrix @ np.asarray(query_vec, dtype=np.float32)
        top = np.argsort(-scores, kind="stable")[:k]
        return [Hit(self.chunks[i], float(scores[i])) for i in top]


__all__ = ["Hit", "Index", "IndexProblem", "SCHEMA_VERSION", "write_index"]
