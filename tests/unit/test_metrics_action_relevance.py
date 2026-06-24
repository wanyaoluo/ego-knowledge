from __future__ import annotations

import pytest

from ego_knowledge.metrics import compute_action_relevance

from .support import concept_payload, decision_payload, dossier_payload, source_payload


def test_compute_action_relevance_for_concept(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="概念来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="核心概念"))
    dependent = fresh_ek.ingest("concept", concept_payload(source.id, title="执行协议"))
    fresh_ek.ingest("decision", decision_payload(concept.id, title="基于概念的决策"))

    fresh_ek.link(dependent.id, concept.id, "depends_on")

    assert compute_action_relevance(concept.id, fresh_ek._registry) == pytest.approx(2.0)


def test_compute_action_relevance_for_dossier(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="档案来源"))
    dossier = fresh_ek.ingest("dossier", dossier_payload(source.id, title="档案"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="引用档案的概念", evidence_refs=[dossier.id]),
    )
    decision = fresh_ek.ingest("decision", decision_payload(source.id, title="关联档案的决策"))

    fresh_ek.link(decision.id, dossier.id, "related")

    assert concept.id.startswith("ek_con_")
    assert compute_action_relevance(dossier.id, fresh_ek._registry) == pytest.approx(2.0)
