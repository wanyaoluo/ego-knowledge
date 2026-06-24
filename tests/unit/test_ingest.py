from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge.errors import ConflictError, ValidationError

from .support import absolute_entry_path, concept_payload, source_payload


def test_ingest_source_roundtrip(fresh_ek, ek_root: Path) -> None:
    entry = fresh_ek.ingest(
        "source",
        source_payload(
            title="烟测",
            search_terms=["烟测", "smoke", "ex", "烟", "alias"],
        ),
    )

    assert entry.id.startswith("ek_src_")
    assert entry.file_path is not None
    assert absolute_entry_path(ek_root, entry.file_path).exists()
    stored = fresh_ek.get(entry.id)
    assert stored.title == "烟测"
    assert stored.metrics is not None
    assert set(stored.metrics) == {
        "evidence_strength",
        "drift_score",
        "compression_ratio",
        "action_relevance",
        "retrieval_heat",
    }


def test_ingest_strict_conflict_raises(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload())
    fresh_ek.ingest("concept", concept_payload(source.id, title="单一真源"))

    with pytest.raises(ConflictError):
        fresh_ek.ingest("concept", concept_payload(source.id, title="唯一真源"))


def test_ingest_alias_conflict_raises(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="来源A"))
    fresh_ek.ingest("concept", concept_payload(source.id, title="关系网络", aliases=["知识图谱"]))

    with pytest.raises(ConflictError):
        fresh_ek.ingest("concept", concept_payload(source.id, title="知识图谱"))


def test_ingest_search_terms_requires_alias_bucket(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="三桶未覆盖"):
        fresh_ek.ingest(
            "source",
            source_payload(
                title="别称校验",
                search_terms=["别称校验", "别称", "校验", "误写", "错写"],
            ),
        )
