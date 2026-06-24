"""AI 断裂关系 apply/restore：备份、写回与 registry 清理。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml  # type: ignore[import-untyped]

from ego_knowledge.paths import resolve_data_root, sha256_text_digest

from ._scan import (
    CleanupBrokenRelationsError,
    _validate_data_root,
    cleanup_broken_relations_dry_run,
    source_files_by_relative_path,
)
from ._types import BrokenRelation, CleanupReport, RelationKey, SourceFile

_logger = logging.getLogger(__name__)

_MANIFEST_FILENAME = "cleanup-broken-relations-manifest.json"
_MANIFEST_RECORD_TYPE = "cleanup_broken_relations.backup.manifest/v1"
_MANIFEST_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REGISTRY_BACKUP_RELATIVE_PATH = "registry/catalog.sqlite"
_RESTORE_TRUTH_ROOTS = ("entries", "sources")


@dataclass(frozen=True, slots=True)
class ApplyPlan:
    """apply 阶段的不可变执行计划。"""

    file_updates: list[tuple[SourceFile, str]]
    registry_deletes: list[BrokenRelation]


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """restore 可写回的 Markdown 备份条目。"""

    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class RegistryBackup:
    """restore 可写回的 registry 备份条目。"""

    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """cleanup_broken_relations manifest 的强校验视图。"""

    entries: list[ManifestEntry]
    registry_backup: RegistryBackup | None


def cleanup_broken_relations_apply(data_root: Path, backup_dir: Path) -> CleanupReport:
    """备份受影响文件/registry，并删除 ai_suggested/ai_confirmed 断裂边。"""

    report = cleanup_broken_relations_dry_run(data_root)
    _reject_parse_errors(report)
    if report.ai_cleanup_count == 0:
        _logger.info(
            "cleanup_broken_relations.apply nothing to do",
            extra={"data_root": str(data_root), "broken_count": report.broken_count},
        )
        return report

    plan = _prepare_apply_plan(data_root, backup_dir, report)
    _execute_apply_plan(data_root, plan)

    _logger.info(
        "cleanup_broken_relations.apply done",
        extra={
            "data_root": str(data_root),
            "backup_dir": str(backup_dir),
            "ai_cleanup_count": report.ai_cleanup_count,
        },
    )
    return report


def _reject_parse_errors(report: CleanupReport) -> None:
    if not report.parse_errors:
        return
    details = "; ".join(f"{error.path}: {error.message}" for error in report.parse_errors[:5])
    raise CleanupBrokenRelationsError(
        f"存在 frontmatter 解析错误，拒绝 apply（先修复解析错误）：{details}"
    )


def _prepare_apply_plan(data_root: Path, backup_dir: Path, report: CleanupReport) -> ApplyPlan:
    _validate_backup_dir(data_root, backup_dir)
    source_files = source_files_by_relative_path(data_root)
    file_updates = _build_file_updates(report, source_files)
    registry_deletes = list(report.registry_delete_relations)

    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries = _backup_files(backup_dir, file_updates)
    registry_backup = _backup_registry_if_needed(data_root, backup_dir, registry_deletes)
    _write_manifest(backup_dir, manifest_entries, registry_backup, registry_deletes)
    return ApplyPlan(file_updates=file_updates, registry_deletes=registry_deletes)


def _execute_apply_plan(data_root: Path, plan: ApplyPlan) -> None:
    written: list[tuple[Path, str]] = []
    conn = _open_registry_for_write(data_root) if plan.registry_deletes else None
    try:
        if conn is not None:
            conn.execute("BEGIN IMMEDIATE")
            _delete_registry_relations(conn, plan.registry_deletes)
        written = _write_file_updates(plan.file_updates)
        if conn is not None:
            conn.commit()
    except (OSError, sqlite3.Error) as exc:
        if conn is not None:
            conn.rollback()
        rolled_back, failures = _rollback_files(written)
        suffix = ""
        if failures:
            suffix = "; 以下文件回滚失败，请从 backup 手动恢复: " + ", ".join(
                str(path) for path in failures
            )
        raise CleanupBrokenRelationsError(
            f"清理断裂关系失败，已回滚文件 {rolled_back}/{len(written)}: {exc}{suffix}"
        ) from exc
    finally:
        if conn is not None:
            conn.close()


def _write_file_updates(file_updates: list[tuple[SourceFile, str]]) -> list[tuple[Path, str]]:
    written: list[tuple[Path, str]] = []
    for source_file, fixed_text in file_updates:
        source_file.path.write_text(fixed_text, encoding="utf-8")
        written.append((source_file.path, source_file.original_text))
    return written


def cleanup_broken_relations_restore(backup_dir: Path, data_root: Path) -> None:
    """按 manifest 恢复 cleanup_broken_relations apply 前的文件与 registry。"""

    data_root = _canonical_data_root(data_root)
    manifest = _read_manifest(backup_dir)
    truth_roots_resolved = tuple(
        (data_root / root).resolve(strict=False) for root in _RESTORE_TRUTH_ROOTS
    )
    for entry in manifest.entries:
        backup_path = backup_dir / entry.relative_path
        target = data_root / entry.relative_path
        _assert_target_within_roots(target, truth_roots_resolved)
        if not backup_path.is_file():
            raise CleanupBrokenRelationsError(f"备份文件缺失: {backup_path}")
        original_text = backup_path.read_text(encoding="utf-8")
        actual_sha = sha256_text_digest(original_text)
        if actual_sha != entry.sha256:
            raise CleanupBrokenRelationsError(
                f"备份 sha256 校验失败: {backup_path} 期望 {entry.sha256} 实际 {actual_sha}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original_text, encoding="utf-8")

    registry_backup = manifest.registry_backup
    if registry_backup is not None:
        backup_path = backup_dir / registry_backup.relative_path
        target = data_root / _REGISTRY_BACKUP_RELATIVE_PATH
        _assert_target_within_data_root(target, data_root.resolve(strict=False))
        if not backup_path.is_file():
            raise CleanupBrokenRelationsError(f"registry 备份文件缺失: {backup_path}")
        actual_sha = _sha256_file_digest(backup_path)
        if actual_sha != registry_backup.sha256:
            raise CleanupBrokenRelationsError(
                f"registry 备份 sha256 校验失败: {backup_path} "
                f"期望 {registry_backup.sha256} 实际 {actual_sha}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy_registry_backup_atomically(backup_path, target)


def _build_file_updates(
    report: CleanupReport,
    source_files: dict[str, SourceFile],
) -> list[tuple[SourceFile, str]]:
    updates: list[tuple[SourceFile, str]] = []
    for change in report.file_changes:
        source_file = source_files.get(change.path)
        if source_file is None:
            raise CleanupBrokenRelationsError(f"待清理文件不存在或无法解析: {change.path}")
        keys = {(r.source_id, r.target, r.type, r.origin) for r in change.removed_relations}
        fixed_frontmatter, removed = _remove_generic_relations(source_file.frontmatter, keys)
        if removed != len(keys):
            raise CleanupBrokenRelationsError(
                f"断裂关系定位失败，拒绝半清理: {change.path} 期望 {len(keys)} 实际 {removed}"
            )
        fixed_text = _render_markdown_preserve_body(fixed_frontmatter, source_file.body)
        updates.append((source_file, fixed_text))
    return updates


def _render_markdown_preserve_body(frontmatter: dict[str, object], body: str) -> str:
    """重写 frontmatter 并逐字保留 body，避免清理关系边时触碰正文。"""

    try:
        frontmatter_raw = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except yaml.YAMLError as exc:
        raise CleanupBrokenRelationsError(f"frontmatter 序列化失败: {exc}") from exc
    return f"---\n{frontmatter_raw}---\n{body}"


def _remove_generic_relations(
    frontmatter: dict[str, object],
    keys: set[RelationKey],
) -> tuple[dict[str, object], int]:
    fixed = dict(frontmatter)
    relations_obj = fixed.get("relations")
    if not isinstance(relations_obj, list):
        return fixed, 0
    remaining: list[object] = []
    removed = 0
    source_id = str(fixed.get("id", ""))
    for item in relations_obj:
        if isinstance(item, dict):
            target = item.get("target")
            rel_type = item.get("type")
            # frontmatter 的 source 字段表示关系产生方式，对应 BrokenRelation.origin。
            origin = item.get("source", "confirmed")
            if (
                isinstance(target, str)
                and isinstance(rel_type, str)
                and isinstance(origin, str)
                and (source_id, target, rel_type, origin) in keys
            ):
                removed += 1
                continue
        remaining.append(item)
    if remaining:
        fixed["relations"] = remaining
    else:
        fixed.pop("relations", None)
    return fixed, removed


def _backup_files(
    backup_dir: Path,
    file_updates: list[tuple[SourceFile, str]],
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for source_file, _ in file_updates:
        backup_path = backup_dir / source_file.relative_path
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_path.write_text(source_file.original_text, encoding="utf-8")
        entries.append(
            {
                "relative_path": source_file.relative_path,
                "sha256": sha256_text_digest(source_file.original_text),
            }
        )
    return entries


def _backup_registry_if_needed(
    data_root: Path,
    backup_dir: Path,
    registry_deletes: list[BrokenRelation],
) -> dict[str, object] | None:
    if not registry_deletes:
        return None
    db_path = data_root / "registry" / "catalog.sqlite"
    if not db_path.is_file():
        return None
    target = backup_dir / _REGISTRY_BACKUP_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, target)
    return {
        "relative_path": _REGISTRY_BACKUP_RELATIVE_PATH,
        "sha256": _sha256_file_digest(target),
    }


def _write_manifest(
    backup_dir: Path,
    entries: list[dict[str, object]],
    registry_backup: dict[str, object] | None,
    registry_deletes: list[BrokenRelation],
) -> None:
    payload: dict[str, object] = {
        "record_type": _MANIFEST_RECORD_TYPE,
        "entry_count": len(entries),
        "entries": entries,
        "registry_backup": registry_backup,
        "registry_delete_count": len(registry_deletes),
        "registry_delete_relations": [
            {
                "source": relation.source_id,
                "target": relation.target,
                "type": relation.type,
                "origin": relation.origin,
            }
            for relation in registry_deletes
        ],
    }
    (backup_dir / _MANIFEST_FILENAME).write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def _read_manifest(backup_dir: Path) -> BackupManifest:
    if not backup_dir.exists() or not backup_dir.is_dir():
        raise CleanupBrokenRelationsError(f"backup_dir 不存在或不是目录: {backup_dir}")
    manifest_path = backup_dir / _MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise CleanupBrokenRelationsError(f"backup_dir 缺少 manifest: {manifest_path}")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CleanupBrokenRelationsError(f"读取 manifest 失败: {manifest_path}: {exc}") from exc
    try:
        payload: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CleanupBrokenRelationsError(
            f"manifest 不是合法 JSON: {manifest_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CleanupBrokenRelationsError(f"manifest 顶层不是 object: {manifest_path}")
    if payload.get("record_type") != _MANIFEST_RECORD_TYPE:
        raise CleanupBrokenRelationsError(f"manifest record_type 不符: {manifest_path}")
    entries_obj = payload.get("entries")
    if not isinstance(entries_obj, list):
        raise CleanupBrokenRelationsError(f"manifest.entries 不是 list: {manifest_path}")
    entry_count = payload.get("entry_count")
    if not isinstance(entry_count, int) or isinstance(entry_count, bool):
        raise CleanupBrokenRelationsError(
            f"manifest.entry_count 不是 int: {entry_count!r} ({manifest_path})"
        )
    if entry_count != len(entries_obj):
        raise CleanupBrokenRelationsError(
            f"manifest.entry_count={entry_count} 与 entries 实际长度 "
            f"{len(entries_obj)} 不一致 ({manifest_path})"
        )
    entries: list[ManifestEntry] = []
    for index, entry in enumerate(entries_obj):
        if not isinstance(entry, dict):
            raise CleanupBrokenRelationsError(f"manifest.entries[{index}] 不是 object: {entry!r}")
        relative_path = _assert_relative_path(entry.get("relative_path"), index, manifest_path)
        expected_sha = _assert_sha(entry.get("sha256"), f"entries[{index}].sha256", manifest_path)
        entries.append(ManifestEntry(relative_path=relative_path, sha256=expected_sha))
    registry_backup = _read_registry_backup(payload.get("registry_backup"), manifest_path)
    return BackupManifest(entries=entries, registry_backup=registry_backup)


def _open_registry_for_write(data_root: Path) -> sqlite3.Connection:
    db_path = data_root / "registry" / "catalog.sqlite"
    if not db_path.is_file():
        raise CleanupBrokenRelationsError(
            f"registry 不存在，无法清理 registry-only 断裂边: {db_path}"
        )
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
    except sqlite3.Error as exc:
        raise CleanupBrokenRelationsError(f"无法打开 registry: {db_path}: {exc}") from exc
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _delete_registry_relations(conn: sqlite3.Connection, relations: list[BrokenRelation]) -> None:
    for relation in relations:
        conn.execute(
            """
            DELETE FROM relations
             WHERE source_id = ?
               AND target_id = ?
               AND type = ?
               AND origin = ?
            """,
            (relation.source_id, relation.target, relation.type, relation.origin),
        )


def _rollback_files(written: list[tuple[Path, str]]) -> tuple[int, list[Path]]:
    rolled_back = 0
    failures: list[Path] = []
    for path, original_text in written:
        try:
            path.write_text(original_text, encoding="utf-8")
            rolled_back += 1
        except OSError:
            failures.append(path)
            _logger.exception(
                "cleanup_broken_relations.apply file rollback failed",
                extra={"path": str(path)},
            )
    return rolled_back, failures


def _canonical_data_root(data_root: Path) -> Path:
    resolved = resolve_data_root(data_root).resolve(strict=False)
    _validate_data_root(resolved)
    return resolved


def _assert_target_within_roots(target: Path, roots_resolved: tuple[Path, ...]) -> None:
    resolved_target = target.resolve(strict=False)
    for root in roots_resolved:
        try:
            resolved_target.relative_to(root)
        except ValueError:
            continue
        return
    roots = ", ".join(str(root) for root in roots_resolved)
    raise CleanupBrokenRelationsError(f"restore target 越界 (不在 {roots} 内): {target}")


def _assert_target_within_data_root(target: Path, data_root_resolved: Path) -> None:
    try:
        target.resolve(strict=False).relative_to(data_root_resolved)
    except ValueError as exc:
        raise CleanupBrokenRelationsError(
            f"registry restore target 越界 (不在 data_root 内): {target}"
        ) from exc


def _assert_relative_path(value: object, index: int, manifest_path: Path) -> str:
    if not isinstance(value, str) or not value:
        raise CleanupBrokenRelationsError(
            f"manifest.entries[{index}].relative_path 缺少字符串值: {value!r} ({manifest_path})"
        )
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise CleanupBrokenRelationsError(
            f"manifest.entries[{index}].relative_path 越界: {value!r} ({manifest_path})"
        )
    if not (value.startswith("entries/") or value.startswith("sources/")):
        raise CleanupBrokenRelationsError(
            f"manifest.entries[{index}].relative_path 不在 entries/sources: "
            f"{value!r} ({manifest_path})"
        )
    if not value.endswith(".md"):
        raise CleanupBrokenRelationsError(
            f"manifest.entries[{index}].relative_path 不以 .md 结尾: {value!r} ({manifest_path})"
        )
    return value


def _assert_sha(value: object, field: str, manifest_path: Path) -> str:
    if not isinstance(value, str) or not _MANIFEST_SHA256_RE.match(value):
        raise CleanupBrokenRelationsError(
            f"manifest.{field} 格式错误，期望 sha256:<64hex>: {value!r} ({manifest_path})"
        )
    return value


def _read_registry_backup(value: object, manifest_path: Path) -> RegistryBackup | None:
    if value is None:
        return None
    if isinstance(value, str):
        raise CleanupBrokenRelationsError(
            f"manifest.registry_backup 使用旧字符串契约，缺少 sha256: {value!r} ({manifest_path})"
        )
    if not isinstance(value, dict):
        raise CleanupBrokenRelationsError(
            f"manifest.registry_backup 不是 object/null: {value!r} ({manifest_path})"
        )
    relative_path = _assert_registry_backup_path(value.get("relative_path"), manifest_path)
    expected_sha = _assert_sha(value.get("sha256"), "registry_backup.sha256", manifest_path)
    return RegistryBackup(relative_path=relative_path, sha256=expected_sha)


def _assert_registry_backup_path(value: object, manifest_path: Path) -> str:
    if not isinstance(value, str) or not value:
        raise CleanupBrokenRelationsError(
            f"manifest.registry_backup.relative_path 缺少字符串值: {value!r} ({manifest_path})"
        )
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or ".." in parsed.parts:
        raise CleanupBrokenRelationsError(
            f"manifest.registry_backup.relative_path 越界: {value!r} ({manifest_path})"
        )
    if value != _REGISTRY_BACKUP_RELATIVE_PATH:
        raise CleanupBrokenRelationsError(
            f"manifest.registry_backup.relative_path 必须是 {_REGISTRY_BACKUP_RELATIVE_PATH!r}: "
            f"{value!r} ({manifest_path})"
        )
    return value


def _sha256_file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CleanupBrokenRelationsError(f"计算 sha256 失败: {path}: {exc}") from exc
    return "sha256:" + digest.hexdigest()


def _copy_registry_backup_atomically(backup_path: Path, target: Path) -> None:
    tmp_target = target.with_name(f".{target.name}.restore.tmp")
    try:
        shutil.copy2(backup_path, tmp_target)
        os.replace(tmp_target, target)
    except OSError as exc:
        try:
            tmp_target.unlink(missing_ok=True)
        except OSError:
            _logger.exception(
                "cleanup_broken_relations.restore temp registry cleanup failed",
                extra={"path": str(tmp_target)},
            )
        raise CleanupBrokenRelationsError(
            f"registry 恢复失败: {backup_path} -> {target}: {exc}"
        ) from exc


def _validate_backup_dir(data_root: Path, backup_dir: Path) -> None:
    try:
        backup_rel = backup_dir.resolve(strict=False).relative_to(data_root.resolve(strict=False))
        raise CleanupBrokenRelationsError(
            f"backup_dir 不能嵌套在 data_root 内 (相对路径: {backup_rel}): {backup_dir}"
        )
    except ValueError:
        pass
    if backup_dir.exists() and any(backup_dir.rglob("*")):
        raise CleanupBrokenRelationsError(
            f"backup_dir 已存在且非空，拒绝覆盖 (避免丢失既有备份): {backup_dir}"
        )
