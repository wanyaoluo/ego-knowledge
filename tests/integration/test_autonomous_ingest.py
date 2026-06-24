"""Integration tests for Phase 8.2 autonomous ingest orchestration."""

from __future__ import annotations

import pytest

from ego_knowledge._autonomous import ingest_autonomous
from ego_knowledge.errors import ValidationError
from ego_knowledge.maintenance_queue_store import list_queue
from tests.unit.support import note_payload, source_payload


def _source(ek, title: str = "自主来源"):
    return ek.ingest("source", source_payload(title=title))


def test_ai_auto_ingest_source(fresh_ek) -> None:
    result = ingest_autonomous(
        fresh_ek,
        "ingest",
        {"kind": "source", "payload": source_payload(title="AI 来源")},
        agent_id="ingest-bot",
    )
    assert result.action == "executed"
    assert result.entry_id is not None
    assert fresh_ek.get(result.entry_id).title == "AI 来源"


def test_ai_auto_promote(fresh_ek) -> None:
    source = _source(fresh_ek, "升格来源")
    note = fresh_ek.ingest("note", note_payload(source.id, title="升格笔记"))
    result = ingest_autonomous(
        fresh_ek,
        "promote",
        {"id": note.id, "target_kind": "concept", "freshness": "watch"},
        agent_id="agent-a",
    )
    assert result.action == "executed"
    assert fresh_ek.get(result.entry_id).kind.value == "concept"


def test_ai_auto_touch_update(fresh_ek) -> None:
    source = _source(fresh_ek, "更新来源")
    updated = ingest_autonomous(
        fresh_ek,
        "update",
        {"id": source.id, "changes": {"tags": ["ai"]}},
        agent_id="agent-a",
    )
    touched = ingest_autonomous(fresh_ek, "touch", {"id": source.id}, agent_id="agent-a")
    assert updated.action == "executed"
    assert touched.entry_id == source.id


def test_ai_auto_update_body_rejects_unauthorized_agent(fresh_ek) -> None:
    source = _source(fresh_ek, "body 护栏来源")
    note = fresh_ek.ingest("note", note_payload(source.id, title="body 护栏笔记"))
    result = ingest_autonomous(
        fresh_ek,
        "update",
        {"id": note.id, "changes": {"body": "y" * 50}},
        agent_id="agent-a",
    )
    assert result.action == "blocked"
    assert result.entry_id is None
    assert result.findings[0].rule_id == "autonomous_body_update_blocked"
    assert fresh_ek.get(note.id).body == "x" * 50 + "\n"


def test_ai_auto_update_body_allows_ops_manage_knowledge(fresh_ek) -> None:
    source = _source(fresh_ek, "body 授权来源")
    note = fresh_ek.ingest("note", note_payload(source.id, title="body 授权笔记"))
    body = "z" * 50
    result = ingest_autonomous(
        fresh_ek,
        "update",
        {"id": note.id, "changes": {"body": body}},
        agent_id="ops-manage-knowledge",
    )
    assert result.action == "executed"
    assert result.entry_id == note.id
    assert fresh_ek.get(note.id).body == body + "\n"


def test_ai_auto_update_non_body_unaffected_by_body_guard(fresh_ek) -> None:
    source = _source(fresh_ek, "非 body 护栏来源")
    note = fresh_ek.ingest("note", note_payload(source.id, title="非 body 护栏笔记"))
    result = ingest_autonomous(
        fresh_ek,
        "update",
        {"id": note.id, "changes": {"tags": ["ai", "safe"]}},
        agent_id="agent-a",
    )
    assert result.action == "executed"
    assert fresh_ek.get(note.id).tags == ["ai", "safe"]


def test_ai_auto_unlink_normal(fresh_ek) -> None:
    source = _source(fresh_ek, "普通解除来源")
    target = fresh_ek.ingest("note", note_payload(source.id, title="普通解除笔记"))
    fresh_ek.link(source.id, target.id, rel_type="related")
    result = ingest_autonomous(
        fresh_ek,
        "unlink",
        {"source_id": source.id, "target_id": target.id, "type": "related"},
        agent_id="agent-a",
    )
    assert result.action == "executed"
    assert target.id not in fresh_ek._registry.neighbors(source.id, rel_type="related")


