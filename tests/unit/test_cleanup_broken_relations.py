"""Phase 2.3：AI 断裂关系清理脚本契约测试。"""

from __future__ import annotations

import errno
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ego_knowledge.core import EgoKnowledge
from ego_knowledge.frontmatter import read_file, write_file
from ego_knowledge.scripts.cleanup_broken_relations import (
    CleanupBrokenRelationsError,
    cleanup_broken_relations_apply,
    cleanup_broken_relations_dry_run,
    cleanup_broken_relations_restore,
    main,
)
from ego_knowledge.scripts.cleanup_broken_relations._apply import _MANIFEST_RECORD_TYPE

from ._doctor_helpers import insert_broken_relation
from .support import concept_payload, source_payload

MISSING_AI = "ek_con_" + "01K" + "0" * 23
MISSING_CONFIRMED = "ek_con_" + "01K" + "0" * 22 + "1"


def _manifest_path(backup_dir: Path) -> Path:
    return backup_dir / "cleanup-broken-relations-manifest.json"


def _read_manifest_payload(backup_dir: Path) -> dict[str, Any]:
    payload = json.loads(_manifest_path(backup_dir).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _write_manifest_payload(backup_dir: Path, payload: dict[str, Any]) -> None:
    _manifest_path(backup_dir).write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_empty_manifest(backup_dir: Path) -> None:
    backup_dir.mkdir(parents=True)
    _write_manifest_payload(
        backup_dir,
        {
            "record_type": _MANIFEST_RECORD_TYPE,
            "entry_count": 0,
            "entries": [],
            "registry_backup": None,
        },
    )


def _inject_relations(data_root: Path, path: Path, relations: list[dict[str, object]]) -> None:
    """绕过当前写入通道目标存在校验，模拟存量历史断裂关系。"""

    if not path.is_absolute():
        path = data_root / path
    frontmatter, body = read_file(str(path))
    frontmatter["relations"] = relations
    write_file(str(path), frontmatter, body)


def _relation_count(
    db_path: Path,
    source_id: str,
    target_id: str,
    rel_type: str,
    origin: str,
) -> int:
    """用独立连接验证 registry 文件状态，避免复用 fixture 连接缓存。"""

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT COUNT(*)
              FROM relations
             WHERE source_id = ?
               AND target_id = ?
               AND type = ?
               AND origin = ?
            """,
            (source_id, target_id, rel_type, origin),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return int(row[0])


def test_dry_run_classifies_ai_and_confirmed_broken_relations(fresh_ek: EgoKnowledge) -> None:
    """dry-run 输出 source/target/type/origin，且 confirmed 只进入裁决清单。"""

    source = fresh_ek.ingest("source", source_payload(title="关系来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="断裂关系概念"),
    )
    _inject_relations(
        fresh_ek._data_root,
        Path(concept.file_path or ""),
        [
            {
                "target": MISSING_AI,
                "type": "related",
                "source": "ai_suggested",
            },
            {
                "target": MISSING_CONFIRMED,
                "type": "depends_on",
                "source": "confirmed",
            },
        ],
    )

    report = cleanup_broken_relations_dry_run(fresh_ek._data_root)

    broken_keys = {(r.source_id, r.target, r.type, r.origin) for r in report.broken_relations}
    assert (
        concept.id,
        MISSING_AI,
        "related",
        "ai_suggested",
    ) in broken_keys
    assert (
        concept.id,
        MISSING_CONFIRMED,
        "depends_on",
        "confirmed",
    ) in broken_keys
    assert report.ai_cleanup_count == 1
    assert len(report.confirmed_adjudication) == 1
    assert report.confirmed_adjudication[0].target == MISSING_CONFIRMED


def test_apply_removes_only_ai_relations_and_keeps_entries(
    fresh_ek: EgoKnowledge,
    tmp_path: Path,
) -> None:
    """apply 只删 AI 断裂关系边，不删 source entry，不删 confirmed 裁决边。"""

    source = fresh_ek.ingest("source", source_payload(title="关系来源2"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="断裂关系概念2"),
    )
    _inject_relations(
        fresh_ek._data_root,
        Path(concept.file_path or ""),
        [
            {
                "target": MISSING_AI,
                "type": "related",
                "source": "ai_confirmed",
            },
            {
                "target": MISSING_CONFIRMED,
                "type": "depends_on",
                "source": "confirmed",
            },
        ],
    )
    backup_dir = tmp_path / "relations-backup"
    entry_path = Path(concept.file_path or "")
    if not entry_path.is_absolute():
        entry_path = fresh_ek._data_root / entry_path
    original_body = entry_path.read_text(encoding="utf-8").split("---\n", 2)[2]

    report = cleanup_broken_relations_apply(fresh_ek._data_root, backup_dir)

    assert report.ai_cleanup_count == 1
    assert backup_dir.joinpath("cleanup-broken-relations-manifest.json").exists()
    assert fresh_ek._registry.has_entry(concept.id)
    body_after = entry_path.read_text(encoding="utf-8").split("---\n", 2)[2]
    assert body_after == original_body
    frontmatter, _ = read_file(str(entry_path))
    relations = frontmatter.get("relations")
    assert isinstance(relations, list)
    assert all(
        not (isinstance(item, dict) and item.get("target") == MISSING_AI)
        for item in relations
    )
    assert any(
        isinstance(item, dict) and item.get("target") == MISSING_CONFIRMED
        for item in relations
    )


def test_restore_reverts_relation_cleanup(fresh_ek: EgoKnowledge, tmp_path: Path) -> None:
    """restore 从备份恢复 apply 前 frontmatter。"""

    source = fresh_ek.ingest("source", source_payload(title="关系来源3"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="断裂关系概念3"),
    )
    _inject_relations(
        fresh_ek._data_root,
        Path(concept.file_path or ""),
        [
            {
                "target": MISSING_AI,
                "type": "related",
                "source": "ai_suggested",
            }
        ],
    )
    entry_path = Path(concept.file_path or "")
    if not entry_path.is_absolute():
        entry_path = fresh_ek._data_root / entry_path
    original = entry_path.read_text(encoding="utf-8")
    backup_dir = tmp_path / "relations-backup-restore"

    cleanup_broken_relations_apply(fresh_ek._data_root, backup_dir)
    assert entry_path.read_text(encoding="utf-8") != original

    cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)

    assert entry_path.read_text(encoding="utf-8") == original


def test_registry_only_relation_apply_and_restore(
    fresh_ek: EgoKnowledge,
    tmp_path: Path,
) -> None:
    """registry 备份恢复保留原件，同一 backup_dir 可重复 restore。"""

    source = fresh_ek.ingest("source", source_payload(title="registry-only 来源"))
    (fresh_ek._data_root / "entries").mkdir(exist_ok=True)
    insert_broken_relation(fresh_ek, source.id, MISSING_AI, "related", "ai_suggested")
    db_path = fresh_ek._data_root / "registry" / "catalog.sqlite"
    backup_dir = tmp_path / "registry-backup"

    report = cleanup_broken_relations_apply(fresh_ek._data_root, backup_dir)

    assert report.ai_cleanup_count == 1
    assert _relation_count(db_path, source.id, MISSING_AI, "related", "ai_suggested") == 0
    registry_backup = backup_dir / "registry" / "catalog.sqlite"
    assert registry_backup.exists()
    manifest = _read_manifest_payload(backup_dir)
    assert manifest["registry_backup"]["relative_path"] == "registry/catalog.sqlite"
    assert manifest["registry_backup"]["sha256"].startswith("sha256:")

    cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)
    cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)

    assert _relation_count(db_path, source.id, MISSING_AI, "related", "ai_suggested") == 1
    assert registry_backup.exists()


def test_restore_rejects_tampered_backup_sha(
    fresh_ek: EgoKnowledge,
    tmp_path: Path,
) -> None:
    """备份文件 sha256 与 manifest 不一致时拒绝恢复。"""

    source = fresh_ek.ingest("source", source_payload(title="关系来源6"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="断裂关系概念6"))
    _inject_relations(
        fresh_ek._data_root,
        Path(concept.file_path or ""),
        [{"target": MISSING_AI, "type": "related", "source": "ai_suggested"}],
    )
    backup_dir = tmp_path / "sha-backup"
    cleanup_broken_relations_apply(fresh_ek._data_root, backup_dir)
    manifest_path = backup_dir / "cleanup-broken-relations-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["entries"][0]["sha256"] = "sha256:" + "1" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(CleanupBrokenRelationsError, match="sha256 校验失败"):
        cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)


@pytest.mark.parametrize(
    ("manifest_update", "expected_message"),
    [
        ({"record_type": "other.manifest/v1"}, "record_type 不符"),
        ({"entry_count": 1}, "entry_count=1 与 entries 实际长度 0 不一致"),
    ],
)
def test_restore_rejects_invalid_manifest_header(
    tmp_path: Path,
    manifest_update: dict[str, object],
    expected_message: str,
) -> None:
    """manifest 顶层契约不符时拒绝恢复。"""

    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "entries").mkdir(parents=True)
    backup_dir = tmp_path / "bad-header-backup"
    _write_empty_manifest(backup_dir)
    manifest = _read_manifest_payload(backup_dir)
    manifest.update(manifest_update)
    _write_manifest_payload(backup_dir, manifest)

    with pytest.raises(CleanupBrokenRelationsError, match=expected_message):
        cleanup_broken_relations_restore(backup_dir, data_root)


def test_restore_rejects_unsafe_data_root_without_writing(tmp_path: Path) -> None:
    """restore 复用 data_root 护栏，拒绝仓库根/文件系统根/缺 entries 的目录。"""

    backup_dir = tmp_path / "empty-restore-backup"
    _write_empty_manifest(backup_dir)
    unsafe_data_roots = [
        Path("/"),
        tmp_path / "repo-root",
        tmp_path / "not-egoknowledge-data",
    ]
    unsafe_data_roots[1].mkdir()
    unsafe_data_roots[1].joinpath(".git").mkdir()
    unsafe_data_roots[2].mkdir()

    for data_root in unsafe_data_roots:
        with pytest.raises(CleanupBrokenRelationsError, match="data_root"):
            cleanup_broken_relations_restore(backup_dir, data_root)

    assert not unsafe_data_roots[1].joinpath("entries").exists()
    assert not unsafe_data_roots[1].joinpath("sources").exists()
    assert not unsafe_data_roots[1].joinpath("registry").exists()
    assert not unsafe_data_roots[2].joinpath("entries").exists()
    assert not unsafe_data_roots[2].joinpath("sources").exists()
    assert not unsafe_data_roots[2].joinpath("registry").exists()


def test_restore_rejects_tampered_registry_backup_sha(
    fresh_ek: EgoKnowledge,
    tmp_path: Path,
) -> None:
    """registry 备份内容与 manifest sha256 不一致时拒绝覆盖目标 registry。"""

    source = fresh_ek.ingest("source", source_payload(title="registry-sha 来源"))
    (fresh_ek._data_root / "entries").mkdir(exist_ok=True)
    insert_broken_relation(fresh_ek, source.id, MISSING_AI, "related", "ai_suggested")
    db_path = fresh_ek._data_root / "registry" / "catalog.sqlite"
    backup_dir = tmp_path / "registry-sha-backup"
    cleanup_broken_relations_apply(fresh_ek._data_root, backup_dir)
    expected_after_apply = db_path.read_bytes()
    backup_file = backup_dir / "registry" / "catalog.sqlite"
    backup_file.write_bytes(b"tampered registry backup")

    with pytest.raises(CleanupBrokenRelationsError, match="registry 备份 sha256 校验失败"):
        cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)

    assert db_path.read_bytes() == expected_after_apply
    assert _relation_count(db_path, source.id, MISSING_AI, "related", "ai_suggested") == 0


def test_restore_wraps_invalid_manifest_json(fresh_ek: EgoKnowledge, tmp_path: Path) -> None:
    """manifest JSON 损坏时包装为脚本统一错误类型。"""

    (fresh_ek._data_root / "entries").mkdir(parents=True, exist_ok=True)
    backup_dir = tmp_path / "bad-json-backup"
    backup_dir.mkdir()
    (backup_dir / "cleanup-broken-relations-manifest.json").write_text(
        "{not json",
        encoding="utf-8",
    )

    with pytest.raises(CleanupBrokenRelationsError, match="manifest 不是合法 JSON"):
        cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)


def test_apply_noop_does_not_create_backup_dir(fresh_ek: EgoKnowledge, tmp_path: Path) -> None:
    """没有 AI 断裂边时 apply 早退，不创建空备份目录。"""

    source = fresh_ek.ingest("source", source_payload(title="关系来源4"))
    fresh_ek.ingest("concept", concept_payload(source.id, title="干净概念"))
    backup_dir = tmp_path / "noop-backup"

    report = cleanup_broken_relations_apply(fresh_ek._data_root, backup_dir)

    assert report.ai_cleanup_count == 0
    assert not backup_dir.exists()


def test_apply_rejects_parse_errors(fresh_ek: EgoKnowledge, tmp_path: Path) -> None:
    """存在 frontmatter 解析错误时拒绝 apply，避免在不完整视图上清理。"""

    bad = fresh_ek._data_root / "entries" / "notes" / "bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(
        "---\nid: ek_note_01K00000000000000000000000\nkind: note\n---\n正文过短",
        encoding="utf-8",
    )

    with pytest.raises(CleanupBrokenRelationsError, match="解析错误"):
        cleanup_broken_relations_apply(fresh_ek._data_root, tmp_path / "backup")


def test_apply_rolls_back_written_files_on_write_failure(
    fresh_ek: EgoKnowledge,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第二个文件写入失败时，第一个已写文件必须回滚到原文。"""

    source = fresh_ek.ingest("source", source_payload(title="关系来源5"))
    left = fresh_ek.ingest("concept", concept_payload(source.id, title="断裂左"))
    right = fresh_ek.ingest("concept", concept_payload(source.id, title="断裂右"))
    left_path = fresh_ek._data_root / Path(left.file_path or "")
    right_path = fresh_ek._data_root / Path(right.file_path or "")
    _inject_relations(
        fresh_ek._data_root,
        Path(left.file_path or ""),
        [{"target": MISSING_AI, "type": "related", "source": "ai_suggested"}],
    )
    _inject_relations(
        fresh_ek._data_root,
        Path(right.file_path or ""),
        [{"target": MISSING_CONFIRMED, "type": "related", "source": "ai_suggested"}],
    )
    original_left = left_path.read_text(encoding="utf-8")
    original_right = right_path.read_text(encoding="utf-8")
    original_write: Callable[..., int] = Path.write_text

    def failing_write(self: Path, content: str, *args: Any, **kwargs: Any) -> int:
        if self == right_path:
            raise OSError(errno.EROFS, "mocked read-only filesystem")
        return original_write(self, content, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)

    with pytest.raises(CleanupBrokenRelationsError, match="已回滚"):
        cleanup_broken_relations_apply(fresh_ek._data_root, tmp_path / "rollback-backup")

    assert left_path.read_text(encoding="utf-8") == original_left
    assert right_path.read_text(encoding="utf-8") == original_right


def test_restore_rejects_manifest_path_traversal(
    fresh_ek: EgoKnowledge,
    tmp_path: Path,
) -> None:
    """restore 拒绝 manifest 中的越界相对路径。"""

    (fresh_ek._data_root / "entries").mkdir(parents=True, exist_ok=True)
    backup_dir = tmp_path / "bad-backup"
    backup_dir.mkdir()
    manifest = {
        "record_type": _MANIFEST_RECORD_TYPE,
        "entry_count": 1,
        "entries": [{"relative_path": "../escape.md", "sha256": "sha256:" + "0" * 64}],
        "registry_backup": None,
    }
    (backup_dir / "cleanup-broken-relations-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(CleanupBrokenRelationsError, match="越界"):
        cleanup_broken_relations_restore(backup_dir, fresh_ek._data_root)


@pytest.mark.parametrize(
    ("registry_backup", "expected_message"),
    [
        ("registry/catalog.sqlite", "缺少 sha256"),
        ({"relative_path": "../escape.sqlite", "sha256": "sha256:" + "0" * 64}, "越界"),
        ({"relative_path": "/tmp/escape.sqlite", "sha256": "sha256:" + "0" * 64}, "越界"),
    ],
)
def test_restore_rejects_invalid_registry_backup_manifest(
    tmp_path: Path,
    registry_backup: object,
    expected_message: str,
) -> None:
    """registry_backup 必须是带 sha256 的固定相对路径契约。"""

    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "entries").mkdir(parents=True)
    backup_dir = tmp_path / "bad-registry-manifest"
    backup_dir.mkdir()
    _write_manifest_payload(
        backup_dir,
        {
            "record_type": _MANIFEST_RECORD_TYPE,
            "entry_count": 0,
            "entries": [],
            "registry_backup": registry_backup,
        },
    )

    with pytest.raises(CleanupBrokenRelationsError, match=expected_message):
        cleanup_broken_relations_restore(backup_dir, data_root)

    assert not data_root.joinpath("registry").exists()


def test_cli_rejects_flag_and_positional_conflict(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """flag 模式与位置子命令同时出现时拒绝，避免高风险命令意图歧义。"""

    data_root = tmp_path / "data"
    (data_root / "entries").mkdir(parents=True)

    with pytest.raises(SystemExit) as excinfo:
        main(["--dry-run", "apply", "--data-root", str(data_root)])

    assert excinfo.value.code != 0
    assert "互斥" in capsys.readouterr().err


def test_cli_dispatches_dry_run_apply_and_restore(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI 三模式正常分发并输出可解析 JSON。"""

    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "entries").mkdir(parents=True)
    backup_dir = tmp_path / "empty-backup"
    backup_dir.mkdir()
    (backup_dir / "cleanup-broken-relations-manifest.json").write_text(
        json.dumps(
            {
                "record_type": _MANIFEST_RECORD_TYPE,
                "entry_count": 0,
                "entries": [],
                "registry_backup": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert main(["dry-run", "--data-root", str(data_root)]) == 0
    dry_payload = json.loads(capsys.readouterr().out)
    assert dry_payload["mode"] == "dry-run"

    assert main(["apply", "--data-root", str(data_root)]) == 0
    apply_payload = json.loads(capsys.readouterr().out)
    assert apply_payload["mode"] == "apply"
    assert apply_payload["backup_dir"].startswith(str(tmp_path))

    assert main(["restore", "--data-root", str(data_root), "--backup-dir", str(backup_dir)]) == 0
    restore_payload = json.loads(capsys.readouterr().out)
    assert restore_payload["mode"] == "restore"
