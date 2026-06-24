"""Unit tests for maintenance_queue_store CRUD operations."""

from __future__ import annotations

import pytest

from ego_knowledge.doctor import Finding, Severity
from ego_knowledge.errors import NotFoundError, ValidationError
from ego_knowledge.maintenance_queue_store import (
    dismiss,
    enqueue,
    list_queue,
    mark_sent,
    resolve,
)
from ego_knowledge.registry import Registry

from .support import source_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _finding(
    rule_id: str = "test_rule",
    severity: Severity = Severity.MEDIUM,
    target_id: str | None = "ek_src_01HTEST",
    message: str = "测试 finding",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        target_id=target_id,
        target_path=None,
        message=message,
    )


def _enqueue_one(
    registry: Registry,
    *,
    severity: Severity = Severity.MEDIUM,
    rule_id: str = "test_rule",
    target_id: str | None = "ek_src_01HTEST",
    message: str = "测试 finding",
    details: dict | None = None,
) -> str:
    f = _finding(rule_id=rule_id, severity=severity, target_id=target_id, message=message)
    return enqueue(registry, f, details=details)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEnqueue:
    def test_enqueue_new_pending(self, fresh_ek) -> None:
        """New finding inserts a pending row."""
        source = fresh_ek.ingest("source", source_payload(title="队列来源"))
        mq_id = _enqueue_one(fresh_ek._registry, target_id=source.id)

        assert mq_id.startswith("mq_")

        rows = list_queue(fresh_ek._registry, status="pending")
        assert len(rows) == 1
        row = rows[0]
        assert row["id"] == mq_id
        assert row["status"] == "pending"
        assert row["severity"] == "medium"
        assert row["channel"] == "review_only"
        assert row["entry_id"] == source.id
        assert row["origin"] == "human"
        assert row["proposed_op"] is None

    def test_enqueue_idempotent(self, fresh_ek) -> None:
        """Same (rule_id, target_id, pending) only updates updated_at."""
        source = fresh_ek.ingest("source", source_payload(title="幂等来源"))
        reg = fresh_ek._registry

        id1 = _enqueue_one(reg, rule_id="r1", target_id=source.id)
        id2 = _enqueue_one(reg, rule_id="r1", target_id=source.id)
        assert id1 == id2

        rows = list_queue(reg, status="pending")
        assert len(rows) == 1

    def test_enqueue_idempotent_null_target(self, fresh_ek) -> None:
        """Idempotency works when target_id is None."""
        reg = fresh_ek._registry

        id1 = _enqueue_one(reg, rule_id="global_rule", target_id=None)
        id2 = _enqueue_one(reg, rule_id="global_rule", target_id=None)
        assert id1 == id2

        rows = list_queue(reg, status="pending")
        assert len(rows) == 1

    def test_enqueue_low_rejected(self, fresh_ek) -> None:
        """Low severity raises ValidationError."""
        with pytest.raises(ValidationError, match="low 不进 queue"):
            _enqueue_one(fresh_ek._registry, severity=Severity.LOW)

    def test_enqueue_high_uses_task_board_channel(self, fresh_ek) -> None:
        """High severity maps to task_board channel."""
        source = fresh_ek.ingest("source", source_payload(title="高危来源"))
        _enqueue_one(
            fresh_ek._registry,
            severity=Severity.HIGH,
            target_id=source.id,
        )
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        assert rows[0]["channel"] == "task_board"

    def test_enqueue_with_explicit_details(self, fresh_ek) -> None:
        """Explicit details dict is stored as details_json."""
        source = fresh_ek.ingest("source", source_payload(title="详情来源"))
        details = {"candidates": ["ek_a", "ek_b"], "threshold": 0.8}
        _enqueue_one(
            fresh_ek._registry,
            target_id=source.id,
            details=details,
        )
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        assert rows[0]["details_json"] == details

    def test_enqueue_details_fallback_from_message(self, fresh_ek) -> None:
        """When details is None, candidates are parsed from message."""
        source = fresh_ek.ingest("source", source_payload(title="降级解析来源"))
        _enqueue_one(
            fresh_ek._registry,
            target_id=source.id,
            message="重复候选: ek_a,ek_b,ek_c",
        )
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        dj = rows[0]["details_json"]
        assert dj is not None
        assert dj.get("candidates") == ["ek_a", "ek_b", "ek_c"]

    def test_enqueue_different_rule_creates_separate_rows(self, fresh_ek) -> None:
        """Different rule_id creates separate queue entries."""
        source = fresh_ek.ingest("source", source_payload(title="分离来源"))
        reg = fresh_ek._registry

        _enqueue_one(reg, rule_id="rule_a", target_id=source.id)
        _enqueue_one(reg, rule_id="rule_b", target_id=source.id)

        rows = list_queue(reg, status="pending")
        assert len(rows) == 2

    def test_enqueue_ai_proposed_payload(self, fresh_ek) -> None:
        source = fresh_ek.ingest("source", source_payload(title="审批来源"))
        payload = {"id": source.id, "new_slug": "approved-slug"}
        mq_id = enqueue(
            fresh_ek._registry,
            _finding(rule_id="ai_proposed_rename", target_id=source.id),
            origin="ai_proposed",
            proposed_op="rename",
            proposed_payload=payload,
            agent_id="agent-a",
        )

        rows = list_queue(fresh_ek._registry, origin="ai_proposed")
        assert rows[0]["id"] == mq_id
        assert rows[0]["proposed_op"] == "rename"
        assert rows[0]["proposed_payload_json"] == payload
        assert rows[0]["agent_id"] == "agent-a"

    def test_ai_proposed_requires_op(self, fresh_ek) -> None:
        with pytest.raises(ValidationError, match="proposed_op"):
            enqueue(fresh_ek._registry, _finding(), origin="ai_proposed")

    def test_list_queue_bad_payload_keeps_raw(self, fresh_ek) -> None:
        source = fresh_ek.ingest("source", source_payload(title="坏 payload 列表来源"))
        mq_id = enqueue(
            fresh_ek._registry,
            _finding(rule_id="ai_proposed_rename", target_id=source.id),
            origin="ai_proposed",
            proposed_op="rename",
            proposed_payload={"id": source.id, "new_slug": "bad"},
            agent_id="agent-a",
        )
        fresh_ek._registry.conn.execute(
            "UPDATE maintenance_queue SET proposed_payload_json = ? WHERE id = ?",
            ("{bad-json", mq_id),
        )
        fresh_ek._registry.commit()

        rows = list_queue(fresh_ek._registry, status="pending")
        assert rows[0]["id"] == mq_id
        assert rows[0]["proposed_payload_raw"] == "{bad-json"
        assert rows[0]["proposed_payload_json"] is None