@pytest.mark.parametrize(
    ("op", "payload"),
    [
        ("rename", {"id": "placeholder", "new_slug": "new-slug"}),
        ("domains_add", {"name": "ai-domain"}),
        ("domains_migrate", {"entries": ["placeholder"], "target_domain": "ai-domain"}),
    ],
)
def test_must_approve_ops_queued(fresh_ek, op: str, payload: dict[str, object]) -> None:
    source = _source(fresh_ek, f"必审来源 {op}")
    fixed_payload = {}
    for key, value in payload.items():
        if value == ["placeholder"]:
            fixed_payload[key] = [source.id]
        elif value == "placeholder":
            fixed_payload[key] = source.id
        else:
            fixed_payload[key] = value
    result = ingest_autonomous(fresh_ek, op, fixed_payload, agent_id="agent-a")
    rows = list_queue(fresh_ek._registry, origin="ai_proposed")
    assert result.action == "queued_for_approval"
    assert result.queue_id == rows[0]["id"]
    assert rows[0]["proposed_op"] == op


def test_must_approve_unlink_critical_queued(fresh_ek) -> None:
    source = _source(fresh_ek, "关键解除来源")
    note = fresh_ek.ingest("note", note_payload(source.id, title="关键解除笔记"))
    result = ingest_autonomous(
        fresh_ek,
        "unlink",
        {"source_id": note.id, "target_id": source.id, "type": "source_refs"},
        agent_id="agent-a",
    )
    assert result.action == "queued_for_approval"
    assert source.id in fresh_ek._registry.neighbors(note.id, rel_type="source_refs")


def test_unlink_without_type_rejected(fresh_ek) -> None:
    source = _source(fresh_ek, "缺类型来源")
    note = fresh_ek.ingest(
        "note",
        note_payload(source.id, title="缺类型笔记"),
        conflict_policy="allow",
    )
    with pytest.raises(ValidationError, match="unlink payload.type 必填"):
        ingest_autonomous(
            fresh_ek,
            "unlink",
            {"source_id": note.id, "target_id": source.id},
            agent_id="agent-a",
        )


def test_link_guardrail_below_limit_auto(fresh_ek) -> None:
    source = _source(fresh_ek, "限流来源")
    target = fresh_ek.ingest("note", note_payload(source.id, title="限流目标"))
    result = ingest_autonomous(
        fresh_ek,
        "link",
        {"source_id": source.id, "target_id": target.id, "type": "related"},
        agent_id="agent-a",
    )
    audits = list_queue(fresh_ek._registry, status="resolved", origin="ai_auto")
    assert result.action == "executed"
    assert audits[0]["proposed_op"] == "link"


def test_link_guardrail_above_limit_queued(fresh_ek) -> None:
    source = _source(fresh_ek, "限流超额来源")
    for index in range(11):
        target = fresh_ek.ingest(
            "note",
            note_payload(source.id, title=f"限流目标 {index}"),
            conflict_policy="allow",
        )
        result = ingest_autonomous(
            fresh_ek,
            "link",
            {"source_id": source.id, "target_id": target.id, "type": "related"},
            agent_id="agent-a",
        )
    assert result.action == "queued_for_approval"
    assert result.queue_id is not None


def test_unknown_op_raises(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="未知操作"):
        ingest_autonomous(fresh_ek, "retract", {}, agent_id="agent-a")


def test_agent_id_required(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="agent_id"):
        ingest_autonomous(fresh_ek, "ingest", {"kind": "source"}, agent_id="")


def test_queue_origin_ai_proposed(fresh_ek) -> None:
    source = _source(fresh_ek, "origin 来源")
    ingest_autonomous(fresh_ek, "rename", {"id": source.id, "new_slug": "origin-new"}, agent_id="a")
    assert list_queue(fresh_ek._registry, origin="ai_proposed")[0]["agent_id"] == "a"
