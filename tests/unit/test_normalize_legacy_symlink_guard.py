"""task_411da53a4e03 follow-up：symlink / resolve 越界写护栏测试。

qa-strict R1（issue 2）：apply 写回路径原本只靠 restore 端的 resolve 校验
兜底，扫描阶段若遇 symlink 会跟随 symlink 读 + 写，可能把 apply 的写入导向
data_root 外部文件（spec 真源边界突破）。

修复后扫描阶段与写回阶段共用 ``_assert_path_within_allowed_roots``：

- 拒绝 ``md_path.is_symlink()``（symlink 直接拒绝）。
- ``md_path.resolve().relative_to(allowed_root)`` 校验落点（兜底父目录 symlink）。

本模块覆盖：entries 与 sources/docs 两类子树、dry-run 与 apply 两种入口、
合法普通文件仍可 apply/restore round-trip 的回归保护。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    normalize_legacy_apply,
    normalize_legacy_dry_run,
)

from ._normalize_legacy_helpers import _dirty_fm, _write_entry


@pytest.fixture
def _no_symlink_privilege_check() -> None:
    """若运行环境无 symlink 权限，跳过本模块所有测试。"""

    probe = Path("/tmp/_normalize_legacy_symlink_probe")
    target = Path("/tmp/_normalize_legacy_symlink_probe_target")
    try:
        target.write_text("probe", encoding="utf-8")
        probe.unlink(missing_ok=True)
        os.symlink(target, probe)
    except (OSError, NotImplementedError):
        pytest.skip("current environment cannot create symlinks")
    finally:
        probe.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


class TestRejectSymlinkEscape:
    """qa-strict R1：扫描与写回阶段拒绝 symlink 越界写。"""

    def test_dry_run_rejects_symlink_in_entries(
        self,
        tmp_path: Path,
        _no_symlink_privilege_check: None,
    ) -> None:
        """``entries/link.md`` 指向 data_root 外部文件 → dry-run 抛错。

        扫描阶段拒绝 symlink：``rglob`` / ``is_file`` / ``read_text`` 都跟随
        symlink，单纯 resolve 校验不足以阻断通过 symlink 读外部内容或写入
        外部文件，扫描阶段直接拒绝最干净。
        """

        data_root = tmp_path
        outside = tmp_path.parent / f"outside-{tmp_path.name}-entries"
        outside.write_text("---\nkind：note\n---\nbody", encoding="utf-8")

        entries_dir = data_root / "entries" / "notes"
        entries_dir.mkdir(parents=True)
        (entries_dir / "link.md").symlink_to(outside)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_dry_run(data_root)

        msg = str(excinfo.value)
        assert "符号链接" in msg or "symlink" in msg.lower()
        assert "link.md" in msg

    def test_dry_run_rejects_symlink_in_sources_docs(
        self,
        tmp_path: Path,
        _no_symlink_privilege_check: None,
    ) -> None:
        """``sources/docs/link.md`` 指向 data_root 外部 → dry-run(scan_sources) 抛错。

        scan_sources=True 时 sources/docs 子树同样校验 symlink。
        """

        data_root = tmp_path
        (data_root / "entries").mkdir()
        outside = tmp_path.parent / f"outside-{tmp_path.name}-sources"
        outside.write_text("---\nkind：note\n---\nbody", encoding="utf-8")

        sources_dir = data_root / "sources" / "docs"
        sources_dir.mkdir(parents=True)
        (sources_dir / "link.md").symlink_to(outside)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_dry_run(data_root, scan_sources=True)

        msg = str(excinfo.value)
        assert "符号链接" in msg or "symlink" in msg.lower()
        assert "link.md" in msg

    def test_apply_rejects_symlink_in_entries(
        self,
        tmp_path: Path,
        _no_symlink_privilege_check: None,
    ) -> None:
        """apply 阶段遇到 symlink → 抛错且外部文件未被写入。

        防止 apply 把修复后的 frontmatter 写到 data_root 外部文件，破坏
        spec 真源边界（spec.md:207 唯一可修复对象 = entries/**/*.md）。
        """

        data_root = tmp_path
        outside = tmp_path.parent / f"outside-{tmp_path.name}-apply"
        original_outside = "---\nkind：note\n---\n外部原文"
        outside.write_text(original_outside, encoding="utf-8")

        entries_dir = data_root / "entries" / "notes"
        entries_dir.mkdir(parents=True)
        (entries_dir / "link.md").symlink_to(outside)

        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-symlink"

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_apply(data_root, backup_dir)

        # 外部文件未被改动（扫描阶段拦截，未进 apply 写回）
        assert outside.read_text(encoding="utf-8") == original_outside
        msg = str(excinfo.value)
        assert "符号链接" in msg or "symlink" in msg.lower()

    def test_apply_rejects_symlink_in_sources_docs(
        self,
        tmp_path: Path,
        _no_symlink_privilege_check: None,
    ) -> None:
        """apply(scan_sources=True) 阶段遇到 sources/docs symlink → 抛错。"""

        data_root = tmp_path
        (data_root / "entries").mkdir()
        outside = tmp_path.parent / f"outside-{tmp_path.name}-sources-apply"
        original_outside = "---\nkind：note\n---\n外部原文"
        outside.write_text(original_outside, encoding="utf-8")

        sources_dir = data_root / "sources" / "docs"
        sources_dir.mkdir(parents=True)
        (sources_dir / "link.md").symlink_to(outside)

        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-sources-symlink"

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_apply(data_root, backup_dir, scan_sources=True)

        assert outside.read_text(encoding="utf-8") == original_outside
        msg = str(excinfo.value)
        assert "符号链接" in msg or "symlink" in msg.lower()

    def test_normal_files_still_round_trip_after_symlink_guard(
        self,
        tmp_path: Path,
        _no_symlink_privilege_check: None,
    ) -> None:
        """回归保护：合法普通文件（非 symlink）apply 仍可正常写回。

        避免护栏误伤：symlink 校验只拦真正的 symlink，普通文件不受影响。
        """

        data_root = tmp_path
        entry = _write_entry(
            data_root / "entries" / "notes" / "normal.md",
            fm_text=_dirty_fm(),
        )
        original = entry.read_text(encoding="utf-8")
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-symlink-guard"

        report = normalize_legacy_apply(data_root, backup_dir)

        assert report.would_change == 1
        # 写回成功，文件被改
        assert entry.read_text(encoding="utf-8") != original
        # frontmatter 全角冒号已转半角
        assert "：" not in entry.read_text(encoding="utf-8").split("---\n", 2)[1]
