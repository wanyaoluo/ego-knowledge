"""断裂关系扫描与 dry-run 计划生成。"""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path
from typing import cast

from ego_knowledge._serde import _entry_from_frontmatter, _materialized_relation_rows
from ego_knowledge.errors import StorageError, ValidationError
from ego_knowledge.frontmatter import _load_frontmatter, split_frontmatter

from ._types import (
    AI_RELATION_ORIGINS,
    CONFIRMED_RELATION_ORIGIN,
    BrokenRelation,
    CleanupReport,
    FileCleanupChange,
    ParseError,
    RelationKey,
    SourceFile,
)

_SCAN_SUBDIRS = ("entries", "sources")
_REGISTRY_BROKEN_RELATIONS_QUERY = """
    SELECT r.source_id   AS source_id,
           r.target_id   AS target_id,
           r.type        AS rel_type,
           r.origin      AS origin,
           src.file_path AS source_path
      FROM relations AS r
      LEFT JOIN entries AS src ON src.id = r.source_id
     WHERE NOT EXISTS (
         SELECT 1 FROM entries AS e WHERE e.id = r.target_id
     )
     ORDER BY r.source_id, r.target_id, r.type
"""


class CleanupBrokenRelationsError(Exception):
    """cleanup_broken_relations 脚本对外统一错误类型。"""


def cleanup_broken_relations_dry_run(data_root: Path) -> CleanupReport:
    """扫描 Markdown 真源与当前 registry，生成 AI 断裂边清理计划。"""

    scan = scan_broken_relations(data_root)
    file_changes, registry_deletes = _plan_cleanup(scan, data_root)
    confirmed = [
        relation
        for relation in scan.broken_relations
        if relation.origin == CONFIRMED_RELATION_ORIGIN
    ]
    return CleanupReport(
        data_root=data_root,
        scanned_files=scan.scanned_files,
        known_entries=scan.known_entries,
        broken_relations=scan.broken_relations,
        confirmed_adjudication=confirmed,
        file_changes=file_changes,
        registry_delete_relations=registry_deletes,
        parse_errors=scan.parse_errors,
    )


def scan_broken_relations(data_root: Path) -> CleanupReport:
    """返回完整断裂关系清单，不做任何写入。"""

    _validate_data_root(data_root)
    source_files, parse_errors = _load_source_files(data_root)
    known_ids = {source.entry.id for source in source_files}
    by_key: dict[RelationKey, BrokenRelation] = {}

    for source_file in source_files:
        _add_frontmatter_relations(by_key, source_file, known_ids)
        _add_materialized_relations(by_key, source_file, known_ids)

    for relation in _scan_registry_broken_relations(data_root):
        _add_relation(
            by_key,
            relation.source_id,
            relation.target,
            relation.type,
            relation.origin,
            relation.source_path,
            storage="registry.relations",
            detector="registry",
        )

    broken_relations = sorted(
        by_key.values(),
        key=lambda item: (item.origin, item.source_id, item.target, item.type),
    )
    return CleanupReport(
        data_root=data_root,
        scanned_files=len(source_files),
        known_entries=len(known_ids),
        broken_relations=broken_relations,
        confirmed_adjudication=[
            relation
            for relation in broken_relations
            if relation.origin == CONFIRMED_RELATION_ORIGIN
        ],
        file_changes=[],
        registry_delete_relations=[],
        parse_errors=parse_errors,
    )


