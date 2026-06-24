"""task_411da53a4e03：``--scan-sources`` 扩展扫描边界测试。

Phase 2 遗留债务：sources/ 下 9 个 frontmatter 全角结构标点（docs/ 6 +
imports/ 3）。spec.md 真源边界默认 ``entries/`` 唯一可修复；本测试锁定
``scan_sources=True`` 时扫描边界扩展到 ``sources/{docs,imports}/``，
且不破坏默认行为（sources/web 等不可逆素材不进扫描）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    normalize_legacy_apply,
    normalize_legacy_dry_run,
    normalize_legacy_restore,
    read_manifest_full,
)

from ._normalize_legacy_helpers import _dirty_fm, _write_entry, _write_fake_manifest


class TestScanSourcesBoundary:
    """``scan_sources`` 扩展扫描边界的契约。"""

    def test_dry_run_scans_sources_when_flag_enabled(self, tmp_path: Path) -> None:
        """``scan_sources=True``：sources/{docs,imports}/ 下 dirty 进扫描。"""

        data_root = tmp_path
        # canonical 校验要求 data_root 含 entries/（即使本测试只验 sources）
        (data_root / "entries").mkdir()
        _write_entry(
            data_root / "sources" / "docs" / "2026" / "a.md",
            fm_text=_dirty_fm(),
        )
        _write_entry(
            data_root / "sources" / "imports" / "b.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root, scan_sources=True)

        assert report.would_change == 2
        paths = {c.path for c in report.changes}
        assert any("sources/docs/" in p for p in paths)
        assert any("sources/imports/" in p for p in paths)

    def test_dry_run_skips_sources_by_default(self, tmp_path: Path) -> None:
        """``scan_sources=False``（默认）：sources/ 不进扫描（spec 真源边界）。"""

        data_root = tmp_path
        _write_entry(
            data_root / "sources" / "docs" / "a.md",
            fm_text=_dirty_fm(),
        )
        # entries/ 下放一个 dirty 让 scanned > 0，验证 sources 被排除
        _write_entry(
            data_root / "entries" / "notes" / "in.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root)

        assert report.would_change == 1
        assert all("sources/" not in c.path for c in report.changes)

    def test_scan_sources_excludes_github_and_web(self, tmp_path: Path) -> None:
        """``scan_sources=True`` 时 sources/github 与 sources/web 不进扫描。

        Phase 2 实测债务只在 sources/{docs,imports}/；github/、web/ 是
        外部抓取的不可逆素材，结构标点修复风险高，不纳入扩展范围。
        """

        data_root = tmp_path
        (data_root / "entries").mkdir()
        _write_entry(
            data_root / "sources" / "github" / "a.md",
            fm_text=_dirty_fm(),
        )
        _write_entry(
            data_root / "sources" / "web" / "b.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root, scan_sources=True)

        assert report.would_change == 0
        assert all("github" not in c.path for c in report.changes)
        assert all("/web" not in c.path for c in report.changes)

    def test_scan_sources_still_scans_entries(self, tmp_path: Path) -> None:
        """``scan_sources=True`` 时 entries/ 仍然扫描（追加不替换）。"""

        data_root = tmp_path
        _write_entry(
            data_root / "entries" / "notes" / "e.md",
            fm_text=_dirty_fm(),
        )
        _write_entry(
            data_root / "sources" / "docs" / "s.md",
            fm_text=_dirty_fm(),
        )

        report = normalize_legacy_dry_run(data_root, scan_sources=True)

        assert report.would_change == 2
        assert any("entries/" in c.path for c in report.changes)
        assert any("sources/docs/" in c.path for c in report.changes)


class TestScanSourcesApplyRestore:
    """``scan_sources=True`` 时 apply/restore 的备份与恢复契约。"""

    def test_apply_scan_sources_round_trip_idempotent(
        self, tmp_path: Path
    ) -> None:
        """apply(scan_sources=True) → 复跑 dry-run(scan_sources=True) 幂等。"""

        data_root = tmp_path
        (data_root / "entries").mkdir()
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-sources-idem"
        _write_entry(
            data_root / "sources" / "docs" / "a.md",
            fm_text=_dirty_fm(),
        )
        _write_entry(
            data_root / "sources" / "imports" / "b.md",
            fm_text=_dirty_fm(),
        )

        normalize_legacy_apply(data_root, backup_dir, scan_sources=True)

        report = normalize_legacy_dry_run(data_root, scan_sources=True)
        assert report.would_change == 0

    def test_apply_scan_sources_writes_flag_into_manifest(
        self, tmp_path: Path
    ) -> None:
        """apply(scan_sources=True) 把 scan_sources=True 写入 manifest。

        restore 不传 scan_sources，靠 manifest 字段决定允许的 target 子树。
        """

        data_root = tmp_path
        (data_root / "entries").mkdir()
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-manifest-flag"
        _write_entry(
            data_root / "sources" / "docs" / "a.md",
            fm_text=_dirty_fm(),
        )

        normalize_legacy_apply(data_root, backup_dir, scan_sources=True)

        manifest_path = backup_dir / "normalize-legacy-manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert payload["scan_sources"] is True
        # 单次 read_manifest_full 同时拿 (entries, scan_sources)，避免双次 IO
        entries, scan_sources = read_manifest_full(backup_dir)
        assert scan_sources is True
        # read_manifest 应接受 sources/docs/ 前缀（scan_sources=True 时允许）
        assert len(entries) == 1
        assert entries[0]["relative_path"].startswith("sources/docs/")

    def test_apply_default_writes_scan_sources_false_into_manifest(
        self, tmp_path: Path
    ) -> None:
        """apply()（默认）把 scan_sources=False 写入 manifest。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-manifest-default"
        _write_entry(
            data_root / "entries" / "notes" / "a.md",
            fm_text=_dirty_fm(),
        )

        normalize_legacy_apply(data_root, backup_dir)

        # 单次 read_manifest_full 同时拿 (entries, scan_sources)，避免双次 IO
        entries, scan_sources = read_manifest_full(backup_dir)
        assert scan_sources is False
        assert len(entries) == 1

    def test_restore_scan_sources_round_trip(self, tmp_path: Path) -> None:
        """apply(scan_sources=True) → restore 完整恢复 sources/docs 与 entries。"""

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-sources-restore"
        entries_p = data_root / "entries" / "notes" / "e.md"
        sources_p = data_root / "sources" / "docs" / "2026" / "s.md"
        _write_entry(entries_p, fm_text=_dirty_fm())
        _write_entry(sources_p, fm_text=_dirty_fm())
        original_entries = entries_p.read_text(encoding="utf-8")
        original_sources = sources_p.read_text(encoding="utf-8")

        normalize_legacy_apply(data_root, backup_dir, scan_sources=True)
        normalize_legacy_restore(backup_dir, data_root)

        assert entries_p.read_text(encoding="utf-8") == original_entries
        assert sources_p.read_text(encoding="utf-8") == original_sources

    def test_read_manifest_rejects_sources_prefix_when_scan_sources_false(
        self, tmp_path: Path
    ) -> None:
        """manifest scan_sources=False 时 sources/docs/ 前缀被拒（防越界）。

        即使攻击者篡改 manifest 注入 sources/docs/x.md，read_manifest_full 在
        scan_sources=False 时拒绝该前缀，restore 不会写 sources 子树。
        """

        backup_dir = tmp_path / "backup-tampered"
        _write_fake_manifest(
            backup_dir,
            entries=[
                {
                    "relative_path": "sources/docs/x.md",
                    "sha256": "sha256:" + "0" * 64,
                }
            ],
            scan_sources=False,
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest_full(backup_dir)

        assert "允许前缀" in str(excinfo.value) or "entries/" in str(excinfo.value)

    def test_read_manifest_rejects_invalid_scan_sources_type(
        self, tmp_path: Path
    ) -> None:
        """manifest.scan_sources 非 bool 类型时拒绝（防篡改注入）。"""

        backup_dir = tmp_path / "backup-bad-flag"
        _write_fake_manifest(
            backup_dir,
            entries=[
                {"relative_path": "entries/x.md", "sha256": "sha256:" + "0" * 64}
            ],
            scan_sources="yes",  # 非 bool
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest_full(backup_dir)

        assert "scan_sources" in str(excinfo.value)

    def test_read_manifest_accepts_missing_scan_sources_field(
        self, tmp_path: Path
    ) -> None:
        """旧 manifest 缺 scan_sources 字段：视为 False（向后兼容）。

        Phase 2.2 已写入的备份不含 scan_sources 字段，restore 必须仍能读。
        """

        backup_dir = tmp_path / "backup-legacy"
        _write_fake_manifest(
            backup_dir,
            entries=[
                {"relative_path": "entries/x.md", "sha256": "sha256:" + "0" * 64}
            ],
            # scan_sources 默认 "missing"：不写该字段
        )

        # 单次 read_manifest_full 同时拿 (entries, scan_sources)，避免双次 IO
        entries, scan_sources = read_manifest_full(backup_dir)
        assert scan_sources is False
        assert len(entries) == 1
