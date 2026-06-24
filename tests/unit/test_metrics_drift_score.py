from __future__ import annotations

import pytest

from ego_knowledge.metrics import compute_drift_score

from .support import note_payload, source_payload


def test_compute_drift_score_counts_superseded_sources(fresh_ek) -> None:
    source_old = fresh_ek.ingest("source", source_payload(title="旧来源"))
    source_new = fresh_ek.ingest(
        "source",
        source_payload(title="新来源", source_url="https://example.com/new-source"),
    )
    note = fresh_ek.ingest("note", note_payload(source_old.id, title="漂移笔记"))

    assert compute_drift_score(note.id, fresh_ek._registry) == pytest.approx(0.0)

    fresh_ek.link(source_new.id, source_old.id, "supersedes")

    assert compute_drift_score(note.id, fresh_ek._registry) == pytest.approx(1.0)
