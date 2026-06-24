"""任务 2.1：restore 契约测试。"""

from __future__ import annotations

import errno
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    normalize_legacy_apply,
    normalize_legacy_restore,
)

from ._normalize_legacy_helpers import _dirty_fm, _write_entry


class TestNormalizeLegacyRestore:
    """restore：从备份恢复原始内容。"""

    def test_restore_reverts_changes(self, tmp_path: Path) -> None:
        """apply → restore 后，文件内容与 apply 前完全一致。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-restore-revert"
        entry = _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
            body="正文，保留。",
        )
        original = entry.read_text(encoding="utf-8")

        normalize_legacy_apply(data_root, backup_dir)
        # apply 后内容应已变
        assert entry.read_text(encoding="utf-8") != original

        normalize_legacy_restore(backup_dir, data_root)

        assert entry.read_text(encoding="utf-8") == original

    def test_restore_raises_when_backup_missing(self, tmp_path: Path) -> None:
        """备份目录不存在时 restore 报错，不静默成功。"""

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_restore(tmp_path / "nonexistent", tmp_path)

        assert "backup" in str(excinfo.value).lower() or "不存在" in str(excinfo.value)

    def test_restore_round_trip_multiple_files(self, tmp_path: Path) -> None:
        """多文件、嵌套目录的备份/恢复 round-trip 完整。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-restore-multi"
        paths = [
            data_root / "entries" / "notes" / "2026" / "01" / "a.md",
            data_root / "entries" / "concepts" / "tool" / "b.md",
            data_root / "entries" / "decisions" / "2026" / "c.md",
        ]
        originals = []
        for i, p in enumerate(paths):
            _write_entry(p, fm_text=_dirty_fm(title=f"测：{i}"))
            originals.append(p.read_text(encoding="utf-8"))

        normalize_legacy_apply(data_root, backup_dir)
        normalize_legacy_restore(backup_dir, data_root)

        for p, original in zip(paths, originals, strict=True):
            assert p.read_text(encoding="utf-8") == original

    def test_restore_rejects_missing_manifest(self, tmp_path: Path) -> None:
        """backup_dir 缺 manifest 时拒绝恢复（防误用其他 backup_dir）。

        QA 严审 R1 CRITICAL：原实现 rglob('*.md') 会盲扫任何 backup_dir，
        误传路径可越界覆盖 data_root 之外的文件。
        """

        data_root = tmp_path / "data"
        data_root.mkdir()
        (data_root / "entries").mkdir()
        backup_dir = tmp_path / "backup"
        # backup_dir 内有 .md 但无 manifest
        (backup_dir / "entries").mkdir(parents=True)
        (backup_dir / "entries" / "x.md").write_text(
            "---\nkind：bad\n---\nbody",
            encoding="utf-8",
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_restore(backup_dir, data_root)

        assert "manifest" in str(excinfo.value).lower()

    def test_restore_ignores_stray_md_in_backup(self, tmp_path: Path) -> None:
        """backup_dir 含 manifest 之外的 stray.md 时，只恢复 manifest 内条目。

        QA 严审 R1：stray 文件（backup_dir 根级或 sources/ 子树）不应被复制到 data_root。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-stray"
        entry = _write_entry(
            data_root / "entries" / "notes" / "a.md",
            fm_text=_dirty_fm(),
        )
        original = entry.read_text(encoding="utf-8")

        normalize_legacy_apply(data_root, backup_dir)
        # apply 后注入 stray 文件，模拟 backup_dir 被污染
        (backup_dir / "stray.md").write_text("stray at root", encoding="utf-8")
        (backup_dir / "sources").mkdir()
        (backup_dir / "sources" / "out.md").write_text("stray in sources", encoding="utf-8")

        normalize_legacy_restore(backup_dir, data_root)

        # manifest 内条目逐字节恢复
        assert entry.read_text(encoding="utf-8") == original
        # stray 不会被复制到 data_root
        assert not (data_root / "stray.md").exists()
        assert not (data_root / "sources").exists()

    def test_restore_rejects_unsafe_data_root(self, tmp_path: Path) -> None:
        """restore 也复用 _validate_data_root 校验，拒绝文件系统根。"""

        backup_dir = tmp_path / "backup"
        backup_dir.mkdir()
        # manifest 为空也算合法 manifest（被 _validate_data_root 先拒绝）
        (backup_dir / "normalize-legacy-manifest.json").write_text(
            json.dumps(
                {
                    "record_type": "normalize_legacy.backup.manifest/v1",
                    "entry_count": 0,
                    "entries": [],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(NormalizeLegacyError):
            normalize_legacy_restore(backup_dir, Path("/"))

    def test_restore_partial_failure_reports_progress(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """3 文件 restore，第 2 个写失败时，错误消息含「已完成 1/3」上下文。

        QA code-review R1：原错误消息不传达中间态，用户无法判断 data_root 处于何种状态。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-partial"
        paths = [
            data_root / "entries" / "a.md",
            data_root / "entries" / "b.md",
            data_root / "entries" / "c.md",
        ]
        for i, p in enumerate(paths):
            _write_entry(p, fm_text=_dirty_fm(title=f"测：{i}"))

        normalize_legacy_apply(data_root, backup_dir)

        original_write: Callable[..., int] = Path.write_text

        def failing_write(self: Path, content: str, *args: Any, **kwargs: Any) -> int:
            if self == paths[1]:
                raise OSError(errno.EROFS, "mocked read-only filesystem")
            return original_write(self, content, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", failing_write)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_restore(backup_dir, data_root)

        assert "已完成 1/3" in str(excinfo.value)

    def test_restore_wraps_oserror_on_unreadable_backup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """备份读取触发 OSError 时，restore 包装为 NormalizeLegacyError 不逃逸。

        QA code-review R1：原 read_text/mkdir 未 try/except，权限错误逃逸出 main 捕获。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-oserror"
        _write_entry(
            data_root / "entries" / "notes" / "x.md",
            fm_text=_dirty_fm(),
        )
        normalize_legacy_apply(data_root, backup_dir)

        original_read: Callable[..., str] = Path.read_text

        def failing_read(self: Path, *args: Any, **kwargs: Any) -> str:
            if "backup-" in str(self) and "-oserror" in str(self) and self.suffix == ".md":
                raise PermissionError(errno.EACCES, "mocked permission denied")
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", failing_read)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_restore(backup_dir, data_root)

        # 包装后错误消息含「恢复失败」上下文，不是裸 PermissionError
        assert "恢复失败" in str(excinfo.value)