def report_to_payload(report: CleanupReport, *, mode: str) -> dict[str, object]:
    """把 CleanupReport 转成 CLI/报告 JSON payload。"""

    by_origin = Counter(relation.origin for relation in report.broken_relations)
    return {
        "ok": True,
        "mode": mode,
        "data_root": str(report.data_root),
        "scanned_files": report.scanned_files,
        "known_entries": report.known_entries,
        "broken_count": report.broken_count,
        "by_origin": dict(sorted(by_origin.items())),
        "ai_cleanup_count": report.ai_cleanup_count,
        "confirmed_adjudication_count": len(report.confirmed_adjudication),
        "file_change_count": len(report.file_changes),
        "registry_delete_count": len(report.registry_delete_relations),
        "parse_errors": [_parse_error_to_payload(error) for error in report.parse_errors],
        "broken_relations": [
            _broken_relation_to_payload(relation) for relation in report.broken_relations
        ],
        "confirmed_adjudication": [
            _broken_relation_to_payload(relation) for relation in report.confirmed_adjudication
        ],
        "file_changes": [
            {
                "path": change.path,
                "removed_relations": [
                    _broken_relation_to_payload(relation) for relation in change.removed_relations
                ],
            }
            for change in report.file_changes
        ],
        "registry_delete_relations": [
            _broken_relation_to_payload(relation) for relation in report.registry_delete_relations
        ],
    }


def source_files_by_relative_path(data_root: Path) -> dict[str, SourceFile]:
    """重新加载真源文件并按相对路径索引，供 apply 精确写回。"""

    source_files, parse_errors = _load_source_files(data_root)
    if parse_errors:
        details = "; ".join(f"{error.path}: {error.message}" for error in parse_errors[:5])
        raise CleanupBrokenRelationsError(
            f"存在 frontmatter 解析错误，拒绝 apply（先修复解析错误）：{details}"
        )
    return {source.relative_path: source for source in source_files}


def _plan_cleanup(
    report: CleanupReport,
    data_root: Path,
) -> tuple[list[FileCleanupChange], list[BrokenRelation]]:
    file_map: dict[str, list[BrokenRelation]] = {}
    registry_deletes: list[BrokenRelation] = []
    for relation in report.broken_relations:
        if relation.origin not in AI_RELATION_ORIGINS:
            continue
        if "registry.relations" in relation.storages:
            registry_deletes.append(relation)
        if "frontmatter.relations" not in relation.storages:
            continue
        if relation.source_path is None:
            continue
        relative_path = _normalize_source_path(data_root, relation.source_path)
        file_map.setdefault(relative_path, []).append(relation)
    file_changes = [
        FileCleanupChange(path=path, removed_relations=relations)
        for path, relations in sorted(file_map.items())
    ]
    registry_deletes.sort(key=lambda item: (item.source_id, item.target, item.type, item.origin))
    return file_changes, registry_deletes


def _add_frontmatter_relations(
    by_key: dict[RelationKey, BrokenRelation],
    source_file: SourceFile,
    known_ids: set[str],
) -> None:
    for relation in source_file.entry.relations:
        if relation.target in known_ids:
            continue
        _add_relation(
            by_key,
            source_file.entry.id,
            relation.target,
            relation.type.value,
            relation.source.value,
            source_file.relative_path,
            storage="frontmatter.relations",
            detector="frontmatter",
        )


def _add_materialized_relations(
    by_key: dict[RelationKey, BrokenRelation],
    source_file: SourceFile,
    known_ids: set[str],
) -> None:
    for _, target_id, rel_type, origin in _materialized_relation_rows(source_file.entry):
        if target_id in known_ids:
            continue
        _add_relation(
            by_key,
            source_file.entry.id,
            target_id,
            rel_type,
            origin,
            source_file.relative_path,
            storage=f"frontmatter.{rel_type}",
            detector="frontmatter",
        )


def _add_relation(
    by_key: dict[RelationKey, BrokenRelation],
    source: str,
    target: str,
    rel_type: str,
    origin: str,
    source_path: str | None,
    *,
    storage: str,
    detector: str,
) -> None:
    key = (source, target, rel_type, origin)
    existing = by_key.get(key)
    if existing is None:
        by_key[key] = BrokenRelation(
            source_id=source,
            target=target,
            type=rel_type,
            origin=origin,
            source_path=source_path,
            storages=[storage],
            detectors=[detector],
        )
        return
    if storage not in existing.storages:
        existing.storages.append(storage)
        existing.storages.sort()
    if detector not in existing.detectors:
        existing.detectors.append(detector)
        existing.detectors.sort()
    if existing.source_path is None and source_path is not None:
        existing.source_path = source_path


