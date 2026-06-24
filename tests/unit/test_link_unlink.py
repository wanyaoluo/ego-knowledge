from __future__ import annotations

import pytest

from ego_knowledge.errors import ValidationError

from .support import concept_payload, source_payload


def test_link_related_and_unlink_roundtrip(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="关系来源"))
    left = fresh_ek.ingest("concept", concept_payload(source.id, title="左概念"))
    right = fresh_ek.ingest("concept", concept_payload(source.id, title="右概念"))

    relation = fresh_ek.link(left.id, right.id, rel_type="related")
    neighbors = fresh_ek.related_basic(left.id)

    assert relation.target == right.id
    assert right.id in [entry.id for entry in neighbors]

    fresh_ek.unlink(left.id, right.id)
    assert right.id not in [entry.id for entry in fresh_ek.related_basic(left.id)]


def test_link_rejects_kind_specific_field(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="关系来源2"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="概念2"))

    with pytest.raises(ValidationError, match="update\\(\\)"):
        fresh_ek.link(concept.id, source.id, rel_type="evidence_refs")
