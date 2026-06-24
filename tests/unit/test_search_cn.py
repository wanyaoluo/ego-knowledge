from __future__ import annotations

from ego_knowledge.search import SearchRouter, Segment, SegmentType

from .support import concept_payload, source_payload


def test_search_cn_backend_returns_chinese_match(fresh_ek, ek_root) -> None:
    source = fresh_ek.ingest("source", source_payload(title="中文检索来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="知识治理总览",
            aliases=["治理图谱"],
            search_terms=[
                "知识治理总览",
                "knowledge governance",
                "治理图谱",
                "知识治理",
                "kg-overview",
            ],
            body="知识治理与检索治理需要一起建模。" + " " + "x" * 40,
        ),
    )

    router = SearchRouter(fresh_ek._registry, data_root=ek_root)
    results = router._search_cn_single_seg(Segment(SegmentType.CJK, "知识治理"), limit=5)

    assert results
    assert results[0].id == concept.id


def test_search_cn_query_supports_fullwidth_segment(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="全角来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="ＡＩ 治理流程",
            aliases=["ＡＩ治理"],
            search_terms=["ＡＩ 治理流程", "ai governance", "ＡＩ治理", "治理流程", "alias-ai"],
            body="全角输入也应该可召回。" + " " + "x" * 40,
        ),
    )

    results = fresh_ek.search("ＡＩ 治理", backends=["bm25"], limit=5)

    assert results
    assert results[0].id == concept.id
