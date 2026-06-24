"""AI 自主操作编排：按 Phase 8 权限矩阵分流。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import maintenance_queue_store
from ._validation import _require_str
from .core import EgoKnowledge
from .doctor import Finding, Severity
from .errors import ValidationError
from .registry import Registry

AI_AUTO_OPS = frozenset({"ingest", "update", "touch", "promote", "unlink_normal"})
AI_AUTO_WITH_GUARDRAIL_OPS = frozenset({"link"})
MUST_APPROVE_OPS = frozenset({"unlink_critical", "rename", "domains_add", "domains_migrate"})
CRITICAL_RELATION_TYPES = frozenset({"evidence_refs", "source_refs", "supersedes", "superseded_by"})
BODY_AUTHORIZED_AGENTS = frozenset({"ops-manage-knowledge"})
_BODY_UPDATE_BLOCKED_RULE_ID = "autonomous_body_update_blocked"


@dataclass(slots=True)
class AutonomousResult:
    action: str
    entry_id: str | None
    queue_id: str | None
    findings: list[Finding]


def ingest_autonomous(
    ek: EgoKnowledge,
    op: str,
    payload: dict[str, Any],
    *,
    agent_id: str,
) -> AutonomousResult:
    """AI agent 入口：直接执行低风险操作，必审操作只写审批队列。"""
    if not agent_id.strip():
        raise ValidationError("agent_id 必填")
    if not isinstance(payload, dict):
        raise ValidationError("payload 必须是 JSON object")

    normalized_op = _normalize_op(op, payload)
    if normalized_op in MUST_APPROVE_OPS:
        queue_id = _enqueue_approval(ek, normalized_op, payload, agent_id)
        return AutonomousResult("queued_for_approval", None, queue_id, [])

    if normalized_op in AI_AUTO_WITH_GUARDRAIL_OPS:
        if not _guardrail_check(ek._registry, normalized_op, payload, agent_id):
            queue_id = _enqueue_approval(ek, f"{normalized_op}_guardrailed", payload, agent_id)
            return AutonomousResult("queued_for_approval", None, queue_id, [])

    if normalized_op in AI_AUTO_OPS or normalized_op in AI_AUTO_WITH_GUARDRAIL_OPS:
        result = _dispatch_op(ek, normalized_op, payload, agent_id=agent_id)
        if result.entry_id is not None:
            _mark_recent_queue_items_origin(ek, result.entry_id, agent_id)
        if normalized_op in AI_AUTO_WITH_GUARDRAIL_OPS:
            _write_guardrail_audit(ek, normalized_op, payload, agent_id)
        return result

    raise ValidationError(f"未知操作: {op}")


def _normalize_op(op: str, payload: dict[str, Any]) -> str:
    if op in {"unlink", "unlink_normal"}:
        rel_type = payload.get("type")
        if not isinstance(rel_type, str) or not rel_type:
            raise ValidationError("unlink payload.type 必填")
        if rel_type in CRITICAL_RELATION_TYPES:
            return "unlink_critical"
        return "unlink_normal"
    return op


def _dispatch_op(
    ek: EgoKnowledge,
    op: str,
    payload: dict[str, Any],
    *,
    agent_id: str,
) -> AutonomousResult:
    if op == "ingest":
        kind = _require_str(payload, "kind")
        raw_entry_payload = payload.get("payload")
        entry_payload = raw_entry_payload if isinstance(raw_entry_payload, dict) else payload
        entry_payload = {
            k: v for k, v in entry_payload.items() if k not in {"kind", "conflict_policy"}
        }
        conflict_policy = str(payload.get("conflict_policy", "strict"))
        entry_id = ek.ingest(kind, entry_payload, conflict_policy=conflict_policy).id
        return AutonomousResult("executed", entry_id, None, [])
    if op == "update":
        changes = _require_dict(payload, "changes")
        if "body" in changes and agent_id not in BODY_AUTHORIZED_AGENTS:
            return _body_update_blocked(payload, agent_id)
        entry_id = ek.update(_require_str(payload, "id"), changes).id
        return AutonomousResult("executed", entry_id, None, [])
    if op == "touch":
        entry_id = ek.touch(_require_str(payload, "id")).id
        return AutonomousResult("executed", entry_id, None, [])
    if op == "promote":
        entry_id = ek.promote(
            _require_str(payload, "id"),
            _require_str(payload, "target_kind"),
            str(payload.get("freshness", "watch")),
        ).id
        return AutonomousResult("executed", entry_id, None, [])
    if op == "unlink_normal":
        ek.unlink(_require_str(payload, "source_id"), _require_str(payload, "target_id"))
        return AutonomousResult("executed", _require_str(payload, "source_id"), None, [])
    if op == "link":
        ek.link(
            _require_str(payload, "source_id"),
            _require_str(payload, "target_id"),
            _require_str(payload, "type"),
            source=str(payload.get("source", "ai_suggested")),
        )
        return AutonomousResult("executed", _require_str(payload, "source_id"), None, [])
    raise ValidationError(f"未知操作: {op}")


def _body_update_blocked(payload: dict[str, Any], agent_id: str) -> AutonomousResult:
    finding = Finding(
        rule_id=_BODY_UPDATE_BLOCKED_RULE_ID,
        severity=Severity.HIGH,
        target_id=_entry_id_for_payload(payload),
        target_path=None,
        message=(
            "Autonomous body update requires ops-manage-knowledge; "
            f"agent_id={agent_id} is not authorized"
        ),
    )
    return AutonomousResult("blocked", None, None, [finding])


def _enqueue_approval(
    ek: EgoKnowledge,
    proposed_op: str,
    proposed_payload: dict[str, Any],
    agent_id: str,
) -> str:
    entry_id = _entry_id_for_payload(proposed_payload)
    finding = Finding(
        rule_id=f"ai_proposed_{proposed_op}",
        severity=Severity.MEDIUM,
        target_id=entry_id,
        target_path=None,
        message=f"AI 提议待审批: {proposed_op}",
    )
    return maintenance_queue_store.enqueue(
        ek._registry,
        finding,
        origin="ai_proposed",
        proposed_op=proposed_op,
        proposed_payload=proposed_payload,
        agent_id=agent_id,
    )


def _write_guardrail_audit(
    ek: EgoKnowledge,
    op: str,
    payload: dict[str, Any],
    agent_id: str,
) -> None:
    finding = Finding(
        rule_id=f"audit_{op}",
        severity=Severity.LOW,
        target_id=_entry_id_for_payload(payload),
        target_path=None,
        message=f"AI 自主执行审计: {op}",
    )
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    ek._registry.conn.execute(
        """
        UPDATE maintenance_queue
           SET status = 'resolved', updated_at = created_at
         WHERE rule_id = ?
           AND COALESCE(entry_id, '') = ?
           AND origin = 'ai_auto'
           AND proposed_op = ?
           AND proposed_payload_json = ?
           AND status = 'pending'
        """,
        (
            finding.rule_id,
            finding.target_id or "",
            op,
            payload_json,
        ),
    )
    ek._registry.commit()
    existing = ek._registry.conn.execute(
        """
        SELECT id FROM maintenance_queue
         WHERE rule_id = ?
           AND COALESCE(entry_id, '') = ?
           AND origin = 'ai_auto'
           AND proposed_op = ?
           AND proposed_payload_json = ?
           AND status = 'resolved'
         LIMIT 1
        """,
        (finding.rule_id, finding.target_id or "", op, payload_json),
    ).fetchone()
    if existing is not None:
        return
    maintenance_queue_store.enqueue(
        ek._registry,
        finding,
        origin="ai_auto",
        proposed_op=op,
        proposed_payload=payload,
        agent_id=agent_id,
        initial_status="resolved",
    )


def _guardrail_check(registry: Registry, op: str, payload: dict[str, Any], agent_id: str) -> bool:
    if op != "link":
        return True
    source_id = _require_str(payload, "source_id")
    today = datetime.now(tz=UTC).date().isoformat()
    conn = registry.conn
    if _sqlite_supports_json_extract(conn):
        row = conn.execute(
            """
            SELECT COUNT(*) AS total FROM maintenance_queue
             WHERE agent_id = ?
               AND proposed_op IN ('link', 'link_guardrailed')
               AND date(created_at) = ?
               AND json_extract(proposed_payload_json, '$.source_id') = ?
            """,
            (agent_id, today, source_id),
        ).fetchone()
        return int(row["total"] if row is not None else 0) < 10
    rows = conn.execute(
        """
        SELECT proposed_payload_json FROM maintenance_queue
         WHERE agent_id = ?
           AND proposed_op IN ('link', 'link_guardrailed')
           AND date(created_at) = ?
        """,
        (agent_id, today),
    ).fetchall()
    count = 0
    for row in rows:
        try:
            if json.loads(row["proposed_payload_json"] or "{}").get("source_id") == source_id:
                count += 1
        except json.JSONDecodeError:
            continue
    return count < 10


def _sqlite_supports_json_extract(conn: object) -> bool:
    try:
        getattr(conn, "execute")("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
        return True
    except sqlite3.Error:
        return False


def _mark_recent_queue_items_origin(ek: EgoKnowledge, entry_id: str, agent_id: str) -> None:
    ek._registry.conn.execute(
        """
        UPDATE maintenance_queue
           SET origin = 'ai_auto', agent_id = COALESCE(agent_id, ?)
         WHERE entry_id = ?
           AND origin = 'human'
           AND status = 'pending'
           AND created_at >= datetime('now', '-5 seconds')
        """,
        (agent_id, entry_id),
    )
    ek._registry.commit()


def _entry_id_for_payload(payload: dict[str, Any]) -> str | None:
    for key in ("id", "source_id", "entry_id"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValidationError(f"payload.{key} 必须是 object")
    return value
