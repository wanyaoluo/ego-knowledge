"""任务 2.1：CLI 入口测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ego_knowledge.scripts.normalize_legacy import main

from ._normalize_legacy_helpers import _dirty_fm, _write_entry


class TestNormalizeLegacyCLI:
    """``main`` 入口的模式解析与 payload 结构。"""

    def test_cli_rejects_flag_and_positional_mode_conflict(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``--flag`` 与位置子命令同时存在时报错退出，不进入任何模式分支。

        QA 严审 R1：原实现按 flag 优先级分支，``--restore apply`` 会跑 apply，
        用户以为在恢复却触发了写入，高风险脚本的命令意图必须唯一。
        """

        backup_dir = tmp_path / "backup"
        cases = [
            ["--dry-run", "apply", "--data-root", str(tmp_path)],
            ["--apply", "dry-run", "--data-root", str(tmp_path)],
            [
                "--restore",
                "apply",
                "--data-root",
                str(tmp_path),
                "--backup-dir",
                str(backup_dir),
            ],
        ]
        for argv in cases:
            with pytest.raises(SystemExit) as excinfo:
                main(argv)
            assert excinfo.value.code != 0
            captured = capsys.readouterr()
            assert "互斥" in captured.err

    def test_cli_dry_run_payload_includes_changed_fields(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """dry-run JSON payload 每个 change 必须含 changed_fields 字段。

        QA 严审 R1：plan 要求报告含「变更字段」，原 payload 只输出 path + diff_summary。
        """

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )

        rc = main(["dry-run", "--data-root", str(data_root)])

        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is True
        assert payload["would_change"] == 1
        change = payload["changes"][0]
        assert "changed_fields" in change
        assert "kind" in change["changed_fields"]
        assert "tags" in change["changed_fields"]
