"""Search router: dispatch to backends and fuse results."""

from __future__ import annotations

from pathlib import Path

from .._query_preprocess import (
    Segment,
    SegmentType,
    _ascii_match_expr,
    _classify,
    _expand_mixed_segments,
    _generate_symbol_variants,
    _quote_fts,
    _split_chunk,
    parse_query,
)
from ..registry import Registry
from ._backends.bm25 import (
    BM25_WEIGHTS_CN,
    BM25_WEIGHTS_EN,
    BM25_WEIGHTS_TRI,
    PARTIAL_SEGMENT_SCORE_FACTOR,
)
from ._backends.dense import DENSE_WEIGHT, cosine_similarity_scores
from ._backends.exact import _exact_sql_statements, exact_term_matches
from ._helpers import _build_snippet, _match_filters, _merge_backends
from ._router import AUTHORITY_WEIGHT, SearchRouter
from ._types import DenseSearchEmbedder, SearchResult

__all__ = [
    "AUTHORITY_WEIGHT",
    "BM25_WEIGHTS_CN",
    "BM25_WEIGHTS_EN",
    "BM25_WEIGHTS_TRI",
    "DENSE_WEIGHT",
    "PARTIAL_SEGMENT_SCORE_FACTOR",
    "SearchResult",
    "SearchRouter",
    "Segment",
    "SegmentType",
    "_ascii_match_expr",
    "_build_snippet",
    "_classify",
    "_cosine_similarity_scores",
    "_exact_sql_statements",
    "_expand_mixed_segments",
    "_generate_symbol_variants",
    "_match_filters",
    "_merge_backends",
    "_quote_fts",
    "_split_chunk",
    "exact_term_matches",
    "parse_query",
    "search",
]

# Alias for backward compatibility: _cosine_similarity_scores
_cosine_similarity_scores = cosine_similarity_scores


def search(
    registry: Registry,
    query: str,
    *,
    kinds: list[str] | None = None,
    filters: dict[str, object] | None = None,
    backends: list[str] | None = None,
    limit: int = 20,
    expand_graph: bool = True,
    data_root: Path | None = None,
    embedder: DenseSearchEmbedder | None = None,
    include_archived: bool = False,
) -> list[SearchResult]:
    """Convenience entry point for registry-backed searching."""

    return SearchRouter(registry, data_root=data_root, embedder=embedder).search(
        query=query,
        kinds=kinds,
        filters=filters,
        backends=backends,
        limit=limit,
        expand_graph=expand_graph,
        include_archived=include_archived,
    )
