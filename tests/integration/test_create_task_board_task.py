"""Integration tests for _create_task_board_task (Phase 3, Task 3.6).

Tests mock subprocess.run to verify cwd / argv / payload shape without
actually invoking the task-board Node CLI.

P0.4 调整：``_TASK_BOARD_CLI_DIR`` 模块级常量改为惰性求值函数
``_resolve_task_board_dir()``（读 ``EK_TASK_BOARD_DIR`` env，未配置返回
``None``）。测试相应改为通过 autouse fixture 注入「已配置」场景，
并补「未配置→graceful skip」用例（断言抛 ``StorageError``）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ego_knowledge.doctor import (
    Finding,
    Severity,
    _category_from_rule,
    _create_task_board_task,
)
from ego_knowledge.errors import StorageError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _high_finding(
    rule_id: str = "decay_source_context",
    *,
    target_path: str | None = "/some/path.md",
    message: str = "上游变化未同步 context_ref",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=Severity.HIGH,
        target_id="ek_not_01HABC",
        target_path=target_path,
        message=message,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateTaskBoardTask:
    """Verify subprocess invocation, cwd, argv, and payload structure."""

    @pytest.fixture(autouse=True)
    def _configured_task_board_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
        """autouse：默认让 _resolve_task_board_dir 返回真实存在的 tmp 目录。

        happy-path 测试无需显式声明；需要「未配置」或「目录缺失」的测试在
        方法内重新 monkeypatch 覆盖即可（pytest 共享同一 monkeypatch 实例）。
        """
        real_dir = tmp_path / "task-board"
        real_dir.mkdir()
        monkeypatch.setattr(
            "ego_knowledge.doctor._resolve_task_board_dir",
            lambda: real_dir,
        )
        return real_dir

    @patch("ego_knowledge.doctor.subprocess.run")
    @patch("ego_knowledge.doctor.shutil.which", return_value="/usr/bin/node")
    def test_high_finding_to_task_board(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
        _configured_task_board_dir: Path,
    ) -> None:
        """High finding triggers subprocess with correct cwd and argv."""
        # Capture call info via side_effect before temp file is deleted
        captured: dict = {}

        def side_effect(argv: list[str], **kwargs: object) -> None:
            captured["argv"] = argv
            captured["kwargs"] = kwargs

        mock_run.side_effect = side_effect

        finding = _high_finding()
        _create_task_board_task(finding)

        mock_run.assert_called_once()

        # cwd points to task-board directory resolved from env
        assert captured["kwargs"]["cwd"] == _configured_task_board_dir

        argv = captured["argv"]
        assert argv[0] == "/usr/bin/node"
        assert argv[1] == "src/cli.mjs"
        assert argv[2] == "upsert"
        assert argv[3] == "--file"
        assert argv[5] == "--json"

        # env inherits os.environ + NODE_NO_WARNINGS
        env = captured["kwargs"]["env"]
        assert env.get("NODE_NO_WARNINGS") == "1"
        assert "PATH" in env

    @patch("ego_knowledge.doctor.subprocess.run")
    @patch("ego_knowledge.doctor.shutil.which", return_value="/usr/bin/node")
    def test_payload_camelcase_fields(self, mock_which: MagicMock, mock_run: MagicMock) -> None:
        """Payload uses camelCase field names required by task-board CLI."""
        captured_payload: dict = {}

        def side_effect(argv: list[str], **kwargs: object) -> None:
            payload_path = argv[4]
            with open(payload_path) as fh:
                captured_payload.update(json.load(fh))

        mock_run.side_effect = side_effect

        finding = _high_finding(target_path="/docs/entry.md")
        _create_task_board_task(finding)

        # camelCase fields
        assert "docRefs" in captured_payload
        assert "ownerKind" in captured_payload
        # snake_case fields should NOT exist
        assert "doc_refs" not in captured_payload
        assert "owner_kind" not in captured_payload

        # Basic shape
        assert captured_payload["kind"] == "task"
        assert captured_payload["priority"] == "high"
        assert captured_payload["ownerKind"] == "agent"
        assert captured_payload["docRefs"] == ["/docs/entry.md"]
        assert "[diagnose]" in captured_payload["title"]

    @patch("ego_knowledge.doctor.shutil.which", return_value=None)
    def test_node_not_found_raises_storage(self, mock_which: MagicMock) -> None:
        """Missing node binary raises StorageError."""
        finding = _high_finding()
        with pytest.raises(StorageError, match="node 不在 PATH"):
            _create_task_board_task(finding)

    @patch("ego_knowledge.doctor.subprocess.run")
    @patch("ego_knowledge.doctor.shutil.which", return_value="/usr/bin/node")
    def test_target_path_none_empty_docrefs(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """target_path=None results in empty docRefs array."""
        captured_payload: dict = {}

        def side_effect(argv: list[str], **kwargs: object) -> None:
            payload_path = argv[4]
            with open(payload_path) as fh:
                captured_payload.update(json.load(fh))

        mock_run.side_effect = side_effect

        finding = _high_finding(target_path=None)
        _create_task_board_task(finding)

        assert captured_payload["docRefs"] == []

    def test_task_board_dir_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Missing task-board directory raises StorageError."""
        finding = _high_finding()
        nonexistent = tmp_path / "no-such-dir" / "task-board"
        monkeypatch.setattr(
            "ego_knowledge.doctor._resolve_task_board_dir",
            lambda: nonexistent,
        )
        with pytest.raises(StorageError, match="task-board 目录不存在"):
            _create_task_board_task(finding)

    def test_unconfigured_raises_storage(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """未配置 EK_TASK_BOARD_DIR 时 _resolve_task_board_dir 返回 None，
        _create_task_board_task 必须抛 StorageError（由调用方接住降级）。
        """
        monkeypatch.setattr(
            "ego_knowledge.doctor._resolve_task_board_dir",
            lambda: None,
        )
        finding = _high_finding()
        with pytest.raises(StorageError, match="task-board 集成未配置"):
            _create_task_board_task(finding)

    # -----------------------------------------------------------------------
    # P0.4 W5：subprocess 异常 → StorageError 转换的三条降级路径
    # （这些转换是 diagnose graceful skip 的核心保证，必须有测试保护）
    # -----------------------------------------------------------------------

    @patch("ego_knowledge.doctor.subprocess.run")
    @patch("ego_knowledge.doctor.shutil.which", return_value="/usr/bin/node")
    def test_subprocess_timeout_raises_storage(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """subprocess.TimeoutExpired 必须转成 StorageError 由 diagnose 接住。"""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["node"], timeout=10)
        finding = _high_finding()
        with pytest.raises(StorageError, match="task-board upsert 超时"):
            _create_task_board_task(finding)

    @patch("ego_knowledge.doctor.subprocess.run")
    @patch("ego_knowledge.doctor.shutil.which", return_value="/usr/bin/node")
    def test_subprocess_called_process_error_raises_storage(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """subprocess.CalledProcessError 必须转成 StorageError 由 diagnose 接住。"""
        mock_run.side_effect = subprocess.CalledProcessError(returncode=1, cmd=["node"])
        finding = _high_finding()
        with pytest.raises(StorageError, match=r"task-board upsert 失败 \(exit 1\)"):
            _create_task_board_task(finding)

    @patch("ego_knowledge.doctor.subprocess.run")
    @patch("ego_knowledge.doctor.shutil.which", return_value="/usr/bin/node")
    def test_subprocess_os_error_raises_storage(
        self,
        mock_which: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        """OS 级异常（race condition 下 node 被删/权限变更）必须转成 StorageError，
        否则会逃出 diagnose 的 ``except StorageError`` 阻断主流程。
        """
        mock_run.side_effect = FileNotFoundError("[Errno 2] No such file or directory: node")
        finding = _high_finding()
        with pytest.raises(StorageError, match="task-board subprocess 调用失败"):
            _create_task_board_task(finding)


class TestCategoryMapping:
    """Verify rule_id → category mapping."""

    def test_decay_rules_map_to_fix(self) -> None:
        assert _category_from_rule("decay_source_context") == "fix"
        assert _category_from_rule("decay_note_stagnant") == "fix"
        assert _category_from_rule("decay_concept_internal_split") == "fix"

    def test_push_premise_shaken_maps_to_fix(self) -> None:
        assert _category_from_rule("push_premise_shaken") == "fix"

    def test_structure_orphan_decision_maps_to_fix(self) -> None:
        assert _category_from_rule("structure_orphan_decision") == "fix"

    def test_action_rules_map_to_refine(self) -> None:
        assert _category_from_rule("action_promote") == "refine"
        assert _category_from_rule("action_split") == "refine"
        assert _category_from_rule("action_retract") == "refine"

    def test_push_crystallize_maps_to_refine(self) -> None:
        assert _category_from_rule("push_crystallize") == "refine"

    def test_push_internal_split_maps_to_refine(self) -> None:
        assert _category_from_rule("push_internal_split") == "refine"

    def test_unknown_rule_defaults_to_fix(self) -> None:
        assert _category_from_rule("some_unknown_rule") == "fix"
