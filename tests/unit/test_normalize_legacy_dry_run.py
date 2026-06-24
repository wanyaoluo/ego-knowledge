"""任务 2.1：dry-run 契约测试。"""

from __future__ import annotations

from pathlib import Path

from ego_knowledge.scripts.normalize_legacy import (
    FileChange,
    NormalizeReport,
    normalize_legacy_apply,
    normalize_legacy_dry_run,
)

from ._normalize_legacy_helpers import _clean_fm, _dirty_fm, _write_entry


class TestNormalizeLegacyDryRun:
    """dry-run：扫描报告契约。"""

    def test_dry_run_reports_changes(self, tmp_path: Path) -> None:
        """含全角结构标点 frontmatter 的条目必须被报告为待修复。"""

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "notes" / "2026" / "01" / "dirty.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root)

        assert isinstance(report, NormalizeReport)
        assert report.data_root == data_root
        assert report.scanned >= 1
        assert report.would_change == 1
        assert len(report.changes) == 1
        change = report.changes[0]
        assert isinstance(change, FileChange)
        assert change.path.endswith("dirty.md")
        # diff 摘要必须可定位：至少点出哪个字符被改
        assert change.diff_summary

    def test_dry_run_no_changes_on_clean_data(self, tmp_path: Path) -> None:
        """干净 frontmatter 的条目：would_change == 0，changes 为空。"""

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "concepts" / "tool" / "clean.md",
            fm_text=_clean_fm(),
        )

        report = normalize_legacy_dry_run(data_root)

        assert report.scanned == 1
        assert report.would_change == 0
        assert report.changes == []

    def test_dry_run_idempotent_after_apply(self, tmp_path: Path) -> None:
        """apply 后再跑 dry-run，would_change 必须归零（幂等）。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-idempotent"
        _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )

        normalize_legacy_apply(data_root, backup_dir)
        report = normalize_legacy_dry_run(data_root)

        assert report.would_change == 0
        assert report.changes == []

    def test_dry_run_preserves_body_with_fullwidth(self, tmp_path: Path) -> None:
        """body 中的中文全角标点（，：。）不属修复对象，不进报告。"""

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "notes" / "body-only.md",
            fm_text=_clean_fm(),
            body="正文，含中文标点：和句号。这些应保留。",
        )

        report = normalize_legacy_dry_run(data_root)

        assert report.would_change == 0

    def test_dry_run_only_scans_entries(self, tmp_path: Path) -> None:
        """扫描范围限定 ``entries/``：sources/views 下的 .md 不进报告。

        spec.md 真源边界：唯一可修复对象 = 用户数据根下的 ``entries/**/*.md``。
        """

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "notes" / "in_scope.md",
            fm_text=_dirty_fm(),
        )
        # sources/ 与 views/ 下放含全角 frontmatter 的 .md，不应被扫到
        _write_entry(
            data_root / "sources" / "web" / "out1.md",
            fm_text=_dirty_fm(),
        )
        _write_entry(
            data_root / "views" / "indexes" / "out2.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root)

        assert report.would_change == 1
        assert all("entries" in c.path for c in report.changes)
        assert all("sources" not in c.path for c in report.changes)
        assert all("views" not in c.path for c in report.changes)

    def test_dry_run_handles_empty_entries(self, tmp_path: Path) -> None:
        """entries/ 存在但为空：返回空报告，不抛错。

        QA 严审 R2：``_validate_data_root`` 现要求 canonical data_root 含
        ``entries/`` 子目录；空 entries/ 仍是合法数据根（新装未 ingest 场景），
        不应被拒绝，但缺失 entries/ 的普通目录必须拒绝（见拒绝路径测试）。
        """

        data_root = tmp_path
        (data_root / "entries").mkdir()

        report = normalize_legacy_dry_run(data_root)

        assert report.scanned == 0
        assert report.would_change == 0
        assert report.changes == []

    def test_dry_run_skips_files_without_frontmatter(self, tmp_path: Path) -> None:
        """无 frontmatter 边界的 .md 不视为可修复条目，跳过不抛错。"""

        bad = tmp_path / "entries" / "notes" / "no-fm.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("这只是正文，没有 frontmatter。", encoding="utf-8")

        report = normalize_legacy_dry_run(tmp_path)

        # 不抛错；但也不计入 scanned（无法解析 frontmatter 的不算条目）
        assert report.would_change == 0

    def test_dry_run_reports_changed_fields(self, tmp_path: Path) -> None:
        """plan 验收口径：报告每个 change 必须含 changed_fields，且能识别实际字段名。"""

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "notes" / "dirty.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root)

        assert report.would_change == 1
        change = report.changes[0]
        assert isinstance(change, FileChange)
        # _dirty_fm 的 kind 行（全角冒号）、tags 行（全角冒号）、
        # title 行（含全角冒号 + 全角空格）都被改 → 字段名可被解析出来。
        assert "kind" in change.changed_fields
        assert "tags" in change.changed_fields
        assert "title" in change.changed_fields
