"""Tests for L4 structure diagnose rules.

Removed test cases (rules merged / superseded):
- test_structure_note_swamp_*         → deleted (identical to decay_note_stagnant, also removed)
- test_structure_orphan_decision_*    → covered by L3 redline_9_source_reachability
- test_structure_view_as_truth_*      → covered by L3 redline_10_view_as_evidence
"""

from __future__ import annotations

from ego_knowledge._diagnose_rules import structure
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.models import RelationType

from .support import concept_payload, source_payload


def _set_metrics(ek: EgoKnowledge, entry_id: str, **metrics: float) -> None:
    registry = ek._registry
    assignments = ", ".join(f"{key} = ?" for key in metrics)
    registry.conn.execute(
        f"UPDATE entry_metrics SET {assignments} WHERE entry_id = ?",
        (*metrics.values(), entry_id),
    )
    registry.commit()


def _relation(target: str, rel_type: str) -> dict[str, str]:
    return {"target": target, "type": rel_type, "source": "confirmed"}


def test_structure_fossil_concept_hit(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="化石来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="化石概念"))
    _set_metrics(fresh_ek, concept.id, evidence_strength=0.5, action_relevance=3.0)

    findings = structure.rule_structure_fossil_concept(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [concept.id]


def test_structure_fossil_concept_miss_with_strong_evidence(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="强证据来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="强证据概念"))
    _set_metrics(fresh_ek, concept.id, evidence_strength=1.0, action_relevance=3.0)

    assert structure.rule_structure_fossil_concept(fresh_ek._registry) == []


def test_structure_monoculture_evidence_hit(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", source_payload(title="单栽来源A"))
    source_b = fresh_ek.ingest("source", source_payload(title="单栽来源B"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source_a.id, title="单栽概念", evidence_refs=[source_a.id, source_b.id]),
    )

    findings = structure.rule_structure_monoculture_evidence(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [concept.id]


def test_structure_monoculture_evidence_miss_with_diverse_sources(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", source_payload(title="多样来源A"))
    source_b = fresh_ek.ingest("source", source_payload(title="多样来源B", source_type="doc"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source_a.id, title="多样概念", evidence_refs=[source_a.id, source_b.id]),
    )

    assert structure.rule_structure_monoculture_evidence(fresh_ek._registry) == []


def test_structure_supersedes_cycle_hit(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="循环来源"))
    concept_a = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="循环源头Node",
            search_terms=["循环源头Node", "concept-cycle-a", "src", "来源", "alias-cycle-a"],
        ),
    )
    concept_b = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="循环末端Edge",
            search_terms=["循环末端Edge", "concept-cycle-b", "src", "来源", "alias-cycle-b"],
            relations=[_relation(concept_a.id, RelationType.SUPERSEDES.value)],
        ),
    )
    fresh_ek.update(
        concept_a.id,
        {"relations": [_relation(concept_b.id, RelationType.SUPERSEDES.value)]},
    )

    target_ids = {
        finding.target_id
        for finding in structure.rule_structure_supersedes_cycle(fresh_ek._registry)
    }

    assert target_ids == {concept_a.id, concept_b.id}


def test_structure_supersedes_cycle_miss_for_chain(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="链式来源"))
    concept_b = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="链式先驱Predecessor",
            search_terms=["链式先驱Predecessor", "concept-chain-b", "src", "来源", "alias-chain-b"],
        ),
    )
    fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="链式继承Successor",
            search_terms=["链式继承Successor", "concept-chain-a", "src", "来源", "alias-chain-a"],
            relations=[_relation(concept_b.id, RelationType.SUPERSEDES.value)],
        ),
    )

    assert structure.rule_structure_supersedes_cycle(fresh_ek._registry) == []
