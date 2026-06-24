"""Build orchestration for registry rebuilds.

Depends on Registry (from .registry) for database operations.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ._frontmatter_coercion import _utc_now_text
from ._serde import _entry_from_frontmatter
from .errors import StorageError, ValidationError
from .frontmatter import read_file
from .models import Entry

if TYPE_CHECKING:
    from .registry import Registry

type ErrorRecord = tuple[str, str]

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RegistryStats:
    """Result summary for a full registry rebuild."""

    entries_ok: int = 0
    entries_failed: int = 0
    errors: list[ErrorRecord] = field(default_factory=list)


def build_registry(data_root: Path) -> RegistryStats:
    """Rebuild catalog.sqlite from the Markdown truth source."""

    from .metrics import full_recompute
    from .registry import Registry

    registry_dir = data_root / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    tmp_db = registry_dir / "catalog.sqlite.tmp"
    final_db = registry_dir / "catalog.sqlite"
    lock_file = registry_dir / ".lock"
    stats = RegistryStats()

    with _acquire_write_lock(lock_file):
        tmp_db.unlink(missing_ok=True)
        registry = Registry(tmp_db)
        loaded_entries: list[tuple[Entry, Path]] = []
        try:
            registry.init_schema()
            for file_path in _iter_entry_files(data_root):
                try:
                    frontmatter_map, body = read_file(str(file_path))
                    entry = _entry_from_frontmatter(frontmatter_map)
                    registry.stage_build_entry(
                        entry,
                        file_path,
                        body,
                    )
                    stats.entries_ok += 1
                    loaded_entries.append((entry, file_path))
                except (ValidationError, StorageError) as exc:
                    stats.entries_failed += 1
                    stats.errors.append((str(file_path), str(exc)))
            for entry, file_path in loaded_entries:
                try:
                    registry.validate_build_entry_relations(entry)
                except StorageError as exc:
                    registry.delete_entry_by_id(entry.id)
                    stats.entries_ok -= 1
                    stats.entries_failed += 1
                    stats.errors.append((str(file_path), str(exc)))
            _init_metrics_placeholder(registry)
            _replay_access_log_if_available(registry, data_root / "logs" / "access")
            full_recompute(registry)
            registry.upsert_meta("built_at", _utc_now_text())
            registry.rebuild_custom_dictionary()
            registry.commit()
            registry.close()
            try:
                os.rename(tmp_db, final_db)
            except OSError as exc:
                tmp_db.unlink(missing_ok=True)
                raise StorageError(f"原子替换注册表失败 {final_db}: {exc}") from exc
            return stats
        except Exception:
            try:
                registry.close()
            finally:
                tmp_db.unlink(missing_ok=True)
            raise


def _init_metrics_placeholder(registry: Registry) -> None:
    """Ensure each entry has a placeholder metrics row with zero values."""

    registry.init_metrics_placeholders()


def _replay_access_log_if_available(registry: Registry, access_log_dir: Path) -> None:
    """Replay jsonl access logs back into the new catalog's access_log table.

    Reads all ``logs/access/*.jsonl`` files. Each line must contain
    ``entry_id``, ``op``, and ``accessed_at``. Entries that no longer exist
    in the catalog are silently skipped (with a warning logged).
    Malformed lines are also skipped.
    """

    if not access_log_dir.exists() or not access_log_dir.is_dir():
        return

    jsonl_files = sorted(access_log_dir.glob("*.jsonl"))
    if not jsonl_files:
        return

    existing_ids: set[str] | None = None  # lazy-loaded
    skipped_count = 0
    replayed_count = 0

    rows_to_insert: list[tuple[str, str, str]] = []

    for jsonl_file in jsonl_files:
        try:
            text = jsonl_file.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("无法读取 access log %s: %s", jsonl_file, exc)
            continue

        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                log.warning(
                    "access log %s 第 %d 行 JSON 解析失败，跳过",
                    jsonl_file.name,
                    line_no,
                )
                continue

            if not isinstance(record, dict):
                log.warning(
                    "access log %s 第 %d 行不是 JSON 对象，跳过",
                    jsonl_file.name,
                    line_no,
                )
                continue

            entry_id = record.get("entry_id")
            op = record.get("op")
            accessed_at = record.get("accessed_at")

            if (
                not isinstance(entry_id, str)
                or not isinstance(op, str)
                or not isinstance(accessed_at, str)
            ):
                log.warning(
                    "access log %s 第 %d 行字段缺失或类型错误，跳过",
                    jsonl_file.name,
                    line_no,
                )
                continue

            # Lazy-load existing entry IDs
            if existing_ids is None:
                existing_ids = set(registry.all_entry_ids())

            if entry_id not in existing_ids:
                skipped_count += 1
                continue

            rows_to_insert.append((entry_id, op, accessed_at))
            replayed_count += 1

    if rows_to_insert:
        registry.replay_access_log(rows_to_insert)
        registry.commit()

    if skipped_count > 0:
        log.warning("access log 回放跳过 %d 条不存在的 entry 记录", skipped_count)


@contextmanager
def _acquire_write_lock(lock_file: Path) -> Iterator[None]:
    """Hold an exclusive build lock for registry rebuilds."""

    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_file, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _iter_entry_files(data_root: Path) -> list[Path]:
    roots = (data_root / "entries", data_root / "sources")
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.rglob("*.md"), key=lambda path: path.as_posix()))
    return files
