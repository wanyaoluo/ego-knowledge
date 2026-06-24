from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from ego_knowledge._dense_embedder import EMBEDDING_DIM, EmbedResult
from ego_knowledge._dense_index import store_embedding
from ego_knowledge._dense_queue import QUEUE_DEPTH_HARD, drain, enqueue
from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.search import DENSE_WEIGHT, SearchRouter, _cosine_similarity_scores
from tests.unit.support import concept_payload, source_payload


class FakeEmbedder:
    def __init__(self, vector: list[float] | None = None, *, fail: bool = False) -> None:
        self.vector = vector or _embedding(1.0)
        self.fail = fail
        self.batch_calls = 0
        self.cached_calls: list[str] = []
        self.last_model_revision: str | None = "fake-rev"

    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        self.batch_calls += 1
        if self.fail:
            raise RuntimeError("dense boom")
        return EmbedResult(
            embeddings=[list(self.vector) for _ in texts],
            model_revision="test-rev",
            tokens_used=len(texts),
        )

    def embed_cached(self, entry_id: str, embedding_content_hash: str, text: str) -> list[float]:
        self.cached_calls.append(entry_id)
        assert embedding_content_hash
        assert text
        if self.fail:
            raise RuntimeError("queue boom")
        return list(self.vector)


class SearchDrainingEmbedder(FakeEmbedder):
    def embed_cached(self, entry_id: str, embedding_content_hash: str, text: str) -> list[float]:
        assert embedding_content_hash
        assert text
        self.cached_calls.append(entry_id)
        return _embedding(1.0)


def _embedding(first_value: float) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[0] = first_value
    return vector


@pytest.fixture()
def dense_fixture(ek_root: Path) -> tuple[EgoKnowledge, dict[str, str]]:
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    source = ek.ingest("source", source_payload(title="dense source"))
    target = ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="向量目标",
            search_terms=[
                "semantic-only-token",
                "dense search",
                "向量目标",
                "semantic target",
                "dense-target",
            ],
            body=(
                "dense search target body 用于语义检索集成测试，"
                "覆盖 dense route 和 queue drain。" + "x" * 20
            ),
        ),
    )
    other = ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="完全不同的对照条目",
            body=(
                "other dense body 用于语义检索集成测试的对照条目，确保负向向量排名靠后。" + "x" * 20
            ),
        ),
        conflict_policy="allow",
    )
    target_entry = ek.get(target.id)
    other_entry = ek.get(other.id)
    store_embedding(
        ek._registry,
        target.id,
        _embedding(1.0),
        compute_embedding_content_hash(target_entry),
        "test-rev",
    )
    store_embedding(
        ek._registry,
        other.id,
        _embedding(-1.0),
        compute_embedding_content_hash(other_entry),
        "test-rev",
    )
    for queue_file in (ek._data_root / "queue" / "dense_embed").glob("*.jsonl"):
        queue_file.unlink()
    return ek, {"source": source.id, "target": target.id, "other": other.id}


