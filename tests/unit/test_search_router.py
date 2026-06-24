from __future__ import annotations

import pytest

from ego_knowledge.core import EgoKnowledge

from .support import concept_payload, source_payload


@pytest.fixture()
def seeded_search_entries(fresh_ek: EgoKnowledge) -> tuple[EgoKnowledge, dict[str, str]]:
    source = fresh_ek.ingest(
        "source",
        source_payload(
            title="检索总源",
            search_terms=["检索总源", "retrieval", "source", "知识源", "kb-source"],
        ),
    )

    ids = {
        "splade": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="SPLADE 稀疏检索优化",
                aliases=["稀疏检索"],
                search_terms=[
                    "SPLADE",
                    "optimization",
                    "稀疏检索",
                    "sparse retrieval",
                    "splade rerank",
                ],
                tags=["retrieval"],
                body="这份方案关注 SPLADE optimization 在稀疏检索中的调优。" + " " + "x" * 30,
            ),
        ).id,
        "cpp": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="C++ 检索策略",
                aliases=["cpp search"],
                search_terms=[
                    "C++",
                    "retrieval",
                    "检索",
                    "cpp search",
                    "内存检索",
                ],
                tags=["programming"],
                body="C++ retrieval 常见于高性能检索链路。" + " " + "x" * 30,
            ),
        ).id,
        "bge": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="BGE-M3 中文嵌入",
                aliases=["bge embedder"],
                search_terms=[
                    "BGE-M3",
                    "Chinese",
                    "中文",
                    "embedding retrieval",
                    "bge model",
                ],
                tags=["embedding"],
                body="BGE-M3 Chinese retrieval 兼顾中英混合召回。" + " " + "x" * 30,
            ),
        ).id,
        "r2": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="R^2 优化框架",
                aliases=["r squared"],
                search_terms=[
                    "R^2",
                    "optimization",
                    "优化",
                    "objective tuning",
                    "r squared",
                ],
                tags=["math"],
                body="R^2 optimization 常见于目标函数调参。" + " " + "x" * 30,
            ),
        ).id,
        "ai": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="AI 对齐实践",
                aliases=["alignment practice"],
                search_terms=[
                    "AI",
                    "alignment",
                    "对齐",
                    "行为约束",
                    "safe AI",
                ],
                tags=["alignment"],
                body="AI alignment 用于约束模型行为。" + " " + "x" * 30,
            ),
        ).id,
        "openai": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="OpenAI GPT 微调方案",
                aliases=["gpt finetune"],
                search_terms=[
                    "OpenAI",
                    "GPT",
                    "fine-tuning",
                    "plan",
                    "微调",
                ],
                tags=["openai"],
                body="OpenAI GPT fine-tuning plan 需要兼顾数据和目标。" + " " + "x" * 20,
            ),
        ).id,
    }

    fresh_ek.link(ids["ai"], ids["openai"], rel_type="related")
    fresh_ek.link(ids["openai"], ids["splade"], rel_type="related")
    return fresh_ek, ids


@pytest.mark.parametrize(
    ("query", "expected_key"),
    [
        ("SPLADE optimization", "splade"),
        ("C++ retrieval", "cpp"),
        ("BGE-M3 Chinese", "bge"),
        ("R^2 optimization", "r2"),
        ("AI alignment", "ai"),
        ("OpenAI GPT fine-tuning plan", "openai"),
    ],
)
def test_search_covers_phase4_mixed_queries(
    seeded_search_entries: tuple[EgoKnowledge, dict[str, str]],
    query: str,
    expected_key: str,
) -> None:
    ek, ids = seeded_search_entries

    results = ek.search(query, limit=5)

    assert results
    assert results[0].id == ids[expected_key]
    assert results[0].score > 0
    assert results[0].backends
    assert results[0].snippet is not None


def test_search_can_expand_graph_neighbors(
    seeded_search_entries: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    ek, ids = seeded_search_entries

    results = ek.search(
        "AI alignment",
        backends=["bm25", "graph"],
        expand_graph=True,
        limit=5,
    )

    result_ids = [result.id for result in results]
    assert ids["ai"] in result_ids
    assert ids["openai"] in result_ids
