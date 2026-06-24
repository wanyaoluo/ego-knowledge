"""Dry-run stage: query registry and create signed snapshot."""

from __future__ import annotations

import re
from pathlib import Path

from ego_knowledge.paths import sha256_text_hex
from ego_knowledge.registry import REGISTRY_SCHEMA_VERSION, Registry

from ._helpers import (
    ArchiveError,
    _entry_path,
    _file_sha256,
    _ids_sha256,
    _is_string_dict,
    _json_line,
    _loads_json,
    _row_string,
    _string_field,
    _utc_now_text,
)

ALLOWED_FILTER_COLUMNS = frozenset(
    {"id", "kind", "status", "created_at", "domain", "slug", "title"}
)
ALLOWED_FILTER_OPERATORS = frozenset({"=", "!=", "<>"})
FILTER_CLAUSE_RE = re.compile(
    r"^\s*(?P<column>[a-z_]+)\s*(?P<operator>!=|<>|=)\s*"
    r"(?P<value>'[^']*'|\"[^\"]*\"|[^\s]+)\s*$"
)


def run_dry_run(
    *,
    data_root: Path,
    filter_expr: str,
    snapshot_path: Path,
    expected_count: int,
    assume_yes: bool = False,
) -> dict[str, object]:
    _ensure_snapshot_can_be_created(snapshot_path)
    clauses = _parse_filter(filter_expr)
    _enforce_dirty_concept_filter(clauses)
    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        registry.init_schema()
        entries = _fetch_snapshot_entries(registry, data_root, clauses)
    finally:
        registry.close()

    hit_count = len(entries)
    if hit_count != expected_count:
        raise ArchiveError(
            f"dry-run 命中 {hit_count} 条，与 expected-count={expected_count} 不一致，已阻断"
        )
    if not assume_yes and not _confirm(hit_count, expected_count):
        raise ArchiveError("用户未输入 yes，已取消生成快照")

    manifest = _write_snapshot(snapshot_path, entries, filter_expr=filter_expr)
    return {
        "ok": True,
        "mode": "dry-run",
        "count": hit_count,
        "snapshot": str(snapshot_path),
        "payload_sha256": manifest["payload_sha256"],
    }


def _parse_filter(filter_expr: str) -> list[tuple[str, str, str]]:
    clauses: list[tuple[str, str, str]] = []
    for raw_clause in re.split(r"\s+AND\s+", filter_expr, flags=re.IGNORECASE):
        match = FILTER_CLAUSE_RE.match(raw_clause)
        if match is None:
            raise ArchiveError(f"不支持的 filter 子句: {raw_clause}")
        column = match.group("column")
        operator = match.group("operator")
        value = _strip_quotes(match.group("value"))
        if column not in ALLOWED_FILTER_COLUMNS:
            raise ArchiveError(f"filter 字段不在白名单: {column}")
        if operator not in ALLOWED_FILTER_OPERATORS:
            raise ArchiveError(f"filter 操作符不支持: {operator}")
        clauses.append((column, "!=" if operator == "<>" else operator, value))
    return clauses


def _enforce_dirty_concept_filter(clauses: list[tuple[str, str, str]]) -> None:
    normalized = {(column, operator, value) for column, operator, value in clauses}
    if ("kind", "=", "concept") not in normalized:
        raise ArchiveError("归档脚本只允许 kind=concept 的精确过滤")
    if ("status", "!=", "archived") not in normalized:
        raise ArchiveError("归档脚本必须显式包含 status != 'archived'")


