"""任务 2.1：安全护栏测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    normalize_legacy_apply,
    normalize_legacy_dry_run,
)

from ._normalize_legacy_helpers import _dirty_fm, _write_entry


class TestNormalizeLegacySafety:
    """显式 data_root 与禁止隐式扫描仓库根的护栏。"""

    def test_dry_run_rejects_nonexistent_data_root(self, tmp_path: Path) -> None:
        """data_root 不存在时报错，不静默返回空报告掩盖配置问题。"""

        with pytest.raises(NormalizeLegacyError):
            normalize_legacy_dry_run(tmp_path / "does-not-exist")

    def test_dry_run_rejects_unsafe_data_root(self, tmp_path: Path) -> None:
        """文件系统根 / 不是合法 data_root。"""

        with pytest.raises(NormalizeLegacyError):
            normalize_legacy_dry_run(Path("/"))

    def test_dry_run_rejects_root_equivalent_path(self, tmp_path: Path) -> None:
        """``Path('/tmp/..')`` 解析后等价于 ``/``，也必须被拒绝。

        QA 严审 R1：原实现仅匹配字面 ``Path('/')``，等价路径绕过护栏。
        """

        with pytest.raises(NormalizeLegacyError):
            normalize_legacy_dry_run(Path("/tmp/.."))

    def test_dry_run_rejects_repo_root(self, tmp_path: Path) -> None:
        """仓库根（含 .git 目录）不是合法 data_root。

        QA 严审 R1：plan 要求「禁止隐式扫描仓库根」，原实现未识别仓库根形态。
        生产部署 data_root 是用户配置的数据目录，本身不含 .git。
        """

        fake_repo = tmp_path / "fake-repo"
        fake_repo.mkdir()
        (fake_repo / ".git").mkdir()
        (fake_repo / "data").mkdir()  # 仓库根常见结构

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_dry_run(fake_repo)

        assert "仓库根" in str(excinfo.value) or ".git" in str(excinfo.value)

    def test_dry_run_rejects_data_root_without_entries(self, tmp_path: Path) -> None:
        """普通目录（无 entries/）不是合法 data_root。

        QA 严审 R2：原护栏只拒绝 root/不存在/非目录/.git 仓库根；普通 tmp 目录
        会被当成合法 data_root 并静默返回空报告，让 Phase 2.2 批量修复误判
        「无需修复」。canonical EgoKnowledge 数据根必须含 ``entries/`` 子目录。
        """

        ordinary = tmp_path / "ordinary"
        ordinary.mkdir()
        (ordinary / "some-other-dir").mkdir()

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_dry_run(ordinary)

        msg = str(excinfo.value)
        assert "entries" in msg
        assert "canonical" in msg or "数据根" in msg

    def test_dry_run_rejects_data_root_ancestor(self, tmp_path: Path) -> None:
        """data_root 祖先目录（合法数据根的父目录）必须被拒绝。

        QA 严审 R2：祖先目录含合法数据根与 ``entries/``，
        但自身没有 ``entries/``；脚本会静默返回 would_change=0 让调用方误判。
        """

        # 构造合法数据根 ``<tmp>/knowledge-data`` 含 entries/
        data_root = tmp_path / "knowledge-data"
        _write_entry(
            data_root / "entries" / "notes" / "x.md",
            fm_text=_dirty_fm(),
        )
        ancestor = tmp_path  # data_root.parent，无 entries/

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_dry_run(ancestor)

        assert "entries" in str(excinfo.value)

    def test_dry_run_rejects_repo_internal_non_data_root(self, tmp_path: Path) -> None:
        """仓库内非数据根目录（如 ``<repo>/tools``）必须被拒绝。

        QA 严审 R2：仓库内任意子目录（tools/docs/scripts 等）都非 canonical
        数据根；传入这些路径应被拒绝，避免静默空报告。
        """

        fake_repo = tmp_path / "fake-repo"
        (fake_repo / "tools" / "ego-knowledge").mkdir(parents=True)
        (fake_repo / "data" / "EgoKnowledge" / "entries").mkdir(parents=True)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_dry_run(fake_repo / "tools")

        assert "entries" in str(excinfo.value)

    def test_apply_rejects_data_root_inside_backup_dir(self, tmp_path: Path) -> None:
        """backup_dir 嵌套在 data_root 内时拒绝（防递归写）。"""

        data_root = tmp_path
        backup_dir = data_root / "_backup" / "nested"
        _write_entry(
            data_root / "entries" / "notes" / "x.md",
            fm_text=_dirty_fm(),
        )

        with pytest.raises(NormalizeLegacyError):
            normalize_legacy_apply(data_root, backup_dir)
