"""任务 2.1：apply 契约测试。"""

from __future__ import annotations

import errno
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    NormalizeReport,
    normalize_legacy_apply,
)

from ._normalize_legacy_helpers import _clean_fm, _dirty_fm, _frontmatter_of, _write_entry


class TestNormalizeLegacyApply:
    """apply：备份 + 修复 + 幂等。"""

    def test_apply_writes_fixed_content(self, tmp_path: Path) -> None:
        """apply 后文件 frontmatter 已转半角，body 原样保留。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-apply-write"
        entry = _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
            body="正文，保留中文标点：和句号。",
        )
        original_body = entry.read_text(encoding="utf-8").split("---\n", 2)[2]

        normalize_legacy_apply(data_root, backup_dir)

        applied = entry.read_text(encoding="utf-8")
        # 全角冒号/全角空格已修
        assert "：" not in _frontmatter_of(applied)
        assert "\u3000" not in _frontmatter_of(applied)
        # body 完全保留（包括合法中文标点）
        assert applied.endswith(original_body)

    def test_apply_creates_backup_with_original_content(self, tmp_path: Path) -> None:
        """apply 备份保存的是原始（未修复）内容，可被 restore 直接使用。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-apply-backup"
        entry = _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )
        original = entry.read_text(encoding="utf-8")

        normalize_legacy_apply(data_root, backup_dir)

        backup_path = backup_dir / "entries" / "notes" / "dirty.md"
        assert backup_path.exists()
        assert backup_path.read_text(encoding="utf-8") == original

    def test_apply_does_not_backup_clean_files(self, tmp_path: Path) -> None:
        """干净条目不入备份（备份只含 would_change > 0 的文件）。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-apply-clean"
        _write_entry(
            data_root / "entries" / "notes" / "clean.md",
            fm_text=_clean_fm(),
        )

        report = normalize_legacy_apply(data_root, backup_dir)

        assert report.would_change == 0
        assert not backup_dir.exists() or not any(backup_dir.rglob("*.md"))

    def test_apply_rejects_existing_nonempty_backup_dir(self, tmp_path: Path) -> None:
        """backup_dir 已存在且非空时拒绝 apply，防止覆盖既有备份。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-apply-conflict"
        backup_dir.mkdir(parents=True)
        (backup_dir / "stale.txt").write_text("previous backup", encoding="utf-8")
        _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_apply(data_root, backup_dir)

        # 错误信息可定位（玻璃盒：说清楚是哪个 backup_dir 冲突）
        assert "backup" in str(excinfo.value).lower()
        assert str(backup_dir) in str(excinfo.value)

    def test_apply_rejects_unsafe_data_root(self, tmp_path: Path) -> None:
        """data_root 是文件系统根 / 仓库根时拒绝（防误伤）。"""

        backup_dir = tmp_path / "backup"

        with pytest.raises(NormalizeLegacyError):
            normalize_legacy_apply(Path("/"), backup_dir)

    def test_apply_returns_report(self, tmp_path: Path) -> None:
        """apply 返回与 dry-run 同结构的 NormalizeReport，便于复跑对比。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-apply-report"
        _write_entry(
            data_root / "entries" / "notes" / "a.md",
            fm_text=_dirty_fm(title="测：１"),
        )
        _write_entry(
            data_root / "entries" / "notes" / "b.md",
            fm_text=_dirty_fm(title="测：２"),
        )

        report = normalize_legacy_apply(data_root, backup_dir)

        assert isinstance(report, NormalizeReport)
        assert report.would_change == 2
        assert {c.path.rsplit("/", 1)[-1] for c in report.changes} == {"a.md", "b.md"}

    def test_apply_batch_rolls_back_on_write_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """多文件批处理中，第二个文件写回失败时，第一个已写文件必须回滚到原文。

        QA 严审 R1：apply 缺批量回滚会让真实 67 条修复留下半成功中间态。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-batch-rollback"
        entry_a = _write_entry(
            data_root / "entries" / "notes" / "a.md",
            fm_text=_dirty_fm(title="测：１"),
        )
        entry_b = _write_entry(
            data_root / "entries" / "notes" / "b.md",
            fm_text=_dirty_fm(title="测：２"),
        )
        original_a = entry_a.read_text(encoding="utf-8")
        original_b = entry_b.read_text(encoding="utf-8")

        original_write: Callable[..., int] = Path.write_text

        def failing_write(self: Path, content: str, *args: Any, **kwargs: Any) -> int:
            if self == entry_b:
                raise OSError(errno.EROFS, "mocked read-only filesystem")
            return original_write(self, content, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", failing_write)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_apply(data_root, backup_dir)

        # 错误消息含 partial rollback 上下文（已回滚 1/2）
        assert "已回滚 1/2" in str(excinfo.value)
        # a 写过又被回滚 → 内容与原文一致
        assert entry_a.read_text(encoding="utf-8") == original_a
        # b 从未写成功 → 内容保持原文
        assert entry_b.read_text(encoding="utf-8") == original_b

    def test_apply_rollback_message_on_double_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """写回 + 回滚都失败时，错误消息必须含「请从 backup 手动恢复」。

        QA code-review R1：原消息「已尝试回滚」无法区分回滚成功/失败两种状态。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-double-fail"
        entry = _write_entry(
            data_root / "entries" / "notes" / "x.md",
            fm_text=_dirty_fm(),
        )

        original_write: Callable[..., int] = Path.write_text

        def always_fail_on_data_root(self: Path, content: str, *args: Any, **kwargs: Any) -> int:
            if self == entry:
                raise OSError(errno.EROFS, "mocked read-only filesystem")
            return original_write(self, content, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", always_fail_on_data_root)

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_apply(data_root, backup_dir)

        # 单文件场景：写回+回滚都失败 → 错误消息明确指出原文件可能损坏
        msg = str(excinfo.value)
        assert "请从 backup 手动恢复" in msg
        assert "原文件可能损坏" in msg
