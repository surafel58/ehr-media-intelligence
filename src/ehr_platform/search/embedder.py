"""Text embedders. The real one is sentence-transformers; tests use a small offline one."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


class Embedder(Protocol):
    name: str

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str = MODEL_NAME, device: str | None = None):
        from sentence_transformers import SentenceTransformer  # heavy import, load once

        self.name = model_name
        self.model = SentenceTransformer(model_name, device=device)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self.model.encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()


class HashEmbedder:
    """Bag of hashed words. Deterministic and offline, for tests only."""

    name = "hash-embedder"

    def __init__(self, dims: int = 256):
        self.dims = dims

    def encode(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dims
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims] += 1.0
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