def _fetch_snapshot_entries(
    registry: Registry,
    data_root: Path,
    clauses: list[tuple[str, str, str]],
) -> list[dict[str, object]]:
    where = " AND ".join(f"{column} {operator} ?" for column, operator, _ in clauses)
    params = tuple(value for _, _, value in clauses)
    rows = registry.conn.execute(
        f"""
        SELECT id, kind, title, slug, domain, status, created_at, updated_at,
               file_path, frontmatter_json
          FROM entries
         WHERE {where}
         ORDER BY id
        """,
        params,
    ).fetchall()
    snapshot_ts = _utc_now_text()
    version = _registry_version(registry)
    entries: list[dict[str, object]] = []
    for row in rows:
        row_id = _row_string(row, "id", label="entries")
        if _row_string(row, "kind", label="entries") != "concept":
            raise ArchiveError(f"filter 命中非 concept 条目，已阻断: {row_id}")
        row_path = Path(_row_string(row, "file_path", label="entries"))
        file_path = _relative_or_safe_path(data_root, row_path)
        absolute_path = _entry_path(data_root, file_path)
        frontmatter_raw = _loads_json(
            _row_string(row, "frontmatter_json", label="entries"),
            label="entries.frontmatter_json",
        )
        if not _is_string_dict(frontmatter_raw):
            raise ArchiveError(f"frontmatter_json 结构损坏: {row_id}")
        entries.append(
            {
                "record_type": "entry",
                "id": row_id,
                "title": _row_string(row, "title", label="entries"),
                "slug": _row_string(row, "slug", label="entries"),
                "domain": row["domain"],
                "kind": _row_string(row, "kind", label="entries"),
                "status_before": _row_string(row, "status", label="entries"),
                "file_path": file_path,
                "file_hash_before": _file_sha256(absolute_path),
                "created_at": _row_string(row, "created_at", label="entries"),
                "updated_at": _row_string(row, "updated_at", label="entries"),
                "snapshot_ts": snapshot_ts,
                "registry_version": version,
                "frontmatter_before": frontmatter_raw,
            }
        )
    return entries


def _write_snapshot(
    snapshot_path: Path,
    entries: list[dict[str, object]],
    *,
    filter_expr: str,
) -> dict[str, object]:
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    entry_lines = "".join(_json_line(entry) for entry in entries)
    ids = [_string_field(entry, "id", label="snapshot.entry") for entry in entries]
    manifest: dict[str, object] = {
        "record_type": "manifest",
        "format_version": 1,
        "action": "archive_dirty_concepts",
        "snapshot_ts": (
            _string_field(entries[0], "snapshot_ts", label="snapshot.entry")
            if entries
            else _utc_now_text()
        ),
        "entry_count": len(entries),
        "payload_sha256": sha256_text_hex(entry_lines),
        "entry_ids_sha256": _ids_sha256(ids),
        "filter": filter_expr,
        "registry_schema_version": REGISTRY_SCHEMA_VERSION,
    }
    snapshot_text = _json_line(manifest) + entry_lines
    snapshot_path.write_text(snapshot_text, encoding="utf-8")
    snapshot_path.with_suffix(snapshot_path.suffix + ".sha256").write_text(
        sha256_text_hex(snapshot_text) + "\n",
        encoding="utf-8",
    )
    return manifest


def _ensure_snapshot_can_be_created(snapshot_path: Path) -> None:
    if not snapshot_path.exists():
        return
    execution_path = _default_execution_log(snapshot_path)
    if execution_path.exists() and execution_path.stat().st_size > 0:
        raise ArchiveError("snapshot 已存在且 execution.jsonl 有数据，禁止中断后重新生成快照")
    raise ArchiveError(f"snapshot 已存在，拒绝覆盖: {snapshot_path}")


def _default_execution_log(snapshot_path: Path) -> Path:
    name = snapshot_path.name
    if name.endswith(".snapshot.jsonl"):
        return snapshot_path.with_name(name.removesuffix(".snapshot.jsonl") + ".execution.jsonl")
    return snapshot_path.with_suffix(snapshot_path.suffix + ".execution.jsonl")


def _registry_version(registry: Registry) -> str:
    row = registry.conn.execute(
        "SELECT value FROM registry_meta WHERE key = 'schema_version'"
    ).fetchone()
    return _row_string(row, "value", label="registry_meta") if row else REGISTRY_SCHEMA_VERSION


def _relative_or_safe_path(data_root: Path, file_path: Path) -> str:
    target = file_path if file_path.is_absolute() else data_root / file_path
    try:
        return target.resolve(strict=False).relative_to(data_root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise ArchiveError(f"registry file_path 越过 data-root: {file_path}") from exc


def _confirm(hit_count: int, expected_count: int) -> bool:
    print(f"[dry-run] 命中 {hit_count} 条 ✓ 与 expected-count={expected_count} 一致")
    return input("[continue?] 输入 'yes' 继续生成快照: ").strip() == "yes"


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
