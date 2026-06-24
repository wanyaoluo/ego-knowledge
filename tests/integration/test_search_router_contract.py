"""Snapshot tests for the five-route fusion contract (Phase 7.0 freeze).

Contract locked:  exact → bm25 → graph → dense → authority
See: docs/reference/search-contract.md

Dense route assertions use the real ``SearchRouter`` with a fake embedder.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from ego_knowledge._dense_embedder import EMBEDDING_DIM, EmbedResult
from ego_knowledge._dense_index import store_embedding
from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.search import SearchRouter
from tests.unit.support import concept_payload, source_payload

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def seeded_ek(fresh_ek: EgoKnowledge) -> tuple[EgoKnowledge, dict[str, str]]:
    """Seed knowledge base with entries covering all five routes."""
    source = fresh_ek.ingest("source", source_payload(title="融合契约来源"))

    ids = {
        "alias_target": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="精确匹配目标",
                aliases=["契约别名"],
                search_terms=[
                    "精确匹配目标",
                    "exact route",
                    "契约别名",
                    "alias exact",
                    "target-alias",
                ],
                body="用于测试精确匹配早返回的条目。" + "x" * 50,
            ),
        ).id,
        "bm25_target": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="BM25 中文检索",
                search_terms=[
                    "BM25 中文检索",
                    "BM25",
                    "中文检索",
                    "lexical retrieval",
                    "bm25-contract",
                ],
                body="BM25 关键词匹配检索测试内容。" + "x" * 50,
            ),
        ).id,
        "graph_seed": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="图种子",
                search_terms=["图种子", "graph seed", "邻居扩展", "graph route", "seed-node"],
                body="图邻居扩展种子节点。" + "x" * 50,
            ),
        ).id,
        "graph_neighbor": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="图邻居",
                search_terms=[
                    "图邻居",
                    "graph neighbor",
                    "扩展目标",
                    "graph route",
                    "neighbor-node",
                ],
                body="图邻居扩展目标节点。" + "x" * 50,
            ),
        ).id,
        "dense_only": fresh_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="语义相似但词汇不重叠",
                search_terms=["语义相似", "dense vector", "向量", "semantic route", "dense-only"],
                body="这个条目只能通过语义相似度匹配到。" + "x" * 50,
            ),
        ).id,
    }

    fresh_ek.link(ids["graph_seed"], ids["graph_neighbor"], rel_type="related")

    return fresh_ek, ids


class FakeDenseEmbedder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        self.calls += 1
        if self.fail:
            raise RuntimeError("SiliconFlow API 503")
        return EmbedResult(
            embeddings=[_embedding(1.0) for _ in texts],
            model_revision="test",
            tokens_used=1,
        )


def _embedding(value: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = value
    return vector


def _store_dense(ek: EgoKnowledge, entry_id: str, value: float = 1.0) -> None:
    entry = ek.get(entry_id)
    store_embedding(
        ek._registry,
        entry_id,
        _embedding(value),
        compute_embedding_content_hash(entry),
        "test-rev",
    )


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_exact_hit_skips_other_backends(
    seeded_ek: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    """命中 ID/alias 时 dense/bm25/graph 都不跑."""
    ek, ids = seeded_ek
    registry = ek._registry

    _store_dense(ek, ids["bm25_target"])
    embedder = FakeDenseEmbedder()
    router = SearchRouter(
        registry,
        embedder=embedder,
    )

    results = router.search("契约别名", limit=5)

    assert len(results) >= 1
    assert results[0].id == ids["alias_target"]
    # exact 早返回: 只有 exact backend
    assert results[0].backends == ["exact"]
    # dense 未被调用
    assert embedder.calls == 0


def test_default_backends_include_dense_when_embedder_present(
    seeded_ek: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    """默认 `ek.search("xxx")` 启用 dense + bm25 + exact + authority.

    使用真实路由验证：embedder 存在且索引非空时 dense 路被调用且结果合并。
    """
    ek, ids = seeded_ek
    registry = ek._registry

    _store_dense(ek, ids["dense_only"])
    embedder = FakeDenseEmbedder()
    router = SearchRouter(
        registry,
        embedder=embedder,
    )

    results = router.search("完全无关的查询词汇", limit=10)

    # dense_only 条目应出现（通过 dense 路召回）
    result_ids = [r.id for r in results]
    assert ids["dense_only"] in result_ids

    dense_result = next(r for r in results if r.id == ids["dense_only"])
    assert "dense" in dense_result.backends

    # dense 确实被调用
    assert embedder.calls >= 1


def test_dense_disabled_when_embedder_absent(
    seeded_ek: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    """embedder 缺失时 dense 路不启用."""
    ek, ids = seeded_ek
    registry = ek._registry

    _store_dense(ek, ids["dense_only"])
    router = SearchRouter(
        registry,
        embedder=None,
    )

    results = router.search("完全无关的查询词汇", limit=10)

    assert ids["dense_only"] not in [result.id for result in results]


def test_dense_failure_fallback_to_four_routes(
    seeded_ek: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    """dense 路抛异常，其他四路正常返回."""
    ek, ids = seeded_ek
    registry = ek._registry

    _store_dense(ek, ids["dense_only"])
    embedder = FakeDenseEmbedder(fail=True)
    router = SearchRouter(
        registry,
        embedder=embedder,
    )

    # BM25 能命中的查询（不触发 exact 早返回：非标题/别名/term 精确匹配）
    results = router.search("BM25 检索", limit=5)

    # 结果应来自 bm25/exact 路由
    assert len(results) >= 1
    assert any(r.id == ids["bm25_target"] for r in results)

    # 不应出现 dense backend（dense 失败降级）
    all_backends: set[str] = set()
    for r in results:
        all_backends.update(r.backends)
    assert "dense" not in all_backends

    # dense 被调用过但静默失败
    assert embedder.calls >= 1


def test_graph_expand_off_by_default(
    seeded_ek: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    """graph 路需 expand_graph=True + backends 含 'graph' 才跑.

    默认 backends=['exact', 'bm25'] 不含 'graph'，因此 graph 不跑。
    为隔离测试（避免 exact 早返回干扰），使用 backends=['bm25'] 证明
    graph 仅在 backends 列表显式包含 'graph' 时才触发。
    """
    ek, ids = seeded_ek
    registry = ek._registry

    router = SearchRouter(registry)

    # bm25 only → graph 不扩展（backends 不含 "graph"）
    results_no_graph = router.search(
        "图种子",
        backends=["bm25"],
        expand_graph=True,
        limit=10,
    )

    no_graph_ids = [r.id for r in results_no_graph]
    # BM25 命中 graph_seed
    assert ids["graph_seed"] in no_graph_ids
    # graph_neighbor 不出现（backends 不含 "graph"）
    assert ids["graph_neighbor"] not in no_graph_ids

    # 加 "graph" 到 backends → 邻居出现
    results_with_graph = router.search(
        "图种子",
        backends=["bm25", "graph"],
        expand_graph=True,
        limit=10,
    )

    with_graph_ids = [r.id for r in results_with_graph]
    assert ids["graph_neighbor"] in with_graph_ids

    graph_result = next(r for r in results_with_graph if r.id == ids["graph_neighbor"])
    assert "graph" in graph_result.backends
