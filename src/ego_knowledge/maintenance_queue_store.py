"""CRUD operations for the maintenance_queue table.

Functions here are stateless helpers that accept a Registry instance
and interact with the maintenance_queue table via registry.conn.
"""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from .doctor import Finding, Severity
from .errors import NotFoundError, StorageError, ValidationError
from .registry import Registry

# ---------------------------------------------------------------------------
# Channel mapping: severity → channel
# ---------------------------------------------------------------------------

_SEVERITY_TO_CHANNEL: dict[str, str] = {
    "warning": "review_only",
    "medium": "review_only",
    "high": "task_board",
}


def _channel_for_severity(severity: Severity) -> str:
    return _SEVERITY_TO_CHANNEL.get(str(severity), "review_only")


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _generate_mq_id() -> str:
    """Generate a queue id in the form ``mq_<ULID>``."""
    import ulid

    return f"mq_{ulid.new().str}"


# ---------------------------------------------------------------------------
# Details fallback: parse candidate ids from finding.message
# ---------------------------------------------------------------------------


def _parse_details_from_message(message: str) -> dict[str, object]:
    """Best-effort extraction of candidate ids from a finding message.

    This is the degradation path when the caller does not supply *details*.
    Looks for comma-separated id-like tokens after keywords such as
    "候选" / "未关联".
    """
    # Try to find the pattern after "候选:" / "候选：" / "未关联候选:"
    # and extract comma-separated tokens
    for sep in ("候选:", "候选：", "未关联候选:", "未关联候选："):
        if sep in message:
            after = message.split(sep, 1)[1]
            candidates = [tok.strip() for tok in after.split(",") if tok.strip()]
            if candidates:
                return {"candidates": candidates}
    return {}


