"""BM25 / FTS backends: Chinese, English, trigram retrieval."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from ..._query_preprocess import (
    Segment,
    SegmentType,
    _ascii_match_expr,
    _classify,
    _expand_mixed_segments,
    _generate_symbol_variants,
    _quote_fts,
)
from ...errors import StorageError
from ...registry import Registry
from ...tokenizer import sync_runtime_words, tokenize
from .._helpers import _merge_backends
from .._types import SearchResult

BM25_WEIGHTS_CN = (3.0, 2.0, 2.0, 1.5, 1.0)
BM25_WEIGHTS_TRI = (2.0, 1.5, 1.5, 1.0)
BM25_WEIGHTS_EN = (3.0, 2.0, 2.0, 1.0)
PARTIAL_SEGMENT_SCORE_FACTOR = 0.7


def query_fts(
    conn: sqlite3.Connection,
    table_name: str,
    match_expr: str,
    *,
    limit: int,
    weights: tuple[float, ...],
    backend_name: str,
) -> list[SearchResult]:
    if not match_expr:
        return []

    weight_sql = ", ".join(str(weight) for weight in weights)
    # Safe f-string: table_name is selected by internal callers and weights
    # are fixed module constants; user input stays bound via parameters.
    sql = (
        f"SELECT id, -bm25({table_name}, {weight_sql}) AS score "
        f"FROM {table_name} "
        f"WHERE {table_name} MATCH ? "
        "ORDER BY score DESC "
        "LIMIT ?"
    )
    try:
        rows = conn.execute(sql, (match_expr, limit)).fetchall()
    except sqlite3.Error as exc:
        raise StorageError(f"执行 {backend_name} 检索失败: {exc}") from exc
    return [
        SearchResult(
            id=cast(str, row["id"]),
            score=float(cast(float, row["score"])),
            backends=[backend_name],
        )
        for row in rows
    ]


def search_cn_single_seg(
    segment: Segment,
    limit: int,
    *,
    registry: Registry,
    jieba_dict_dir: Path | None,
    jieba_fallback_log: Path | None,
) -> list[SearchResult]:
    tokens = tokenize(
        segment.text,
        custom_dict_dir=jieba_dict_dir,
        fallback_log_path=jieba_fallback_log,
    )
    if not tokens:
        return []
    match_expr = " AND ".join(_quote_fts(token) for token in tokens)
    return query_fts(
        registry.conn,
        "entries_fts_cn",
        match_expr,
        limit=limit,
        weights=BM25_WEIGHTS_CN,
        backend_name="fts_cn",
    )


def search_en_single_seg(
    segment: Segment,
    limit: int,
    *,
    registry: Registry,
) -> list[SearchResult]:
    match_expr = _ascii_match_expr(segment.text)
    if not match_expr:
        return []
    return query_fts(
        registry.conn,
        "entries_fts_en",
        match_expr,
        limit=limit,
        weights=BM25_WEIGHTS_EN,
        backend_name="fts_en",
    )


def search_tri_single_seg(
    segment: Segment,
    limit: int,
    *,
    registry: Registry,
) -> list[SearchResult]:
    if len(segment.text) < 3:
        return []
    return query_fts(
        registry.conn,
        "entries_fts_tri",
        _quote_fts(segment.text),
        limit=limit,
        weights=BM25_WEIGHTS_TRI,
        backend_name="fts_tri",
    )


def fuse_bm25(
    segments: list[Segment],
    *,
    limit: int,
    registry: Registry,
    jieba_dict_dir: Path | None,
    jieba_fallback_log: Path | None,
) -> dict[str, SearchResult]:
    from .exact import exact_term_matches

    meaningful_segments = [seg for seg in segments if seg.type != SegmentType.EMOJI]
    if not meaningful_segments:
        return {}

    sync_runtime_words((*registry.all_aliases(), *registry.all_tags()))

    expanded_segments = _expand_mixed_segments(meaningful_segments)

    combined: dict[str, SearchResult] = {}
    segment_hits: dict[str, set[int]] = {}

    def accumulate(
        results: Iterable[SearchResult],
        weight: float,
        segment_idx: int,
    ) -> None:
        for result in results:
            if result.id not in segment_hits:
                segment_hits[result.id] = set()
            segment_hits[result.id].add(segment_idx)
            if result.id in combined:
                existing = combined[result.id]
                existing.score += result.score * weight
                existing.backends = _merge_backends(
                    existing.backends,
                    result.backends,
                )
                continue
            combined[result.id] = SearchResult(
                id=result.id,
                score=result.score * weight,
                backends=list(result.backends),
                snippet=result.snippet,
            )

    for index, segment in enumerate(expanded_segments):
        if segment.type == SegmentType.CJK:
            accumulate(
                search_cn_single_seg(
                    segment,
                    limit,
                    registry=registry,
                    jieba_dict_dir=jieba_dict_dir,
                    jieba_fallback_log=jieba_fallback_log,
                ),
                1.0,
                index,
            )
            accumulate(
                search_tri_single_seg(segment, limit, registry=registry),
                0.5,
                index,
            )
            continue
        if segment.type == SegmentType.ASCII_WORD:
            accumulate(
                search_en_single_seg(segment, limit, registry=registry),
                1.0,
                index,
            )
            continue
        if segment.type == SegmentType.ASCII_SHORT:
            accumulate(
                exact_term_matches(
                    registry,
                    segment.text,
                    include_title_substring=True,
                ).values(),
                2.0,
                index,
            )
            accumulate(
                search_en_single_seg(segment, limit, registry=registry),
                1.0,
                index,
            )
            continue
        if segment.type == SegmentType.SYMBOL_TOKEN:
            accumulate(
                exact_term_matches(
                    registry,
                    segment.text,
                    include_title_substring=False,
                ).values(),
                2.0,
                index,
            )
            accumulate(
                search_en_single_seg(segment, limit, registry=registry),
                1.0,
                index,
            )
            for variant_text in _generate_symbol_variants(segment.text):
                variant_seg = _classify(variant_text)
                accumulate(
                    search_en_single_seg(variant_seg, limit, registry=registry),
                    0.8,
                    index,
                )
            continue
        if segment.type == SegmentType.MIXED:
            accumulate(
                exact_term_matches(
                    registry,
                    segment.text,
                    include_title_substring=False,
                ).values(),
                2.0,
                index,
            )
            accumulate(
                search_en_single_seg(segment, limit, registry=registry),
                1.0,
                index,
            )
            accumulate(
                search_tri_single_seg(segment, limit, registry=registry),
                0.5,
                index,
            )
            continue
        if segment.type == SegmentType.VERSION:
            accumulate(
                exact_term_matches(
                    registry,
                    segment.text,
                    include_title_substring=False,
                ).values(),
                2.0,
                index,
            )
            accumulate(
                search_en_single_seg(segment, limit, registry=registry),
                1.0,
                index,
            )
            accumulate(
                search_tri_single_seg(segment, limit, registry=registry),
                0.5,
                index,
            )
            continue
        if segment.type == SegmentType.NUMBER:
            accumulate(
                exact_term_matches(
                    registry,
                    segment.text,
                    include_title_substring=False,
                ).values(),
                2.0,
                index,
            )
            accumulate(
                search_en_single_seg(segment, limit, registry=registry),
                1.0,
                index,
            )
            continue
        if segment.type == SegmentType.FULLWIDTH:
            accumulate(
                search_cn_single_seg(
                    segment,
                    limit,
                    registry=registry,
                    jieba_dict_dir=jieba_dict_dir,
                    jieba_fallback_log=jieba_fallback_log,
                ),
                1.0,
                index,
            )
            continue

    total_segments = len(expanded_segments)
    for entry_id, hits in segment_hits.items():
        if len(hits) < total_segments:
            combined[entry_id].score *= PARTIAL_SEGMENT_SCORE_FACTOR

    return combined
