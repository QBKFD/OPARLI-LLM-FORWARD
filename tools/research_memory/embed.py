"""Embedders: the pinned sentence-transformers model, and a fake for tests.

The real model is BAAI/bge-small-en-v1.5 at a fixed Hugging Face revision,
SHA-256-checked on load and always loaded offline (`local_files_only`). It
is downloaded once with `python -m tools.research_memory fetch-model`, the
only command here that touches the network. sentence-transformers (and
torch) are imported lazily: only the real embedder needs
requirements/requirements-memory.lock; nothing else in this package does.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

MODEL_ID = "BAAI/bge-small-en-v1.5"
MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
MODEL_SAFETENSORS_SHA256 = "3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad"
MODEL_DIM = 384
# bge v1.5: queries get this instruction, passages none (the model card's retrieval setup)
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
_FETCH_PATTERNS = ["*.json", "model.safetensors", "vocab.txt", "1_Pooling/*"]


class Embedder(Protocol):
    name: str           # recorded in the index; a query with another name is refused
    dim: int

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        """float32 [n, dim], L2-normalised."""

    def embed_query(self, text: str) -> np.ndarray:
        """float32 [dim], L2-normalised."""


class ModelError(RuntimeError):
    """The pinned model is missing or not the pinned file."""


def fetch_model() -> Path:
    """Download the pinned revision into the Hugging Face cache (network)."""
    from huggingface_hub import snapshot_download   # noqa: PLC0415 - memory lock only

    path = Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION, allow_patterns=_FETCH_PATTERNS))
    _check_weights(path)
    return path


def _snapshot_dir() -> Path:
    from huggingface_hub import snapshot_download   # noqa: PLC0415

    try:
        return Path(snapshot_download(MODEL_ID, revision=MODEL_REVISION, allow_patterns=_FETCH_PATTERNS,
                                      local_files_only=True))
    except Exception as e:                         # noqa: BLE001 - any cache miss means "not fetched"
        raise ModelError(f"{MODEL_ID}@{MODEL_REVISION[:12]} is not in the local cache: run "
                         f"`python -m tools.research_memory fetch-model` ({type(e).__name__})") from None


def _check_weights(path: Path) -> None:
    h = hashlib.sha256()
    with open(path / "model.safetensors", "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if h.hexdigest() != MODEL_SAFETENSORS_SHA256:
        raise ModelError(f"model.safetensors SHA-256 {h.hexdigest()[:16]}… is not the pinned "
                         f"{MODEL_SAFETENSORS_SHA256[:16]}…")


class SentenceTransformerEmbedder:
    name = f"{MODEL_ID}@{MODEL_REVISION}"
    dim = MODEL_DIM

    def __init__(self, batch_size: int = 32) -> None:
        self.batch_size = batch_size
        self._model = None

    def _load(self):
        if self._model is None:
            path = _snapshot_dir()
            _check_weights(path)
            from sentence_transformers import SentenceTransformer   # noqa: PLC0415 - memory lock only

            self._model = SentenceTransformer(str(path), device="cpu", local_files_only=True)
        return self._model

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        v = self._load().encode(texts, batch_size=self.batch_size, normalize_embeddings=True,
                                convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(v, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_passages([QUERY_PREFIX + text])[0]


_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class HashingEmbedder:
    """For tests: a bag of hashed word unigrams and bigrams, L2-normalised.
    Deterministic, no model, no network; good enough to rank a tiny corpus."""
    dim: int = 256
    name: str = "test-hashing-embedder"

    def _vec(self, text: str) -> np.ndarray:
        toks = _TOKEN.findall(text.lower())
        v = np.zeros(self.dim, dtype=np.float32)
        for t in toks + [f"{a} {b}" for a, b in zip(toks, toks[1:])]:
            v[int.from_bytes(hashlib.blake2b(t.encode(), digest_size=4).digest(), "big") % self.dim] += 1.0
        n = float(np.linalg.norm(v))
        return v / n if n else v

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._vec(text)


__all__ = ["Embedder", "HashingEmbedder", "MODEL_DIM", "MODEL_ID", "MODEL_REVISION", "MODEL_SAFETENSORS_SHA256",
           "ModelError", "QUERY_PREFIX", "SentenceTransformerEmbedder", "fetch_model"]