def _scan_registry_broken_relations(data_root: Path) -> list[BrokenRelation]:
    db_path = data_root / "registry" / "catalog.sqlite"
    if not db_path.is_file():
        return []
    uri = f"file:{db_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise CleanupBrokenRelationsError(f"无法只读打开 registry: {db_path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(_REGISTRY_BROKEN_RELATIONS_QUERY).fetchall()
    except sqlite3.Error as exc:
        raise CleanupBrokenRelationsError(f"扫描 registry 断裂关系失败: {db_path}: {exc}") from exc
    finally:
        conn.close()
    result: list[BrokenRelation] = []
    for row in rows:
        source_path = row["source_path"]
        normalized_path = (
            _normalize_source_path(data_root, cast(str, source_path))
            if source_path is not None
            else None
        )
        result.append(
            BrokenRelation(
                source_id=cast(str, row["source_id"]),
                target=cast(str, row["target_id"]),
                type=cast(str, row["rel_type"]),
                origin=cast(str, row["origin"]),
                source_path=normalized_path,
                storages=["registry.relations"],
                detectors=["registry"],
            )
        )
    return result


def _load_source_files(data_root: Path) -> tuple[list[SourceFile], list[ParseError]]:
    source_files: list[SourceFile] = []
    parse_errors: list[ParseError] = []
    for path in _iter_truth_files(data_root):
        try:
            text = path.read_text(encoding="utf-8")
            parsed = split_frontmatter(text)
            if parsed is None:
                continue
            frontmatter_raw, body = parsed
            frontmatter = _load_frontmatter(frontmatter_raw, str(path))
            entry = _entry_from_frontmatter(
                frontmatter,
                file_path=str(path),
                body=body,
            )
        except (OSError, StorageError, ValidationError) as exc:
            parse_errors.append(ParseError(path=_relative_posix(data_root, path), message=str(exc)))
            continue
        source_files.append(
            SourceFile(
                path=path,
                relative_path=_relative_posix(data_root, path),
                original_text=text,
                frontmatter=frontmatter,
                body=body,
                entry=entry,
            )
        )
    return source_files, parse_errors


def _iter_truth_files(data_root: Path) -> list[Path]:
    files: list[Path] = []
    for subdir in _SCAN_SUBDIRS:
        root = data_root / subdir
        if root.is_dir():
            files.extend(path for path in sorted(root.rglob("*.md")) if path.is_file())
    return sorted(files, key=lambda path: path.as_posix())


def _normalize_source_path(data_root: Path, source_path: str) -> str:
    path = Path(source_path)
    if not path.is_absolute():
        return path.as_posix()
    return _relative_posix(data_root, path)


def _relative_posix(data_root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(data_root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.as_posix()


def _validate_data_root(data_root: Path) -> None:
    resolved = data_root.resolve(strict=False)
    if resolved == resolved.parent:
        raise CleanupBrokenRelationsError(f"拒绝不安全的 data_root (文件系统根): {data_root}")
    if not resolved.exists():
        raise CleanupBrokenRelationsError(f"data_root 不存在: {data_root}")
    if not resolved.is_dir():
        raise CleanupBrokenRelationsError(f"data_root 不是目录: {data_root}")
    if (resolved / ".git").exists():
        raise CleanupBrokenRelationsError(f"拒绝不安全的 data_root (仓库根，含 .git): {data_root}")
    if not (resolved / "entries").is_dir():
        raise CleanupBrokenRelationsError(
            f"data_root 不是 canonical EgoKnowledge 数据根 (缺少 entries/ 子目录): {data_root}"
        )


def _broken_relation_to_payload(relation: BrokenRelation) -> dict[str, object]:
    return {
        "source": relation.source_id,
        "target": relation.target,
        "type": relation.type,
        "origin": relation.origin,
        "source_path": relation.source_path,
        "storages": list(relation.storages),
        "detectors": list(relation.detectors),
    }


def _parse_error_to_payload(error: ParseError) -> dict[str, str]:
    return {"path": error.path, "message": error.message}
