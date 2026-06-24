from __future__ import annotations

import datetime as dt

import pytest

from ego_knowledge.metrics import _freshness_score, compute_evidence_strength

from .support import concept_payload, note_payload, source_payload


def test_freshness_score_uses_true_365_day_half_life() -> None:
    today = dt.date.today()

    assert _freshness_score(None) == pytest.approx(0.5)
    assert _freshness_score(today + dt.timedelta(days=3)) == pytest.approx(0.5)
    assert _freshness_score(today - dt.timedelta(days=365)) == pytest.approx(0.5)


def test_compute_evidence_strength_collects_recursive_sources(fresh_ek) -> None:
    source_a = fresh_ek.ingest(
        "source",
        source_payload(title="Alpha Source", captured_at=dt.date.today()),
    )
    source_b = fresh_ek.ingest(
        "source",
        source_payload(
            title="Policy Manual",
            source_type="doc",
            source_url="knowledge://source-b.pdf",
            captured_at=dt.date.today() - dt.timedelta(days=365),
        ),
    )
    note = fresh_ek.ingest("note", note_payload(source_a.id, title="调研摘录"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_a.id,
            title="行动假设",
            evidence_refs=[note.id, source_b.id],
        ),
    )

    score = compute_evidence_strength(concept.id, fresh_ek._registry)

    assert score == pytest.approx(3.0)
