"""SearchRouter class: exact/BM25/graph/dense routing with authority fusion."""

from __future__ import annotations

from pathlib import Path

from .._query_preprocess import Segment, SegmentType, parse_query
from ..registry import Registry
from ..unicode_utils import normalize_fullwidth, to_nfc
from ._backends.bm25 import (
    fuse_bm25,
    search_cn_single_seg,
    search_en_single_seg,
    search_tri_single_seg,
)
from ._backends.dense import DENSE_WEIGHT, search_dense
from ._backends.exact import exact_query_matches, exact_term_matches
from ._backends.graph import graph_neighbors
from ._helpers import _build_snippet, _match_filters, _merge_backends
from ._types import DenseSearchEmbedder, SearchResult

AUTHORITY_WEIGHT = 0.3


class SearchRouter:
    """Search router for exact, BM25, graph, dense and authority fusion."""

    def __init__(
        self,
        registry: Registry,
        *,
        data_root: Path | None = None,
        embedder: DenseSearchEmbedder | None = None,
        dense_weight: float = DENSE_WEIGHT,
    ) -> None:
        self._registry = registry
        self._data_root = data_root or registry.path.parent.parent
        self._embedder = embedder
        self._dense_weight = dense_weight
        self._jieba_dict_dir = self._data_root / "registry" / "jieba"
        self._jieba_fallback_log = self._data_root / "logs" / "refresh" / "jieba-fallback.log"

    @property
    def dense_enabled(self) -> bool:
        return self._embedder is not None and self._registry.dense_index_populated()

    def search(
        self,
        query: str,
        kinds: list[str] | None = None,
        filters: dict[str, object] | None = None,
        backends: list[str] | None = None,
        limit: int = 20,
        expand_graph: bool = True,
        include_archived: bool = False,
    ) -> list[SearchResult]:
        """Run exact -> BM25 -> graph -> dense -> authority retrieval."""

        normalized_query = normalize_fullwidth(to_nfc(query).strip())
        if not normalized_query:
            return []

        segments = parse_query(normalized_query)
        if not segments:
            return []

        default_backends = ["exact", "bm25"]
        if self.dense_enabled:
            default_backends.append("dense")
        backend_list = list(dict.fromkeys(backends or default_backends))

        if "exact" in backend_list:
            exact_results = self._apply_filters(
                exact_query_matches(self._registry, normalized_query),
                kinds=kinds,
                filters=filters,
                include_archived=include_archived,
            )
            if exact_results:
                ranked_exact = self._rank_results(exact_results, limit)
                self._populate_snippets(ranked_exact, segments)
                return ranked_exact

        results_by_id: dict[str, SearchResult] = {}
        if "bm25" in backend_list:
            results_by_id = self._apply_filters(
                fuse_bm25(
                    segments,
                    limit=max(limit * 5, 20),
                    registry=self._registry,
                    jieba_dict_dir=self._jieba_dict_dir,
                    jieba_fallback_log=self._jieba_fallback_log,
                ),
                kinds=kinds,
                filters=filters,
                include_archived=include_archived,
            )

        if expand_graph and "graph" in backend_list and results_by_id:
            for entry_id, result in graph_neighbors(
                self._registry,
                results_by_id,
                include_archived=include_archived,
            ).items():
                existing = results_by_id.get(entry_id)
                if existing is None:
                    results_by_id[entry_id] = result
                    continue
                existing.score = max(existing.score, result.score)
                existing.backends = _merge_backends(existing.backends, result.backends)

            results_by_id = self._apply_filters(
                results_by_id,
                kinds=kinds,
                filters=filters,
                include_archived=include_archived,
            )

        if "dense" in backend_list and self.dense_enabled:
            dense_results = self._apply_filters(
                self._search_dense(normalized_query, limit=max(limit * 5, 20)),
                kinds=kinds,
                filters=filters,
                include_archived=include_archived,
            )
            if "dense" in backend_list and len(backend_list) == 1:
                results_by_id = dense_results
                ranked = self._rank_results(results_by_id, limit)
                self._populate_snippets(ranked, segments)
                return ranked
            for entry_id, result in dense_results.items():
                existing = results_by_id.get(entry_id)
                if existing is None:
                    results_by_id[entry_id] = result
                    continue
                existing.score += result.score
                existing.backends = _merge_backends(existing.backends, result.backends)

        ranked = self._rank_results(results_by_id, limit)
        self._populate_snippets(ranked, segments)
        return ranked

    def _search_dense(
        self,
        query: str,
        *,
        limit: int,
    ) -> dict[str, SearchResult]:
        if self._embedder is None:
            return {}
        return search_dense(
            query,
            limit=limit,
            registry=self._registry,
            embedder=self._embedder,
            dense_weight=self._dense_weight,
            data_root=self._data_root,
        )

    # -- thin delegation wrappers for backends (used by tests) --

    def _search_cn_single_seg(
        self,
        segment: Segment,
        limit: int,
    ) -> list[SearchResult]:
        return search_cn_single_seg(
            segment,
            limit,
            registry=self._registry,
            jieba_dict_dir=self._jieba_dict_dir,
            jieba_fallback_log=self._jieba_fallback_log,
        )

    def _search_en_single_seg(
        self,
        segment: Segment,
        limit: int,
    ) -> list[SearchResult]:
        return search_en_single_seg(segment, limit, registry=self._registry)

    def _search_tri_single_seg(
        self,
        segment: Segment,
        limit: int,
    ) -> list[SearchResult]:
        return search_tri_single_seg(segment, limit, registry=self._registry)

    def _exact_query_matches(self, query: str) -> dict[str, SearchResult]:
        return exact_query_matches(self._registry, query)

    def _exact_term_matches(
        self,
        term: str,
        *,
        include_title_substring: bool,
    ) -> dict[str, SearchResult]:
        return exact_term_matches(
            self._registry,
            term,
            include_title_substring=include_title_substring,
        )

    def _query_fts(
        self,
        table_name: str,
        match_expr: str,
        *,
        limit: int,
        weights: tuple[float, ...],
        backend_name: str,
    ) -> list[SearchResult]:
        from ._backends.bm25 import query_fts

        return query_fts(
            self._registry.conn,
            table_name,
            match_expr,
            limit=limit,
            weights=weights,
            backend_name=backend_name,
        )

    def _fuse_bm25(
        self,
        segments: list[Segment],
        *,
        limit: int,
    ) -> dict[str, SearchResult]:
        return fuse_bm25(
            segments,
            limit=limit,
            registry=self._registry,
            jieba_dict_dir=self._jieba_dict_dir,
            jieba_fallback_log=self._jieba_fallback_log,
        )

    def _graph_neighbors(
        self,
        seeds: dict[str, SearchResult],
        *,
        include_archived: bool = False,
    ) -> dict[str, SearchResult]:
        return graph_neighbors(
            self._registry,
            seeds,
            include_archived=include_archived,
        )

    def _apply_filters(
        self,
        results: dict[str, SearchResult],
        *,
        kinds: list[str] | None,
        filters: dict[str, object] | None,
        include_archived: bool = False,
    ) -> dict[str, SearchResult]:
        if not results:
            return {}
        if not kinds and not filters and include_archived:
            return results

        allowed_kinds = set(kinds or [])
        filtered: dict[str, SearchResult] = {}
        for entry_id, result in results.items():
            entry = self._registry.get_entry(entry_id)
            if not include_archived and entry.status.value == "archived":
                continue
            if allowed_kinds and entry.kind.value not in allowed_kinds:
                continue
            if filters and not _match_filters(entry, filters):
                continue
            filtered[entry_id] = result
        return filtered

    def _rank_results(
        self,
        results_by_id: dict[str, SearchResult],
        limit: int,
        *,
        authority_weight: float = AUTHORITY_WEIGHT,
    ) -> list[SearchResult]:
        ranked_results: dict[str, SearchResult] = {}
        for result in results_by_id.values():
            ranked_results[result.id] = SearchResult(
                id=result.id,
                score=result.score,
                backends=list(result.backends),
                snippet=result.snippet,
            )
        self._apply_authority_boost(ranked_results, authority_weight)
        return sorted(
            ranked_results.values(),
            key=lambda result: (-result.score, result.id),
        )[:limit]

    def _apply_authority_boost(
        self,
        results: dict[str, SearchResult],
        weight: float,
    ) -> None:
        if not results or weight <= 0:
            return

        authority_map = self._registry.authority_score_map(list(results.keys()))
        if not authority_map:
            return

        max_authority = max(authority_map.values(), default=0.0)
        if max_authority <= 0:
            return

        for entry_id, result in results.items():
            authority = authority_map.get(entry_id, 0.0)
            if authority <= 0:
                continue
            authority_norm = authority / max_authority
            result.score *= 1.0 + weight * authority_norm

    def _populate_snippets(
        self,
        results: list[SearchResult],
        segments: list[Segment],
    ) -> None:
        needle_texts = [seg.text for seg in segments if seg.type != SegmentType.EMOJI]
        for result in results:
            entry = self._registry.get_entry(result.id)
            result.snippet = _build_snippet(entry, needle_texts)
