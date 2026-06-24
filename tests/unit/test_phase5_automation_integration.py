from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from ego_knowledge.cli import main

from .support import (
    concept_payload,
    decision_payload,
    dossier_payload,
    note_payload,
    source_payload,
)


def test_phase5_cli_chain_smoke(ek_root: Path, fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", source_payload(title="研究文章"))
    internal_url = "knowledge://source-b.pdf"
    source_b = fresh_ek.ingest(
        "source",
        source_payload(title="会议手册", source_type="doc", source_url=internal_url),
    )
    note_a = fresh_ek.ingest("note", note_payload(source_a.id, title="调研摘录"))
    fresh_ek.ingest("note", note_payload(source_b.id, title="会议纪要"))
    dossier = fresh_ek.ingest("dossier", dossier_payload(source_a.id, title="档案 A"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source_a.id, title="概念 A", evidence_refs=[source_a.id, note_a.id]),
    )
    decision = fresh_ek.ingest("decision", decision_payload(concept.id, title="决策 A"))
    fresh_ek.link(concept.id, decision.id, "applied_in")

    runner = CliRunner()
    env = {"EK_DATA_ROOT": str(ek_root)}

    doctor_result = runner.invoke(main, ["doctor"], env=env)
    diagnose_result = runner.invoke(main, ["diagnose"], env=env)
    stats_result = runner.invoke(main, ["stats", "--by", "kind"], env=env)
    review_result = runner.invoke(main, ["review", "--overdue"], env=env)

    assert doctor_result.exit_code == 0
    assert diagnose_result.exit_code == 0
    assert stats_result.exit_code == 0
    assert review_result.exit_code == 0

    doctor_payload = json.loads(doctor_result.output)
    diagnose_payload = json.loads(diagnose_result.output)
    stats_payload = json.loads(stats_result.output)
    review_payload = json.loads(review_result.output)

    assert len(doctor_payload["checked_rules"]) == 17
    assert diagnose_payload["checked_rules"][:2] == [
        "redline_9_source_reachability",
        "redline_10_view_as_evidence",
    ]
    assert len(diagnose_payload["checked_rules"]) == 16
    assert stats_payload["counts"]["concept"] >= 1
    assert isinstance(review_payload, list)
    assert dossier.id.startswith("ek_dos_")
