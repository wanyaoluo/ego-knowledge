from __future__ import annotations

from ego_knowledge.search import SearchRouter, Segment, SegmentType

from .support import concept_payload, source_payload


def test_search_tri_backend_matches_mixed_symbol_token(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="三元检索来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="BGE-M3 中文嵌入",
            aliases=["bge embedder"],
            search_terms=[
                "BGE-M3 中文嵌入",
                "bge-m3",
                "中文嵌入",
                "embedding retrieval",
                "bge model",
            ],
            body="BGE-M3 兼顾中英混合召回。" + " " + "x" * 40,
        ),
    )

    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = router._search_tri_single_seg(Segment(SegmentType.MIXED, "BGE-M3"), limit=5)

    assert results
    assert results[0].id == concept.id


def test_search_tri_backend_ignores_short_tokens(fresh_ek, ek_root) -> None:
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)

    assert router._search_tri_single_seg(Segment(SegmentType.MIXED, "AI"), limit=5) == []
