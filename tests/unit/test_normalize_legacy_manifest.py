"""任务 2.1：manifest 完整性契约测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge.scripts.normalize_legacy import (
    NormalizeLegacyError,
    normalize_legacy_apply,
    normalize_legacy_restore,
)
from ego_knowledge.scripts.normalize_legacy._apply import read_manifest

from ._normalize_legacy_helpers import _dirty_fm, _write_entry, _write_fake_manifest


class TestNormalizeLegacyManifestIntegrity:
    """manifest 强校验：防篡改/部分备份/越界 relative_path。

    QA 严审 R2 warning：原 read_manifest 只校验 record_type 与 entries 是 list，
    未校验 entry_count/sha256/entries/**/*.md 契约，restore 也不比对 backup
    文件 sha256；manifest 被改或 backup 内容被换后仍会覆盖条目真源。
    """

    def test_restore_rejects_tampered_backup_content(self, tmp_path: Path) -> None:
        """apply 后修改 backup 文件内容，restore 必须按 sha256 拒绝。

        场景：恶意/故障把 backup 文件内容替换为错误文本；restore 若不校验
        sha256 会把错误内容写入条目真源，造成不可逆数据损坏。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-tampered"
        entry = _write_entry(
            data_root / "entries" / "notes" / "x.md",
            fm_text=_dirty_fm(),
        )
        normalize_legacy_apply(data_root, backup_dir)

        # 篡改 backup 文件内容（不动 manifest，让 sha256 校验捕获）
        backup_file = backup_dir / "entries" / "notes" / "x.md"
        backup_file.write_text(
            "---\nid: tampered\nkind: bad\n---\n这是被篡改的内容",
            encoding="utf-8",
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            normalize_legacy_restore(backup_dir, data_root)

        msg = str(excinfo.value)
        assert "sha256" in msg.lower()
        # 真源未被覆盖（restore 在写回前已拒绝）
        assert "tampered" not in entry.read_text(encoding="utf-8")

    def test_read_manifest_rejects_entry_count_mismatch(self, tmp_path: Path) -> None:
        """manifest.entry_count 与 entries 实际长度不一致时拒绝。"""

        backup_dir = tmp_path / "backup-count"
        backup_dir.mkdir()
        # entries 有 2 条但 entry_count 声明 1
        _write_fake_manifest(
            backup_dir,
            entries=[
                {"relative_path": "entries/a.md", "sha256": "sha256:" + "0" * 64},
                {"relative_path": "entries/b.md", "sha256": "sha256:" + "0" * 64},
            ],
            entry_count=1,
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert "entry_count" in str(excinfo.value)

    def test_read_manifest_rejects_non_md_relative_path(self, tmp_path: Path) -> None:
        """relative_path 不以 .md 结尾（如 entries/foo.txt）时拒绝。

        防止畸形 manifest 让 restore 把非 markdown 文件复制到 entries 子树。
        """

        backup_dir = tmp_path / "backup-non-md"
        backup_dir.mkdir()
        _write_fake_manifest(
            backup_dir,
            entries=[
                {"relative_path": "entries/foo.txt", "sha256": "sha256:" + "0" * 64},
            ],
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert ".md" in str(excinfo.value)

    def test_read_manifest_rejects_dotdot_escape(self, tmp_path: Path) -> None:
        """relative_path 含 ``..`` 段（如 entries/../sources/x.md）时拒绝。

        防止 manifest 篡改后 restore 写到 entries 子树之外（覆盖 sources 等）。
        """

        backup_dir = tmp_path / "backup-dotdot"
        backup_dir.mkdir()
        _write_fake_manifest(
            backup_dir,
            entries=[
                {
                    "relative_path": "entries/../sources/x.md",
                    "sha256": "sha256:" + "0" * 64,
                },
            ],
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert ".." in str(excinfo.value)

    def test_read_manifest_rejects_absolute_relative_path(self, tmp_path: Path) -> None:
        """relative_path 是绝对路径时拒绝。

        防止 Windows ``C:\\...`` 或 POSIX ``/etc/...`` 越界写。
        """

        backup_dir = tmp_path / "backup-abs"
        backup_dir.mkdir()
        _write_fake_manifest(
            backup_dir,
            entries=[
                {
                    "relative_path": "/etc/passwd",
                    "sha256": "sha256:" + "0" * 64,
                },
            ],
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert "绝对" in str(excinfo.value)

    def test_read_manifest_rejects_non_entries_prefix(self, tmp_path: Path) -> None:
        """relative_path 不以 ``entries/`` 开头时拒绝。

        防止 manifest 篡改后扫描 sources/views/logs 等非真源边界路径。
        """

        backup_dir = tmp_path / "backup-prefix"
        backup_dir.mkdir()
        _write_fake_manifest(
            backup_dir,
            entries=[
                {
                    "relative_path": "sources/web/x.md",
                    "sha256": "sha256:" + "0" * 64,
                },
            ],
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert "entries/" in str(excinfo.value)

    def test_read_manifest_rejects_invalid_sha256_format(self, tmp_path: Path) -> None:
        """sha256 不符 ``sha256:<64hex>`` 格式时拒绝。

        防止畸形 manifest 让 sha 比对变成字符串字面相等，掩盖 backup 损坏。
        """

        backup_dir = tmp_path / "backup-bad-sha"
        backup_dir.mkdir()
        _write_fake_manifest(
            backup_dir,
            entries=[
                {"relative_path": "entries/x.md", "sha256": "not-a-hash"},
            ],
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert "sha256" in str(excinfo.value)

    def test_read_manifest_rejects_non_object_entry(self, tmp_path: Path) -> None:
        """manifest.entries[i] 不是 JSON object 时拒绝。"""

        backup_dir = tmp_path / "backup-non-obj"
        backup_dir.mkdir()
        _write_fake_manifest(
            backup_dir,
            entries=["not-an-object"],
        )

        with pytest.raises(NormalizeLegacyError) as excinfo:
            read_manifest(backup_dir)

        assert "entries[" in str(excinfo.value)

    def test_restore_round_trip_still_passes_after_strict_validation(self, tmp_path: Path) -> None:
        """合法 apply → restore round-trip 在强校验下仍逐字节恢复。

        回归保护：新增的 sha256/entry_count/路径契约校验不能破坏合法路径。
        """

        data_root = tmp_path
        backup_dir = tmp_path.parent / f"backup-{tmp_path.name}-strict-roundtrip"
        paths = [
            data_root / "entries" / "notes" / "2026" / "a.md",
            data_root / "entries" / "concepts" / "tool" / "b.md",
        ]
        originals: list[str] = []
        for i, p in enumerate(paths):
            _write_entry(p, fm_text=_dirty_fm(title=f"测：{i}"))
            originals.append(p.read_text(encoding="utf-8"))

        normalize_legacy_apply(data_root, backup_dir)
        normalize_legacy_restore(backup_dir, data_root)

        for p, original in zip(paths, originals, strict=True):
            assert p.read_text(encoding="utf-8") == original
