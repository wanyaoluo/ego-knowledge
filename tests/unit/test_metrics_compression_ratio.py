from __future__ import annotations

import pytest

from ego_knowledge.metrics import compute_compression_ratio

from .support import concept_payload, note_payload, source_payload


def test_compute_compression_ratio_counts_derived_from_edges(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="上游笔记"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="概念"))

    fresh_ek.link(concept.id, note.id, "derived_from")
    fresh_ek.link(concept.id, source.id, "derived_from")

    assert compute_compression_ratio(concept.id, fresh_ek._registry) == pytest.approx(2.0)
