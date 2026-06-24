from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner

from ego_knowledge._dense_embedder import EMBEDDING_DIM, EmbedResult
from ego_knowledge._dense_index import (
    _build_embed_text,
    load_all_embeddings,
    rebuild_all,
    stale_entry_ids,
    store_embedding,
)
from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge.cli import main
from ego_knowledge.core import EgoKnowledge
from tests.unit.support import concept_payload, source_payload


class FakeEmbedder:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[list[str]] = []

    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        self.calls.append(list(texts))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise RuntimeError("api busy")
        return EmbedResult(
            embeddings=[
                _embedding(float(len(self.calls) + index)) for index, _ in enumerate(texts)
            ],
            model_revision="fake-revision",
            tokens_used=len(texts),
        )


def _embedding(seed: float = 1.0) -> list[float]:
    return [float(seed)] * EMBEDDING_DIM


@pytest.fixture()
def dense_ek(ek_root: Path) -> EgoKnowledge:
    ek = EgoKnowledge(ek_root)
    source = ek.ingest("source", source_payload(title="dense 来源"))
    ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="dense 概念",
            body=(
                "语义索引正文用于 dense index 集成测试，"
                "覆盖向量存储、重建、续跑与状态更新。" + "x" * 20
            ),
            search_terms=["dense", "语义索引", "概念样例", "semantic", "向量检索"],
        ),
    )
    return ek


def test_store_and_load_embedding(dense_ek: EgoKnowledge) -> None:
    try:
        entry = dense_ek._registry.all_entries()[0]
        entry_hash = compute_embedding_content_hash(entry)
        store_embedding(dense_ek._registry, entry.id, _embedding(0.25), entry_hash, "rev-1")

        loaded = load_all_embeddings(dense_ek._registry)

        assert list(loaded) == [entry.id]
        assert len(loaded[entry.id]) == EMBEDDING_DIM
        assert loaded[entry.id][0] == pytest.approx(0.25)
    finally:
        dense_ek.close()


def test_embedding_dim_mismatch_raises(dense_ek: EgoKnowledge) -> None:
    try:
        entry = dense_ek._registry.all_entries()[0]
        with pytest.raises(ValueError, match="维度错"):
            store_embedding(dense_ek._registry, entry.id, [1.0, 2.0], "hash", "rev-1")
    finally:
        dense_ek.close()


def test_stale_detection(dense_ek: EgoKnowledge) -> None:
    try:
        entries = dense_ek._registry.all_entries()
        first = entries[0]
        store_embedding(
            dense_ek._registry,
            first.id,
            _embedding(),
            compute_embedding_content_hash(first),
            "rev-1",
        )

        stale = stale_entry_ids(dense_ek._registry)

        assert first.id not in stale
        assert {entry.id for entry in entries[1:]} <= set(stale)
    finally:
        dense_ek.close()


def test_stale_detection_no_content_hash_in_entries(dense_ek: EgoKnowledge) -> None:
    try:
        columns = {
            row["name"] for row in dense_ek._registry.conn.execute("PRAGMA table_info(entries)")
        }
        assert "content_hash" not in columns
        assert stale_entry_ids(dense_ek._registry)
    finally:
        dense_ek.close()


def test_stale_detection_skips_archived(dense_ek: EgoKnowledge) -> None:
    try:
        archived = dense_ek._registry.all_entries()[0]
        dense_ek.update(archived.id, {"status": "archived"})

        assert archived.id not in stale_entry_ids(dense_ek._registry)
    finally:
        dense_ek.close()


def test_rebuild_all_batches(dense_ek: EgoKnowledge, monkeypatch: pytest.MonkeyPatch) -> None:
    try:
        monkeypatch.setattr("ego_knowledge._dense_index.time.sleep", lambda seconds: None)
        embedder = FakeEmbedder()

        stats = rebuild_all(dense_ek._registry, embedder, batch_size=1)

        assert stats == {"total": 2, "ok": 2, "failed": 0, "skipped": 0}
        assert len(embedder.calls) == 2
        assert len(load_all_embeddings(dense_ek._registry)) == 2
    finally:
        dense_ek.close()