class TestMarkSent:
    def test_mark_sent_pending_to_sent(self, fresh_ek) -> None:
        """Status transitions pending → sent."""
        source = fresh_ek.ingest("source", source_payload(title="发送来源"))
        reg = fresh_ek._registry
        mq_id = _enqueue_one(reg, severity=Severity.HIGH, target_id=source.id)

        mark_sent(reg, mq_id)

        rows = list_queue(reg, status="sent")
        assert len(rows) == 1
        assert rows[0]["id"] == mq_id
        # pending should be empty now
        assert list_queue(reg, status="pending") == []

    def test_mark_sent_not_found(self, fresh_ek) -> None:
        """Non-existent id raises NotFoundError."""
        with pytest.raises(NotFoundError, match="队列条目不存在"):
            mark_sent(fresh_ek._registry, "mq_nonexistent")

    def test_mark_sent_wrong_status(self, fresh_ek) -> None:
        """Cannot mark sent from a non-pending status."""
        source = fresh_ek.ingest("source", source_payload(title="错误来源"))
        reg = fresh_ek._registry
        mq_id = _enqueue_one(reg, severity=Severity.HIGH, target_id=source.id)

        mark_sent(reg, mq_id)  # pending → sent
        with pytest.raises(ValidationError, match="状态不允许"):
            mark_sent(reg, mq_id)  # sent → sent: invalid


