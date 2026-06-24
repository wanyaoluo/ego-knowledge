from __future__ import annotations

from ego_knowledge.core import EgoKnowledge

from .support import concept_payload, source_payload


def test_related_traverses_bfs_with_depth_and_type(fresh_ek: EgoKnowledge) -> None:
    source = fresh_ek.ingest("source", source_payload(title="图遍历来源"))
    first = fresh_ek.ingest("concept", concept_payload(source.id, title="第一层"))
    second = fresh_ek.ingest("concept", concept_payload(source.id, title="第二层"))
    third = fresh_ek.ingest("concept", concept_payload(source.id, title="第三层"))

    fresh_ek.link(first.id, second.id, rel_type="related")
    fresh_ek.link(second.id, third.id, rel_type="related")
    fresh_ek.link(first.id, source.id, rel_type="evidence_for")

    one_hop = fresh_ek.related(first.id, depth=1, rel_type="related")
    two_hop = fresh_ek.related(first.id, depth=2, rel_type="related")

    assert [entry.id for entry in one_hop] == [second.id]
    assert [entry.id for entry in two_hop] == [second.id, third.id]
