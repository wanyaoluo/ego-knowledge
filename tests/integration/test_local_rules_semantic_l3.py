from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ego_knowledge._dense_embedder import EMBEDDING_DIM
from ego_knowledge._dense_index import store_embedding
from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.maintenance_queue_store import list_queue
from tests.unit.support import concept_payload, note_payload, source_payload

if TYPE_CHECKING:
    from pathlib import Path

    from ego_knowledge.models import Entry


class FakeSemanticEmbedder:
    last_model_revision = "fake-revision"

    def __init__(self) -> None:
        self.cached_calls: list[tuple[str, str]] = []

    def embed_cached(
        self,
        entry_id: str,
        embedding_content_hash: str,
        text: str,
    ) -> list[float]:
        self.cached_calls.append((entry_id, embedding_content_hash))
        assert text.strip()
        return _vec(0.93, 0.36755958)


def _vec(first: float, second: float = 0.0) -> list[float]:
    return [float(first), float(second), *([0.0] * (EMBEDDING_DIM - 2))]


def _store_dense(ek: EgoKnowledge, entry: Entry) -> None:
    store_embedding(
        ek._registry,
        entry.id,
        _vec(1.0, 0.0),
        compute_embedding_content_hash(entry),
        "fake-revision",
    )


def _semantic_queue_rows(ek: EgoKnowledge) -> list[dict[str, object]]:
    return [
        row
        for row in list_queue(ek._registry, status="pending")
        if row["rule_id"] == "semantic_duplicate_candidate"
    ]


@pytest.fixture()
def semantic_ek(ek_root: Path) -> EgoKnowledge:
    return EgoKnowledge(ek_root, dense_embedder=FakeSemanticEmbedder())


def test_integration_real_ingest_triggers_semantic_finding(semantic_ek: EgoKnowledge) -> None:
    try:
        source = semantic_ek.ingest("source", source_payload(title="真实接入来源"))
        original = semantic_ek.ingest(
            "concept",
            concept_payload(source.id, title="真实接入原始火星概念"),
        )
        _store_dense(semantic_ek, original)

        duplicate = semantic_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="真实接入数据库候选",
                search_terms=["真实接入数据库候选", "candidate", "semantic", "候选", "alias-sem"],
            ),
        )

        rows = _semantic_queue_rows(semantic_ek)
        assert rows
        assert rows[0]["entry_id"] == duplicate.id
        assert original.id in str(rows[0]["message"])
    finally:
        semantic_ek.close()


def test_integration_promotion_triggers_semantic_finding(semantic_ek: EgoKnowledge) -> None:
    try:
        source = semantic_ek.ingest("source", source_payload(title="升格语义来源"))
        existing = semantic_ek.ingest(
            "dossier",
            {
                "title": "升格语义既有火星档案",
                "evidence_refs": [source.id],
                "search_terms": [
                    "升格语义既有火星档案",
                    "dossier",
                    "existing",
                    "既有",
                    "alias-dos",
                ],
                "tags": ["测试"],
                "body": "x" * 50,
            },
        )
        _store_dense(semantic_ek, existing)
        note = semantic_ek.ingest(
            "note",
            note_payload(
                source.id,
                title="升格语义新样本",
                search_terms=["升格语义新样本", "note", "nt", "新样本", "alias-note"],
            ),
        )

        promoted = semantic_ek.promote(note.id, target_kind="dossier")

        rows = _semantic_queue_rows(semantic_ek)
        assert rows
        assert rows[0]["entry_id"] == promoted.id
        assert existing.id in str(rows[0]["message"])
    finally:
        semantic_ek.close()


def test_integration_link_triggers_semantic_finding(semantic_ek: EgoKnowledge) -> None:
    try:
        source = semantic_ek.ingest("source", source_payload(title="链接语义来源"))
        left = semantic_ek.ingest(
            "concept",
            concept_payload(source.id, title="链接语义左火星概念"),
        )
        right = semantic_ek.ingest(
            "concept",
            concept_payload(
                source.id,
                title="链接语义右数据库概念",
                search_terms=["链接语义右数据库概念", "right", "semantic", "右侧", "alias-right"],
            ),
        )
        _store_dense(semantic_ek, left)

        semantic_ek.link(right.id, source.id, "related")

        rows = _semantic_queue_rows(semantic_ek)
        assert rows
        assert any(row["entry_id"] == right.id for row in rows)
        assert any(left.id in str(row["message"]) for row in rows)
    finally:
        semantic_ek.close()
