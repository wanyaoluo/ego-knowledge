"""执行 maintenance_queue 中已获批准的 AI 提议。"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from . import maintenance_queue_store
from ._validation import _require_str
from .core import EgoKnowledge
from .errors import NotFoundError, ValidationError

Executor = Callable[[EgoKnowledge, dict[str, Any]], object]


def _exec_unlink(ek: EgoKnowledge, payload: dict[str, Any]) -> None:
    source_id = _require_str(payload, "source_id")
    target_id = _require_str(payload, "target_id")
    ek.unlink(source_id, target_id)


def _exec_rename(ek: EgoKnowledge, payload: dict[str, Any]) -> object:
    return ek.rename(_require_str(payload, "id"), _require_str(payload, "new_slug"))


def _exec_domains_add(ek: EgoKnowledge, payload: dict[str, Any]) -> None:
    ek.domains_add(_require_str(payload, "name"))


def _exec_domains_migrate(ek: EgoKnowledge, payload: dict[str, Any]) -> object:
    entries = payload.get("entries")
    if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
        raise ValidationError("payload.entries 必须是字符串数组")
    return ek.domains_migrate(entries, _require_str(payload, "target_domain"))


def _exec_link(ek: EgoKnowledge, payload: dict[str, Any]) -> object:
    return ek.link(
        _require_str(payload, "source_id"),
        _require_str(payload, "target_id"),
        _require_str(payload, "type"),
        source=str(payload.get("source", "ai_confirmed")),
    )


OP_TO_METHOD: dict[str, Executor] = {
    "unlink_critical": _exec_unlink,
    "rename": _exec_rename,
    "domains_add": _exec_domains_add,
    "domains_migrate": _exec_domains_migrate,
    "link_guardrailed": _exec_link,
    "unlink_normal_guardrailed": _exec_unlink,
}


def approve(ek: EgoKnowledge, queue_id: str) -> dict[str, object]:
    """批准并执行一条 AI 提议；执行失败时保持原 queue 状态。"""
    record = _get_queue_record(ek, queue_id)
    _ensure_ai_proposed(record)
    if record["status"] not in {"pending", "sent"}:
        return {"ok": False, "reason": "already resolved/dismissed"}

    op = record["proposed_op"]
    if not isinstance(op, str) or op not in OP_TO_METHOD:
        raise ValidationError(f"未知的 proposed_op: {op}")
    payload = _parse_payload(record["proposed_payload_json"])
    OP_TO_METHOD[op](ek, payload)
    maintenance_queue_store.resolve(ek._registry, queue_id)
    return {"ok": True, "executed_op": op, "id": queue_id}


def reject(ek: EgoKnowledge, queue_id: str, reason: str = "") -> dict[str, object]:
    record = _get_queue_record(ek, queue_id)
    _ensure_ai_proposed(record)
    if record["status"] in {"pending", "sent"}:
        maintenance_queue_store.dismiss(ek._registry, queue_id)
    return {"ok": True, "id": queue_id, "rejected_reason": reason}


def _get_queue_record(ek: EgoKnowledge, queue_id: str) -> Any:
    record = ek._registry.conn.execute(
        "SELECT * FROM maintenance_queue WHERE id = ?",
        (queue_id,),
    ).fetchone()
    if record is None:
        raise NotFoundError(f"queue 条目不存在: {queue_id}")
    return record


def _ensure_ai_proposed(record: Any) -> None:
    if record["origin"] != "ai_proposed":
        raise ValidationError(f"只能处理 AI 提议,当前 origin={record['origin']}")


def _parse_payload(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        raise ValidationError("proposed_payload_json 缺失")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError("proposed_payload_json 不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("proposed_payload_json 必须是 object")
    return payload
