"""Integration tests for Phase 8.2 approval executor."""

from __future__ import annotations

import pytest

from ego_knowledge._approval_executor import approve, reject
from ego_knowledge._autonomous import ingest_autonomous
from ego_knowledge.doctor import Finding, Severity
from ego_knowledge.errors import ValidationError
from ego_knowledge.maintenance_queue_store import enqueue, list_queue
from tests.unit.support import concept_payload, note_payload, source_payload


def test_approve_rename(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="批准改名来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="批准改名概念"))
    result = ingest_autonomous(
        fresh_ek,
        "rename",
        {"id": concept.id, "new_slug": "approved-concept"},
        agent_id="agent-a",
    )

    approved = approve(fresh_ek, result.queue_id)
    row = list_queue(fresh_ek._registry, status="resolved")[0]
    assert approved == {"ok": True, "executed_op": "rename", "id": result.queue_id}
    assert row["id"] == result.queue_id
    assert fresh_ek.get(concept.id).slug == "approved-concept"


def test_approve_unlink_critical(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="批准关键来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="批准关键笔记"))
    decision = fresh_ek.ingest(
        "decision",
        {
            "title": "批准关键决策",
            "evidence_refs": [source.id],
            "decision_status": "active",
            "search_terms": ["批准关键决策", "decision", "dec", "关键", "alias-decision"],
            "tags": ["测试"],
            "body": "x" * 50,
        },
    )
    fresh_ek.link(decision.id, note.id, rel_type="supersedes")
    result = ingest_autonomous(
        fresh_ek,
        "unlink",
        {"source_id": decision.id, "target_id": note.id, "type": "supersedes"},
        agent_id="agent-a",
    )
    assert note.id in fresh_ek._registry.neighbors(decision.id, rel_type="supersedes")

    approve(fresh_ek, result.queue_id)

    assert note.id not in fresh_ek._registry.neighbors(decision.id, rel_type="supersedes")


def test_approve_domains_add_and_migrate(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="批准领域来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="批准领域概念"))
    add_result = ingest_autonomous(
        fresh_ek,
        "domains_add",
        {"name": "approved-domain"},
        agent_id="agent-a",
    )
    approve(fresh_ek, add_result.queue_id)
    migrate_result = ingest_autonomous(
        fresh_ek,
        "domains_migrate",
        {"entries": [concept.id], "target_domain": "approved-domain"},
        agent_id="agent-a",
    )
    approve(fresh_ek, migrate_result.queue_id)

    assert fresh_ek.get(concept.id).domain == "approved-domain"


def test_approve_link_guardrailed(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="批准 link 来源"))
    targets = [
        fresh_ek.ingest(
            "note",
            note_payload(source.id, title=f"批准 link 目标 {idx}"),
            conflict_policy="allow",
        )
        for idx in range(11)
    ]
    for target in targets:
        result = ingest_autonomous(
            fresh_ek,
            "link",
            {"source_id": source.id, "target_id": target.id, "type": "related"},
            agent_id="agent-a",
        )
    assert result.action == "queued_for_approval"

    approve(fresh_ek, result.queue_id)

    assert targets[-1].id in fresh_ek._registry.neighbors(source.id, rel_type="related")


def test_approve_invalid_origin_rejects(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="人工来源"))
    qid = enqueue(
        fresh_ek._registry,
        Finding("human_rule", Severity.MEDIUM, source.id, None, "human"),
    )
    with pytest.raises(ValidationError, match="只能处理 AI 提议"):
        approve(fresh_ek, qid)


def test_approve_already_resolved_idempotent(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="幂等批准来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="幂等批准概念"))
    result = ingest_autonomous(
        fresh_ek,
        "rename",
        {"id": concept.id, "new_slug": "idempotent-concept"},
        agent_id="agent-a",
    )
    approve(fresh_ek, result.queue_id)
    assert approve(fresh_ek, result.queue_id) == {
        "ok": False,
        "reason": "already resolved/dismissed",
    }


def test_reject_with_reason(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="拒绝来源"))
    result = ingest_autonomous(
        fresh_ek,
        "rename",
        {"id": source.id, "new_slug": "rejected-source"},
        agent_id="agent-a",
    )
    rejected = reject(fresh_ek, result.queue_id, reason="不需要")
    assert rejected["ok"] is True
    assert list_queue(fresh_ek._registry, status="dismissed")[0]["id"] == result.queue_id


def test_approve_bad_payload_keeps_pending(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="坏 payload 来源"))
    qid = enqueue(
        fresh_ek._registry,
        Finding("ai_proposed_rename", Severity.MEDIUM, source.id, None, "bad"),
        origin="ai_proposed",
        proposed_op="rename",
        proposed_payload={"id": source.id, "new_slug": "bad"},
        agent_id="agent-a",
    )
    fresh_ek._registry.conn.execute(
        "UPDATE maintenance_queue SET proposed_payload_json = ? WHERE id = ?",
        ("{bad-json", qid),
    )
    fresh_ek._registry.commit()

    with pytest.raises(ValidationError, match="不是合法 JSON"):
        approve(fresh_ek, qid)
    assert list_queue(fresh_ek._registry, status="pending")[0]["id"] == qid
