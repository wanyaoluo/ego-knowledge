from __future__ import annotations

from ego_knowledge.search import SearchRouter, Segment, SegmentType, _ascii_match_expr

from .support import concept_payload, source_payload


def test_ascii_match_expr_collapses_non_ascii_tokens() -> None:
    assert _ascii_match_expr("C++ 检索 2.0") == '"C++" AND "2.0"'


def test_search_en_backend_handles_ascii_queries(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="英文检索来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="OpenAI GPT 微调方案",
            aliases=["gpt finetune"],
            search_terms=[
                "OpenAI GPT 微调方案",
                "openai gpt",
                "gpt finetune",
                "微调",
                "fine-tuning",
            ],
            body="OpenAI GPT fine-tuning plan 关注数据和目标。" + " " + "x" * 20,
        ),
    )

    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = router._search_en_single_seg(Segment(SegmentType.ASCII_WORD, "OpenAI"), limit=5)

    assert results
    assert results[0].id == concept.id


def test_search_en_backend_returns_empty_when_ascii_expr_is_blank(fresh_ek, ek_root) -> None:
    router = SearchRouter(fresh_ek._registry, data_root=ek_root)

    assert router._search_en_single_seg(Segment(SegmentType.ASCII_WORD, "知识"), limit=5) == []