def test_rebuild_continues_after_failure(
    dense_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    try:
        monkeypatch.setattr("ego_knowledge._dense_index.time.sleep", lambda seconds: None)
        progress_log = tmp_path / "progress.jsonl"
        embedder = FakeEmbedder(fail_on_call=1)

        stats = rebuild_all(
            dense_ek._registry,
            embedder,
            progress_log=progress_log,
            batch_size=1,
        )

        assert stats == {"total": 2, "ok": 1, "failed": 1, "skipped": 0}
        records = [
            json.loads(line) for line in progress_log.read_text(encoding="utf-8").splitlines()
        ]
        assert [record["status"] for record in records] == ["failed", "ok"]
        assert len(load_all_embeddings(dense_ek._registry)) == 1
    finally:
        dense_ek.close()


def test_rebuild_resume_from_checkpoint(
    dense_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    try:
        monkeypatch.setattr("ego_knowledge._dense_index.time.sleep", lambda seconds: None)
        target_ids = dense_ek._registry.all_entry_ids()
        first_id = target_ids[0]
        progress_log = tmp_path / "progress.jsonl"
        progress_log.write_text(
            json.dumps({"entry_id": first_id, "status": "ok"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        embedder = FakeEmbedder()

        stats = rebuild_all(
            dense_ek._registry,
            embedder,
            progress_log=progress_log,
            resume=True,
            batch_size=16,
        )

        assert stats == {"total": 1, "ok": 1, "failed": 0, "skipped": 0}
        assert len(embedder.calls) == 1
        assert target_ids[1] not in stale_entry_ids(dense_ek._registry)
    finally:
        dense_ek.close()


def test_semantic_index_meta_updated_with_correct_schema(
    dense_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        monkeypatch.setattr("ego_knowledge._dense_index.time.sleep", lambda seconds: None)
        rebuild_all(dense_ek._registry, FakeEmbedder())

        row = dense_ek._registry.conn.execute(
            "SELECT * FROM semantic_index_meta WHERE index_name = 'dense_bge_m3'"
        ).fetchone()

        assert row is not None
        assert set(row.keys()) == {
            "index_name",
            "model_id",
            "model_revision",
            "index_schema_version",
            "indexed_at",
        }
        assert row["model_id"] == "bge-m3"
        assert row["index_schema_version"] == "2.2"
    finally:
        dense_ek.close()


def test_schema_less_than_2_2_raises(dense_ek: EgoKnowledge) -> None:
    try:
        dense_ek._registry.conn.execute(
            "UPDATE registry_meta SET value = '2.1' WHERE key = 'schema_version'"
        )
        dense_ek._registry.commit()

        with pytest.raises(RuntimeError) as exc_info:
            stale_entry_ids(dense_ek._registry)
        assert str(exc_info.value) == (
            "dense 索引需要 schema 2.2+；历史一次性迁移脚本已退役；"
            "请从备份恢复到匹配版本或重建 2.3 数据根后再生成索引。"
        )
    finally:
        dense_ek.close()


def test_schema_version_comparison_uses_semantic_order(
    dense_ek: EgoKnowledge,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        dense_ek._registry.conn.execute(
            "UPDATE registry_meta SET value = '2.10' WHERE key = 'schema_version'"
        )
        dense_ek._registry.commit()
        monkeypatch.setattr("ego_knowledge._dense_index.REGISTRY_SCHEMA_VERSION", "2.10")

        assert stale_entry_ids(dense_ek._registry)
    finally:
        dense_ek.close()


def test_dense_schema_missing_tables_raises_exact_message(dense_ek: EgoKnowledge) -> None:
    try:
        dense_ek._registry.conn.execute("DROP TABLE dense_embeddings")
        dense_ek._registry.commit()

        with pytest.raises(RuntimeError) as exc_info:
            stale_entry_ids(dense_ek._registry)
        assert str(exc_info.value) == "dense 索引表缺失；请重建 2.3 数据根后再生成索引。"
    finally:
        dense_ek.close()


def test_build_embed_text_uses_title_tags_alias_terms_and_body(dense_ek: EgoKnowledge) -> None:
    try:
        entry = dense_ek._registry.all_entries()[0]
        text = _build_embed_text(entry)

        assert entry.title in text
        assert (entry.body or "")[:20] in text
    finally:
        dense_ek.close()


def test_rebuild_dense_index_help_lists_stale_and_resume() -> None:
    result = CliRunner().invoke(main, ["rebuild-dense-index", "--help"])

    assert result.exit_code == 0
    assert "--stale" in result.output
    assert "--resume" in result.output