def test_dense_route_returns_results(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, ids = dense_fixture
    try:
        router = SearchRouter(ek._registry, data_root=ek._data_root, embedder=FakeEmbedder())
        results = router.search("没有词法命中的语义查询", limit=5)
        result = next(item for item in results if item.id == ids["target"])
        assert "dense" in result.backends
        assert result.score == pytest.approx(DENSE_WEIGHT)
    finally:
        ek.close()


def test_dense_backend_only_returns_dense_results(
    dense_fixture: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    ek, _ids = dense_fixture
    try:
        router = SearchRouter(ek._registry, data_root=ek._data_root, embedder=FakeEmbedder())
        results = router.search("semantic-only-token", backends=["dense"], limit=5)
        assert results
        assert all(result.backends == ["dense"] for result in results)
    finally:
        ek.close()


def test_dense_failure_fallback_to_four_routes(
    dense_fixture: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    ek, ids = dense_fixture
    try:
        router = SearchRouter(
            ek._registry,
            data_root=ek._data_root,
            embedder=FakeEmbedder(fail=True),
        )
        results = router.search("semantic-only-token", backends=["bm25", "dense"], limit=5)
        assert ids["target"] in [item.id for item in results]
        assert all("dense" not in item.backends for item in results)
        assert (ek._data_root / "logs" / "retrieval" / "dense-errors.jsonl").exists()
    finally:
        ek.close()


def test_exact_hit_skips_dense(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, ids = dense_fixture
    try:
        embedder = FakeEmbedder()
        router = SearchRouter(ek._registry, data_root=ek._data_root, embedder=embedder)
        results = router.search(ids["target"], limit=5)
        assert results[0].id == ids["target"]
        assert results[0].backends == ["exact"]
        assert embedder.batch_calls == 0
    finally:
        ek.close()


def test_delayed_embed_queue_not_blocking_ingest(ek_root: Path) -> None:
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    try:
        source = ek.ingest("source", source_payload(title="queued source"))
        entry = ek.ingest("concept", concept_payload(source.id, title="queued concept"))
        assert entry.id
        assert list((ek_root / "queue" / "dense_embed").glob("*.jsonl"))
    finally:
        ek.close()


def test_update_enqueues_dense_refresh(ek_root: Path) -> None:
    ek = EgoKnowledge(ek_root, dense_disabled=True)
    try:
        source = ek.ingest("source", source_payload(title="update queued source"))
        entry = ek.ingest("concept", concept_payload(source.id, title="update queued concept"))
        queue_dir = ek_root / "queue" / "dense_embed"
        for queue_file in queue_dir.glob("*.jsonl"):
            queue_file.unlink()

        ek.update(entry.id, {"tags": ["dense-refresh"]})

        queue_text = "\n".join(
            file.read_text(encoding="utf-8") for file in queue_dir.glob("*.jsonl")
        )
        assert entry.id in queue_text
    finally:
        ek.close()


def test_search_drains_queue_before_dense_route(ek_root: Path) -> None:
    embedder = SearchDrainingEmbedder()
    ek = EgoKnowledge(ek_root, dense_embedder=embedder)
    try:
        source = ek.ingest("source", source_payload(title="search drain source"))
        target = ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="search drain target",
                body=(
                    "semantic target body 用于验证搜索前自动消费延迟队列"
                    "并写入 dense embedding。" + "x" * 20
                ),
            ),
        )
        results = ek.search("没有词法命中的语义查询", limit=5)
        assert target.id in [item.id for item in results]
        assert embedder.cached_calls
    finally:
        ek.close()


def test_drain_processes_queue(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, ids = dense_fixture
    try:
        target = ids["target"]
        ek._registry.conn.execute("DELETE FROM dense_embeddings WHERE entry_id = ?", (target,))
        ek._registry.commit()
        enqueue(ek._data_root, target)
        stats = drain(ek._registry, FakeEmbedder(), ek._data_root, max_items=10)
        assert stats == {"processed": 1, "ok": 1, "skipped": 0, "failed": 0}
        assert not list((ek._data_root / "queue" / "dense_embed").glob("*.jsonl"))
    finally:
        ek.close()


def test_drain_tombstone_skipped(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, _ids = dense_fixture
    try:
        enqueue(ek._data_root, "missing-entry")
        stats = drain(ek._registry, FakeEmbedder(), ek._data_root, max_items=10)
        assert stats["processed"] == 1
        assert stats["skipped"] == 1
    finally:
        ek.close()


def test_drain_archived_skipped(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, ids = dense_fixture
    try:
        ek.update(ids["target"], {"status": "archived"})
        enqueue(ek._data_root, ids["target"])
        stats = drain(ek._registry, FakeEmbedder(), ek._data_root, max_items=10)

        assert stats["skipped"] >= 1
    finally:
        ek.close()


def test_drain_idempotent_same_hash(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, ids = dense_fixture
    try:
        enqueue(ek._data_root, ids["target"])
        embedder = FakeEmbedder()
        stats = drain(ek._registry, embedder, ek._data_root, max_items=10)
        assert stats["skipped"] == 1
        assert embedder.cached_calls == []
    finally:
        ek.close()


def test_drain_failed_item_remains_queued(
    dense_fixture: tuple[EgoKnowledge, dict[str, str]],
) -> None:
    ek, ids = dense_fixture
    try:
        target = ids["target"]
        ek._registry.conn.execute("DELETE FROM dense_embeddings WHERE entry_id = ?", (target,))
        ek._registry.commit()
        enqueue(ek._data_root, target)
        stats = drain(ek._registry, FakeEmbedder(fail=True), ek._data_root, max_items=10)
        assert stats["failed"] == 1
        assert list((ek._data_root / "queue" / "dense_embed").glob("*.jsonl"))
    finally:
        ek.close()


def test_queue_depth_hard_limit_rejects(ek_root: Path) -> None:
    queue_dir = ek_root / "queue" / "dense_embed"
    queue_dir.mkdir(parents=True)
    queue_file = queue_dir / "2026-01-01.jsonl"
    queue_file.write_text(
        "".join('{"entry_id":"x","queued_at":"now"}\n' for _ in range(QUEUE_DEPTH_HARD)),
        encoding="utf-8",
    )
    assert enqueue(ek_root, "overflow") is False


def test_queue_depth_warn_logs(ek_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ego_knowledge._dense_queue.QUEUE_DEPTH_WARN", 1)
    assert enqueue(ek_root, "one") is True
    assert enqueue(ek_root, "two") is True
    assert (ek_root / "logs" / "retrieval" / "dense-errors.jsonl").exists()


def test_fusion_weight_configurable(dense_fixture: tuple[EgoKnowledge, dict[str, str]]) -> None:
    ek, ids = dense_fixture
    try:
        router = SearchRouter(
            ek._registry,
            data_root=ek._data_root,
            embedder=FakeEmbedder(),
            dense_weight=2.5,
        )
        result = next(item for item in router.search("无词法", limit=5) if item.id == ids["target"])
        assert result.score == pytest.approx(2.5)
    finally:
        ek.close()


def test_cosine_similarity_correct() -> None:
    scores = _cosine_similarity_scores(_embedding(1.0), [_embedding(1.0), _embedding(-1.0)])
    assert scores == pytest.approx([1.0, -1.0])