class TestListQueue:
    def test_list_all(self, fresh_ek) -> None:
        """No filters returns all rows."""
        source = fresh_ek.ingest("source", source_payload(title="列表来源"))
        reg = fresh_ek._registry

        _enqueue_one(reg, target_id=source.id, severity=Severity.MEDIUM)
        _enqueue_one(reg, rule_id="high_rule", severity=Severity.HIGH, target_id=source.id)

        rows = list_queue(reg)
        assert len(rows) == 2

    def test_list_filter_status(self, fresh_ek) -> None:
        """Filter by status."""
        source = fresh_ek.ingest("source", source_payload(title="状态过滤来源"))
        reg = fresh_ek._registry
        _enqueue_one(reg, target_id=source.id)

        pending = list_queue(reg, status="pending")
        resolved = list_queue(reg, status="resolved")
        assert len(pending) == 1
        assert len(resolved) == 0

    def test_list_filter_channel(self, fresh_ek) -> None:
        """Filter by channel."""
        source = fresh_ek.ingest("source", source_payload(title="通道过滤来源"))
        reg = fresh_ek._registry
        _enqueue_one(reg, severity=Severity.HIGH, target_id=source.id)
        _enqueue_one(reg, rule_id="medium_rule", severity=Severity.MEDIUM, target_id=source.id)

        task_board = list_queue(reg, channel="task_board")
        review_only = list_queue(reg, channel="review_only")
        assert len(task_board) == 1
        assert len(review_only) == 1

    def test_list_filter_severity(self, fresh_ek) -> None:
        """Filter by severity."""
        source = fresh_ek.ingest("source", source_payload(title="严重度过滤来源"))
        reg = fresh_ek._registry
        _enqueue_one(reg, severity=Severity.HIGH, target_id=source.id)
        _enqueue_one(reg, rule_id="medium_rule", severity=Severity.MEDIUM, target_id=source.id)

        high = list_queue(reg, severity="high")
        medium = list_queue(reg, severity="medium")
        assert len(high) == 1
        assert len(medium) == 1

    def test_list_filter_since(self, fresh_ek) -> None:
        """Filter by created_at >= since."""
        source = fresh_ek.ingest("source", source_payload(title="时间过滤来源"))
        reg = fresh_ek._registry
        _enqueue_one(reg, target_id=source.id)

        rows = list_queue(reg, since="2000-01-01")
        assert len(rows) >= 1

        rows_future = list_queue(reg, since="2099-01-01")
        assert len(rows_future) == 0


class TestResolveDismiss:
    def test_resolve_pending(self, fresh_ek) -> None:
        """Resolve from pending."""
        source = fresh_ek.ingest("source", source_payload(title="解决来源"))
        reg = fresh_ek._registry
        mq_id = _enqueue_one(reg, target_id=source.id)

        resolve(reg, mq_id)
        rows = list_queue(reg, status="resolved")
        assert len(rows) == 1
        assert rows[0]["id"] == mq_id

    def test_resolve_sent(self, fresh_ek) -> None:
        """Resolve from sent."""
        source = fresh_ek.ingest("source", source_payload(title="解决已发送来源"))
        reg = fresh_ek._registry
        mq_id = _enqueue_one(reg, severity=Severity.HIGH, target_id=source.id)
        mark_sent(reg, mq_id)

        resolve(reg, mq_id)
        rows = list_queue(reg, status="resolved")
        assert len(rows) == 1

    def test_dismiss_pending(self, fresh_ek) -> None:
        """Dismiss from pending."""
        source = fresh_ek.ingest("source", source_payload(title="忽略来源"))
        reg = fresh_ek._registry
        mq_id = _enqueue_one(reg, target_id=source.id)

        dismiss(reg, mq_id)
        rows = list_queue(reg, status="dismissed")
        assert len(rows) == 1
        assert rows[0]["id"] == mq_id

    def test_dismiss_sent(self, fresh_ek) -> None:
        """Dismiss from sent."""
        source = fresh_ek.ingest("source", source_payload(title="忽略已发送来源"))
        reg = fresh_ek._registry
        mq_id = _enqueue_one(reg, severity=Severity.HIGH, target_id=source.id)
        mark_sent(reg, mq_id)

        dismiss(reg, mq_id)
        rows = list_queue(reg, status="dismissed")
        assert len(rows) == 1

    def test_resolve_not_found(self, fresh_ek) -> None:
        """Resolve non-existent id raises NotFoundError."""
        with pytest.raises(NotFoundError):
            resolve(fresh_ek._registry, "mq_nonexistent")

    def test_dismiss_not_found(self, fresh_ek) -> None:
        """Dismiss non-existent id raises NotFoundError."""
        with pytest.raises(NotFoundError):
            dismiss(fresh_ek._registry, "mq_nonexistent")
