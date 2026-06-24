"""Search sub-package types — shared across main shell and backends."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .._dense_embedder import EmbedResult


@dataclass(slots=True)
class SearchResult:
    id: str
    score: float
    backends: list[str]
    snippet: str | None = None


class DenseSearchEmbedder(Protocol):
    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        """Return embeddings for dense query search."""
