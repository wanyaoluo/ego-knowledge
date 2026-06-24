"""Tests for L4 decay diagnose rules.

Removed test cases (rules merged / superseded):
- test_decay_note_stagnant_* → deleted; identical to structure_note_swamp,
  also removed.
- test_decay_concept_internal_split_* → push_internal_split; identical logic,
  better push context.
"""

from __future__ import annotations

from ego_knowledge._diagnose_rules import decay
from ego_knowledge.core import EgoKnowledge

from .support import concept_payload, dossier_payload, source_payload


def _set_metrics(ek: EgoKnowledge, entry_id: str, **metrics: float) -> None:
    registry = ek._registry
    assignments = ", ".join(f"{key} = ?" for key in metrics)
    registry.conn.execute(
        f"UPDATE entry_metrics SET {assignments} WHERE entry_id = ?",
        (*metrics.values(), entry_id),
    )
    registry.commit()


def test_decay_source_context_hit(fresh_ek) -> None:
    old_source = fresh_ek.ingest("source", source_payload(title="旧上下文来源"))
    new_source = fresh_ek.ingest("source", source_payload(title="新上下文来源"))
    fresh_ek.link(new_source.id, old_source.id, "supersedes")

    findings = decay.rule_decay_source_context(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [old_source.id]


def test_decay_source_context_miss(fresh_ek) -> None:
    fresh_ek.ingest("source", source_payload(title="稳定上下文来源"))

    assert decay.rule_decay_source_context(fresh_ek._registry) == []


def test_decay_dossier_outdated_hit(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="漂移档案来源"))
    dossier = fresh_ek.ingest("dossier", dossier_payload(source.id, title="漂移档案"))
    _set_metrics(fresh_ek, dossier.id, drift_score=0.5)

    findings = decay.rule_decay_dossier_outdated(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [dossier.id]


def test_decay_dossier_outdated_miss(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="稳定档案来源"))
    fresh_ek.ingest("dossier", dossier_payload(source.id, title="稳定档案"))

    assert decay.rule_decay_dossier_outdated(fresh_ek._registry) == []


def test_decay_concept_evidence_singular_hit(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="单证据来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="单证据概念"))

    findings = decay.rule_decay_concept_evidence_singular(fresh_ek._registry)

    assert [finding.target_id for finding in findings] == [concept.id]


def test_decay_concept_evidence_singular_miss_with_diversity(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", source_payload(title="多证据来源A"))
    source_b = fresh_ek.ingest(
        "source",
        source_payload(title="多证据来源B", source_type="doc"),
    )
    fresh_ek.ingest(
        "concept",
        concept_payload(source_a.id, title="多证据概念", evidence_refs=[source_a.id, source_b.id]),
    )

    assert decay.rule_decay_concept_evidence_singular(fresh_ek._registry) == []
