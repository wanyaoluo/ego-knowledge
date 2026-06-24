"""Integration tests for _push_findings_by_severity (Phase 3, Task 3.5).

Verifies the severity-based routing logic:
- low → skipped
- action_demote → skipped (_NO_QUEUE_RULES)
- medium → enqueued to maintenance_queue, no task_board
- high → enqueued + task_board + mark_sent

Also verifies _append_to_ai_session_pending has been fully removed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ego_knowledge.diagnose import (
    _DIAGNOSE_RULES,
    _NO_QUEUE_RULES,
    _push_findings_by_severity,
)
from ego_knowledge.doctor import Finding, Severity
from ego_knowledge.maintenance_queue_store import list_queue
from tests.unit.support import source_payload

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(
    rule_id: str = "decay_source_context",
    severity: Severity = Severity.MEDIUM,
    *,
    target_id: str | None = None,
    target_path: str | None = "/some/path.md",
    message: str = "测试 finding",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity,
        target_id=target_id,
        target_path=target_path,
        message=message,
    )


def _ingest_source(fresh_ek, *, idx: int = 0) -> str:
    """创建真实 source entry，返回其 ID。idx 用于区分多次调用。"""
    tag = f"-{idx}" if idx else ""
    source = fresh_ek.ingest("source", source_payload(title=f"推送诊断测试来源{tag}"))
    return source.id


# ---------------------------------------------------------------------------
# _NO_QUEUE_RULES constants
# ---------------------------------------------------------------------------


class TestNoQueueRules:
    """Verify _NO_QUEUE_RULES frozenset contents."""

    def test_no_queue_rules_contains_action_demote(self) -> None:
        assert "action_demote" in _NO_QUEUE_RULES

    def test_no_queue_rules_is_frozenset(self) -> None:
        assert isinstance(_NO_QUEUE_RULES, frozenset)


# ---------------------------------------------------------------------------
# Low severity
# ---------------------------------------------------------------------------


class TestLowSeveritySkipped:
    """low severity findings must not enter the queue."""

    def test_low_severity_not_enqueued(self, fresh_ek, ek_root) -> None:
        findings = [_make_finding(severity=Severity.LOW)]
        _push_findings_by_severity(findings, fresh_ek._registry)

        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# _NO_QUEUE_RULES (action_demote)
# ---------------------------------------------------------------------------


class TestNoQueueRulesSkipped:
    """Findings with rule_id in _NO_QUEUE_RULES must not enter the queue."""

    def test_action_demote_not_enqueued(self, fresh_ek, ek_root) -> None:
        findings = [_make_finding(rule_id="action_demote", severity=Severity.MEDIUM)]
        _push_findings_by_severity(findings, fresh_ek._registry)

        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Medium severity
# ---------------------------------------------------------------------------


class TestMediumSeverity:
    """medium findings enter queue but do NOT trigger task_board."""

    def test_medium_enqueued(self, fresh_ek, ek_root) -> None:
        entry_id = _ingest_source(fresh_ek)
        findings = [
            _make_finding(
                rule_id="decay_source_context", severity=Severity.MEDIUM, target_id=entry_id
            )
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)

        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        assert rows[0]["severity"] == "medium"
        assert rows[0]["status"] == "pending"

    @patch("ego_knowledge.diagnose._create_task_board_task")
    def test_medium_does_not_call_task_board(
        self,
        mock_task_board: MagicMock,
        fresh_ek,
        ek_root,
    ) -> None:
        entry_id = _ingest_source(fresh_ek)
        findings = [
            _make_finding(
                rule_id="decay_source_context", severity=Severity.MEDIUM, target_id=entry_id
            )
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)

        mock_task_board.assert_not_called()


# ---------------------------------------------------------------------------
# High severity
# ---------------------------------------------------------------------------


class TestHighSeverity:
    """high findings enter queue AND trigger task_board + mark_sent."""

    @patch("ego_knowledge.diagnose._create_task_board_task")
    def test_high_enqueued_and_task_board_called(
        self,
        mock_task_board: MagicMock,
        fresh_ek,
        ek_root,
    ) -> None:
        entry_id = _ingest_source(fresh_ek)
        findings = [
            _make_finding(rule_id="push_premise_shaken", severity=Severity.HIGH, target_id=entry_id)
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)

        # task_board called
        mock_task_board.assert_called_once()
        called_finding = mock_task_board.call_args[0][0]
        assert called_finding.rule_id == "push_premise_shaken"

        # queue has entry with status=sent
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        assert rows[0]["severity"] == "high"
        assert rows[0]["status"] == "sent"


class TestHighSeverityDegradation:
    """P0.4: 未配置 EK_TASK_BOARD_DIR 时，HIGH finding 仍进 queue 但不外推。

    降级语义（spec §5.4）：``_create_task_board_task`` 抛 ``StorageError`` 时，
    ``diagnose._push_findings_by_severity`` 接住并 ``log.warning`` 继续；
    finding 留在 maintenance_queue（enqueue 已发生）但 status 不为 ``sent``
    （mark_sent 跳过）。
    """

    def test_unconfigured_high_finding_stays_unsent(
        self,
        fresh_ek,
        ek_root,
        monkeypatch,
    ) -> None:
        # 不 mock _create_task_board_task（让它真走降级路径），
        # 仅 patch _resolve_task_board_dir 返回 None 触发 StorageError。
        monkeypatch.setattr(
            "ego_knowledge.doctor._resolve_task_board_dir",
            lambda: None,
        )

        entry_id = _ingest_source(fresh_ek)
        findings = [
            _make_finding(
                rule_id="push_premise_shaken", severity=Severity.HIGH, target_id=entry_id
            )
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)

        # finding 仍在 maintenance_queue（enqueue 不受降级影响）
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        assert rows[0]["severity"] == "high"
        # 但 status 不是 sent（未调 mark_sent）
        assert rows[0]["status"] != "sent"

    def test_mark_sent_failure_does_not_crash_diagnose(
        self,
        fresh_ek,
        ek_root,
        monkeypatch,
    ) -> None:
        """P0.4 W2：mark_sent 抛非 StorageError（如 NotFoundError）时 diagnose 不崩。

        分离故障域后，task-board 推送成功但 mark_sent 失败时：
        - diagnose 主流程不阻断（mark_sent 失败被独立 try 接住）
        - finding 留在 queue（mark_sent 未成功，status 仍 pending）
        """
        from ego_knowledge.errors import NotFoundError

        # task-board 推送成功路径（mock _create_task_board_task 不抛错）
        monkeypatch.setattr(
            "ego_knowledge.diagnose._create_task_board_task",
            lambda finding: None,
        )
        # mark_sent 抛 NotFoundError（非 StorageError 子类）
        def _raise_not_found(registry, queue_id):
            raise NotFoundError(f"队列条目不存在: {queue_id}")

        monkeypatch.setattr("ego_knowledge.diagnose.mark_sent", _raise_not_found)

        entry_id = _ingest_source(fresh_ek)
        findings = [
            _make_finding(
                rule_id="push_premise_shaken", severity=Severity.HIGH, target_id=entry_id
            )
        ]
        # 不抛错即通过（mark_sent 失败被接住）
        _push_findings_by_severity(findings, fresh_ek._registry)

        # finding 在 queue 但 mark_sent 没成功，status 不是 sent
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 1
        assert rows[0]["status"] != "sent"


# ---------------------------------------------------------------------------
# Mixed findings
# ---------------------------------------------------------------------------


class TestMixedFindings:
    """A batch with low + demote + medium + high produces correct routing."""

    @patch("ego_knowledge.diagnose._create_task_board_task")
    def test_mixed_batch_routing(
        self,
        mock_task_board: MagicMock,
        fresh_ek,
        ek_root,
    ) -> None:
        # 为需要入队的 finding 创建真实 entry（不同 title 避免路径冲突）
        med_id = _ingest_source(fresh_ek, idx=1)
        high_id = _ingest_source(fresh_ek, idx=2)
        findings = [
            _make_finding(rule_id="some_low_rule", severity=Severity.LOW),
            _make_finding(rule_id="action_demote", severity=Severity.MEDIUM),
            _make_finding(
                rule_id="decay_source_context", severity=Severity.MEDIUM, target_id=med_id
            ),
            _make_finding(rule_id="push_premise_shaken", severity=Severity.HIGH, target_id=high_id),
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)

        rows = list_queue(fresh_ek._registry)
        # Only medium + high (2 items), low and demote skipped
        assert len(rows) == 2

        rule_ids = {r["rule_id"] for r in rows}
        assert "some_low_rule" not in rule_ids
        assert "action_demote" not in rule_ids
        assert "decay_source_context" in rule_ids
        assert "push_premise_shaken" in rule_ids

        # task_board called once (for high only)
        mock_task_board.assert_called_once()


# ---------------------------------------------------------------------------
# Empty findings
# ---------------------------------------------------------------------------


class TestEmptyFindings:
    def test_empty_findings_no_side_effects(self, fresh_ek, ek_root) -> None:
        _push_findings_by_severity([], fresh_ek._registry)
        rows = list_queue(fresh_ek._registry)
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# Registry count: 16 registered → low 0 → demote 1 → write queue 15
# ---------------------------------------------------------------------------


class TestRegistryCounts:
    """Verify the final count alignment with spec §6.2."""

    def test_diagnose_rules_count_is_16(self) -> None:
        assert len(_DIAGNOSE_RULES) == 16

    def test_no_queue_rules_count_is_1(self) -> None:
        assert len(_NO_QUEUE_RULES) == 1

    def test_writeable_rules_count(self) -> None:
        """16 - 1 (action_demote) = 15 non-demote rules that can write to queue."""
        non_demote = [rid for rid, _ in _DIAGNOSE_RULES if rid not in _NO_QUEUE_RULES]
        assert len(non_demote) == 15


# ---------------------------------------------------------------------------
# _append_to_ai_session_pending removal verification
# ---------------------------------------------------------------------------


class TestAppendRemoved:
    """Verify _append_to_ai_session_pending has been removed."""

    def test_not_in_doctor_module(self) -> None:
        import ego_knowledge.doctor as doctor_mod

        assert not hasattr(doctor_mod, "_append_to_ai_session_pending")

    def test_not_in_core_module(self) -> None:
        import ego_knowledge.core as core_mod

        assert not hasattr(core_mod, "_append_to_ai_session_pending")

    def test_not_in_cli_module(self) -> None:
        import ego_knowledge.cli as cli_mod

        assert not hasattr(cli_mod, "_append_to_ai_session_pending")

    def test_not_in_diagnose_module(self) -> None:
        import ego_knowledge.diagnose as diagnose_mod

        assert not hasattr(diagnose_mod, "_append_to_ai_session_pending")
