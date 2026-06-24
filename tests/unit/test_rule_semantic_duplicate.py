from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ego_knowledge._dense_embedder import EMBEDDING_DIM
from ego_knowledge._dense_index import store_embedding
from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge._validation import collect_semantic_candidates
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.local_rules import check_local_rules
from tests.unit.support import concept_payload, source_payload

if TYPE_CHECKING:
    from ego_knowledge.models import Entry


class FakeCachedEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[tuple[str, str]] = []

    def embed_cached(
        self,
        entry_id: str,
        embedding_content_hash: str,
        text: str,
    ) -> list[float]:
        self.calls.append((entry_id, embedding_content_hash))
        assert text.strip()
        return self.vectors[entry_id]


def _vec(first: float, second: float = 0.0) -> list[float]:
    return [float(first), float(second), *([0.0] * (EMBEDDING_DIM - 2))]


def _store(ek: EgoKnowledge, entry: Entry, vector: list[float]) -> None:
    registry = ek._registry
    store_embedding(
        registry,
        entry.id,
        vector,
        compute_embedding_content_hash(entry),
        "fake-revision",
    )


@pytest.fixture()
def semantic_entries(fresh_ek: EgoKnowledge) -> dict[str, Entry]:
    source = fresh_ek.ingest("source", source_payload(title="语义重复来源"))
    original = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="火星地貌原始概念"),
    )
    duplicate = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="数据库隔离候选概念",
            search_terms=["数据库隔离候选概念", "candidate", "dup-cand", "候选", "alias-cand"],
        ),
    )
    other_kind = fresh_ek.ingest(
        "note",
        {
            "title": "语义重复其它类型",
            "source_refs": [source.id],
            "search_terms": ["语义重复其它类型", "note", "nt", "其它", "alias-note"],
            "tags": ["测试"],
            "body": "x" * 50,
        },
    )
    return {
        "source": source,
        "original": original,
        "duplicate": duplicate,
        "other_kind": other_kind,
    }


def test_semantic_duplicate_above_threshold(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    original = semantic_entries["original"]
    duplicate = semantic_entries["duplicate"]
    _store(fresh_ek, original, _vec(1.0, 0.0))
    embedder = FakeCachedEmbedder({duplicate.id: _vec(0.93, 0.36755958)})

    findings = check_local_rules({duplicate.id}, fresh_ek._registry, embedder=embedder)

    semantic_findings = [
        finding for finding in findings if finding.rule_id == "semantic_duplicate_candidate"
    ]
    assert len(semantic_findings) == 1
    assert original.id in semantic_findings[0].message
    assert "≥0.92" in semantic_findings[0].message


def test_semantic_duplicate_below_threshold_skipped(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    original = semantic_entries["original"]
    duplicate = semantic_entries["duplicate"]
    _store(fresh_ek, original, _vec(1.0, 0.0))
    embedder = FakeCachedEmbedder({duplicate.id: _vec(0.91, 0.414608)})

    findings = check_local_rules({duplicate.id}, fresh_ek._registry, embedder=embedder)

    assert [
        finding for finding in findings if finding.rule_id == "semantic_duplicate_candidate"
    ] == []


def test_same_kind_only(fresh_ek: EgoKnowledge, semantic_entries: dict[str, Entry]) -> None:
    duplicate = semantic_entries["duplicate"]
    other_kind = semantic_entries["other_kind"]
    _store(fresh_ek, other_kind, _vec(1.0, 0.0))
    embedder = FakeCachedEmbedder({duplicate.id: _vec(1.0, 0.0)})

    assert (
        collect_semantic_candidates(
            fresh_ek._registry,
            embedder,
            duplicate,
            ignore_ids={duplicate.id},
            threshold=0.92,
        )
        == []
    )


def test_ignore_self(fresh_ek: EgoKnowledge, semantic_entries: dict[str, Entry]) -> None:
    duplicate = semantic_entries["duplicate"]
    _store(fresh_ek, duplicate, _vec(1.0, 0.0))
    embedder = FakeCachedEmbedder({duplicate.id: _vec(1.0, 0.0)})

    assert (
        collect_semantic_candidates(
            fresh_ek._registry,
            embedder,
            duplicate,
            ignore_ids={duplicate.id},
            threshold=0.92,
        )
        == []
    )


def test_collect_semantic_candidates_empty_text_skips(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    duplicate = semantic_entries["duplicate"]
    duplicate.title = ""
    duplicate.tags = []
    duplicate.aliases = []
    duplicate.search_terms = []
    duplicate.body = ""

    assert (
        collect_semantic_candidates(
            fresh_ek._registry,
            FakeCachedEmbedder({}),
            duplicate,
            ignore_ids={duplicate.id},
            threshold=0.92,
        )
        == []
    )


def test_collect_semantic_candidates_zero_vector_skips(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    original = semantic_entries["original"]
    duplicate = semantic_entries["duplicate"]
    _store(fresh_ek, original, _vec(1.0, 0.0))
    embedder = FakeCachedEmbedder({duplicate.id: _vec(0.0, 0.0)})

    assert (
        collect_semantic_candidates(
            fresh_ek._registry,
            embedder,
            duplicate,
            ignore_ids={duplicate.id},
            threshold=0.92,
        )
        == []
    )


def test_collect_semantic_candidates_dimension_mismatch_raises(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    original = semantic_entries["original"]
    duplicate = semantic_entries["duplicate"]
    _store(fresh_ek, original, _vec(1.0, 0.0))
    embedder = FakeCachedEmbedder({duplicate.id: [1.0, 0.0]})

    with pytest.raises(ValueError, match="维度不一致"):
        collect_semantic_candidates(
            fresh_ek._registry,
            embedder,
            duplicate,
            ignore_ids={duplicate.id},
            threshold=0.92,
        )


def test_embedder_missing_skips_rule(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    duplicate = semantic_entries["duplicate"]

    findings = check_local_rules({duplicate.id}, fresh_ek._registry, embedder=None)

    assert [
        finding for finding in findings if finding.rule_id == "semantic_duplicate_candidate"
    ] == []


def test_threshold_configurable_via_meta(
    fresh_ek: EgoKnowledge,
    semantic_entries: dict[str, Entry],
) -> None:
    original = semantic_entries["original"]
    duplicate = semantic_entries["duplicate"]
    _store(fresh_ek, original, _vec(1.0, 0.0))
    fresh_ek._registry.conn.execute(
        "INSERT INTO registry_meta(key, value) VALUES(?, ?)",
        ("semantic_duplicate_threshold", "0.95"),
    )
    fresh_ek._registry.commit()
    embedder = FakeCachedEmbedder({duplicate.id: _vec(0.93, 0.36755958)})

    findings = check_local_rules({duplicate.id}, fresh_ek._registry, embedder=embedder)

    assert [
        finding for finding in findings if finding.rule_id == "semantic_duplicate_candidate"
    ] == []