def _load_optional_json(raw: object) -> object | None:
    """Best-effort decode for optional queue JSON fields.

    Corrupt proposed payloads must remain listable so reviewers can still see
    and approve/reject diagnostics without the review page crashing.
    """

    if raw is None:
        return None
    try:
        return cast(object, json.loads(cast(str, raw)))
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def enqueue(
    registry: Registry,
    finding: Finding,
    *,
    details: dict[str, object] | None = None,
    origin: str = "human",
    proposed_op: str | None = None,
    proposed_payload: dict[str, object] | None = None,
    agent_id: str | None = None,
    initial_status: str = "pending",
) -> str:
    """Write a *finding* into the ``maintenance_queue`` table.

    Returns the queue id (``mq_<ULID>``).

    **Idempotency**: if a row with the same ``(rule_id, COALESCE(entry_id,''), status='pending')``
    already exists, its ``updated_at`` is refreshed and ``details_json`` is merged,
    and the existing id is returned.

    **Low-severity rejection**: ``severity=low`` raises
    :class:`~ego_knowledge.errors.ValidationError`.
    """
    if finding.severity == Severity.LOW and initial_status != "resolved":
        raise ValidationError("low 不进 queue")
    if origin not in {"human", "ai_auto", "ai_proposed"}:
        raise ValidationError(f"不支持的 queue origin: {origin}")
    if initial_status not in {"pending", "sent", "resolved", "dismissed"}:
        raise ValidationError(f"不支持的 queue 初始状态: {initial_status}")
    if origin == "ai_proposed" and not proposed_op:
        raise ValidationError("ai_proposed queue 必须提供 proposed_op")

    rule_id = finding.rule_id
    entry_id = finding.target_id  # may be None
    severity = str(finding.severity)
    channel = _channel_for_severity(finding.severity)
    message = finding.message
    now_iso = _utc_now_text()

    # Resolve details_json
    if details is not None:
        details_payload = details
    else:
        details_payload = _parse_details_from_message(message)
    details_json_str = json.dumps(details_payload, ensure_ascii=False) if details_payload else None
    proposed_payload_json = (
        json.dumps(proposed_payload, ensure_ascii=False, sort_keys=True)
        if proposed_payload is not None
        else None
    )

    # Idempotency check: (rule_id, COALESCE(entry_id,''), status='pending')
    try:
        existing = registry.conn.execute(
            """
            SELECT id, details_json
              FROM maintenance_queue
             WHERE rule_id = ?
                AND COALESCE(entry_id, '') = ?
                AND status = 'pending'
                AND origin = ?
                AND COALESCE(proposed_op, '') = ?
                AND COALESCE(proposed_payload_json, '') = ?
              LIMIT 1
            """,
            (rule_id, entry_id or "", origin, proposed_op or "", proposed_payload_json or ""),
        ).fetchone()
    except sqlite3.Error as exc:
        raise StorageError(f"查询 maintenance_queue 失败: {exc}") from exc

    if existing is not None:
        existing_id = cast(str, existing["id"])
        existing_details_raw = existing["details_json"]
        # Merge details_json if both sides have data
        merged_details_json = details_json_str
        if existing_details_raw and details_json_str:
            try:
                old = json.loads(cast(str, existing_details_raw))
                new = json.loads(details_json_str)
                if isinstance(old, dict) and isinstance(new, dict):
                    merged = {**old, **new}
                    merged_details_json = json.dumps(merged, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass  # keep new details_json as-is
        try:
            registry.conn.execute(
                """
                UPDATE maintenance_queue
                   SET updated_at = ?,
                       details_json = COALESCE(?, details_json),
                       proposed_payload_json = COALESCE(?, proposed_payload_json),
                       agent_id = COALESCE(?, agent_id)
                  WHERE id = ?
                """,
                (now_iso, merged_details_json, proposed_payload_json, agent_id, existing_id),
            )
            registry.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"更新 maintenance_queue 失败: {exc}") from exc
        return existing_id

    # Insert new row
    mq_id = _generate_mq_id()
    try:
        registry.conn.execute(
            """
            INSERT INTO maintenance_queue(
                id, rule_id, severity, entry_id, channel,
                status, message, details_json, origin, proposed_op,
                proposed_payload_json, agent_id, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mq_id,
                rule_id,
                severity,
                entry_id,
                channel,
                initial_status,
                message,
                details_json_str,
                origin,
                proposed_op,
                proposed_payload_json,
                agent_id,
                now_iso,
                now_iso,
            ),
        )
        registry.commit()
    except sqlite3.Error as exc:
        raise StorageError(f"写入 maintenance_queue 失败: {exc}") from exc

    return mq_id


def mark_sent(registry: Registry, queue_id: str) -> None:
    """Transition status ``pending → sent`` for a high-severity queue item."""
    _transition_status(registry, queue_id, {"pending"}, "sent")


def resolve(registry: Registry, queue_id: str) -> None:
    """Transition status ``pending/sent → resolved``."""
    _transition_status(registry, queue_id, {"pending", "sent"}, "resolved")


def dismiss(registry: Registry, queue_id: str) -> None:
    """Transition status ``pending/sent → dismissed``."""
    _transition_status(registry, queue_id, {"pending", "sent"}, "dismissed")


def list_queue(
    registry: Registry,
    *,
    status: str | None = None,
    channel: str | None = None,
    severity: str | None = None,
    since: str | None = None,
    origin: str | None = None,
) -> list[dict[str, object]]:
    """Read the maintenance_queue with optional filters.

    Returns a list of dicts, each representing a row.
    """
    clauses: list[str] = []
    params: list[object] = []

    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if channel is not None:
        clauses.append("channel = ?")
        params.append(channel)
    if severity is not None:
        clauses.append("severity = ?")
        params.append(severity)
    if since is not None:
        clauses.append("created_at >= ?")
        params.append(since)
    if origin is not None:
        clauses.append("origin = ?")
        params.append(origin)

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    sql = f"""
        SELECT id, rule_id, severity, entry_id, channel,
               status, message, details_json, origin, proposed_op,
               proposed_payload_json, agent_id, created_at, updated_at
          FROM maintenance_queue
          {where}
         ORDER BY created_at DESC
    """
    try:
        rows = registry.conn.execute(sql, tuple(params)).fetchall()
    except sqlite3.Error as exc:
        raise StorageError(f"查询 maintenance_queue 失败: {exc}") from exc

    results: list[dict[str, object]] = []
    for row in rows:
        record: dict[str, object] = {
            "id": cast(str, row["id"]),
            "rule_id": cast(str, row["rule_id"]),
            "severity": cast(str, row["severity"]),
            "entry_id": cast(str | None, row["entry_id"]),
            "channel": cast(str, row["channel"]),
            "status": cast(str, row["status"]),
            "message": cast(str, row["message"]),
            "details_json": _load_optional_json(row["details_json"]),
            "origin": cast(str, row["origin"]),
            "proposed_op": cast(str | None, row["proposed_op"]),
            "proposed_payload_json": _load_optional_json(row["proposed_payload_json"]),
            "proposed_payload_raw": cast(str | None, row["proposed_payload_json"]),
            "agent_id": cast(str | None, row["agent_id"]),
            "created_at": cast(str, row["created_at"]),
            "updated_at": cast(str, row["updated_at"]),
        }
        results.append(record)
    return results


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _transition_status(
    registry: Registry,
    queue_id: str,
    from_statuses: set[str],
    to_status: str,
) -> None:
    """Generic status transition with existence check."""
    try:
        row = registry.conn.execute(
            "SELECT status FROM maintenance_queue WHERE id = ?",
            (queue_id,),
        ).fetchone()
    except sqlite3.Error as exc:
        raise StorageError(f"查询 maintenance_queue 失败: {exc}") from exc

    if row is None:
        raise NotFoundError(f"队列条目不存在: {queue_id}")

    current = cast(str, row["status"])
    if current not in from_statuses:
        raise ValidationError(f"状态不允许从 '{current}' 转换到 '{to_status}'")

    try:
        registry.conn.execute(
            """
            UPDATE maintenance_queue
               SET status = ?, updated_at = ?
             WHERE id = ?
            """,
            (to_status, _utc_now_text(), queue_id),
        )
        registry.commit()
    except sqlite3.Error as exc:
        raise StorageError(f"更新 maintenance_queue 状态失败: {exc}") from exc


def _utc_now_text() -> str:
    """Return the current UTC timestamp as ISO-8601 text (microsecond precision)."""
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat(timespec="microseconds")
