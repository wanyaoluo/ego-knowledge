"""Reconcile stage: four-way reconcile of archive/restore state."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ego_knowledge.errors import ValidationError
from ego_knowledge.frontmatter import read_file

from ._helpers import (
    ArchiveError,
    Snapshot,
    _archive_execution_id,
    _archived_event_counts,
    _assert_exact_coverage,
    _entry_path,
    _json_object,
    _loads_json,
    _optional_string_field,
    _read_log_state,
    _registry_status_map,
    _string_field,
    load_snapshot,
)


def run_reconcile(
    *,
    data_root: Path,
    snapshot_path: Path,
    execution_path: Path,
    restore_log_path: Path | None = None,
) -> dict[str, object]:
    snapshot = load_snapshot(snapshot_path)
    archive_state = _read_log_state(execution_path, action="archive", snapshot=snapshot)
    success_ids = archive_state.done
    _assert_exact_coverage(snapshot, success_ids, label="execution")
    archive_execution_id = _archive_execution_id(snapshot, execution_path)
    archive_execution_ids = archive_state.log_archive_execution_ids
    if archive_execution_ids and archive_execution_ids != {archive_execution_id}:
        raise ArchiveError(
            "archive execution log 与当前 execution 路径不匹配: "
            f"expected={archive_execution_id} actual={sorted(archive_execution_ids)}"
        )
    if restore_log_path is not None:
        restore_state = _read_log_state(restore_log_path, action="restore", snapshot=snapshot)
        restore_execution_ids = restore_state.log_archive_execution_ids
        if restore_execution_ids != {archive_execution_id}:
            raise ArchiveError(
                "restore log 未绑定当前 archive execution: "
                f"expected={archive_execution_id} actual={sorted(restore_execution_ids)}"
            )
        restore_ids = restore_state.done
        _assert_exact_coverage(snapshot, restore_ids, label="restore")
        return _reconcile_restored(data_root, snapshot, archive_execution_id=archive_execution_id)
    return _reconcile_archived(data_root, snapshot, archive_execution_id=archive_execution_id)


def _reconcile_archived(
    data_root: Path,
    snapshot: Snapshot,
    *,
    archive_execution_id: str,
) -> dict[str, object]:
    registry_status = _registry_status_map(data_root, snapshot.ids)
    bad_registry = sorted(
        entry_id for entry_id in snapshot.ids if registry_status.get(entry_id) != "archived"
    )
    frontmatter_status = _frontmatter_status_map(data_root, snapshot.entries)
    bad_frontmatter = sorted(
        entry_id for entry_id in snapshot.ids if frontmatter_status.get(entry_id) != "archived"
    )
    event_counts = _archived_event_counts(
        data_root,
        snapshot,
        archive_execution_id=archive_execution_id,
    )
    bad_events = sorted(entry_id for entry_id in snapshot.ids if event_counts.get(entry_id, 0) != 1)
    if bad_registry or bad_frontmatter or bad_events:
        raise ArchiveError(
            "archive 对账失败: "
            f"registry={bad_registry[:5]} frontmatter={bad_frontmatter[:5]} events={bad_events[:5]}"
        )
    return {
        "ok": True,
        "mode": "reconcile",
        "state": "archived",
        "count": len(snapshot.entries),
        "registry": "matched",
        "frontmatter": "matched",
        "status_events": "matched",
        "coverage": "matched",
    }


def _reconcile_restored(
    data_root: Path,
    snapshot: Snapshot,
    *,
    archive_execution_id: str,
) -> dict[str, object]:
    expected = {
        _string_field(entry, "id", label="snapshot.entry"): _string_field(
            entry,
            "status_before",
            label="snapshot.entry",
        )
        for entry in snapshot.entries
    }
    registry_status = _registry_status_map(data_root, snapshot.ids)
    bad_registry = sorted(
        entry_id for entry_id, status in expected.items() if registry_status.get(entry_id) != status
    )
    frontmatter_status = _frontmatter_status_map(data_root, snapshot.entries)
    bad_frontmatter = sorted(
        entry_id
        for entry_id, status in expected.items()
        if frontmatter_status.get(entry_id) != status
    )
    archive_event_counts = _archived_event_counts(
        data_root,
        snapshot,
        archive_execution_id=archive_execution_id,
    )
    restore_event_counts = _restore_event_counts(
        data_root,
        snapshot,
        archive_execution_id=archive_execution_id,
    )
    bad_events = sorted(
        entry_id
        for entry_id in snapshot.ids
        if (archive_event_counts.get(entry_id, 0) != 1 or restore_event_counts.get(entry_id, 0) < 1)
    )
    if bad_registry or bad_frontmatter or bad_events:
        raise ArchiveError(
            "restore 对账失败: "
            f"registry={bad_registry[:5]} frontmatter={bad_frontmatter[:5]} events={bad_events[:5]}"
        )
    return {
        "ok": True,
        "mode": "reconcile",
        "state": "restored",
        "count": len(snapshot.entries),
        "registry": "matched",
        "frontmatter": "matched",
        "status_events": "matched",
        "coverage": "matched",
    }


def _frontmatter_status_map(data_root: Path, entries: list[dict[str, object]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in entries:
        entry_id = _string_field(entry, "id", label="snapshot.entry")
        path = _entry_path(data_root, _string_field(entry, "file_path", label="snapshot.entry"))
        status_from_frontmatter: str | None = None
        try:
            frontmatter, _ = read_file(str(path))
            status_from_frontmatter = _optional_string_field(
                frontmatter,
                "status",
                label="frontmatter",
            )
        except ValidationError:
            raw = path.read_text(encoding="utf-8")
            m = re.search(r"^status:\s*(.+)", raw, re.MULTILINE)
            status_from_frontmatter = m.group(1).strip() if m else None
        result[entry_id] = status_from_frontmatter or "unknown"
    return result


def _restore_event_counts(
    data_root: Path,
    snapshot: Snapshot,
    *,
    archive_execution_id: str,
) -> Counter[str]:
    log_file = data_root / "logs" / "refresh" / "status-events.jsonl"
    counts: Counter[str] = Counter()
    if not log_file.exists():
        return counts
    expected = {
        _string_field(entry, "id", label="snapshot.entry"): _string_field(
            entry,
            "status_before",
            label="snapshot.entry",
        )
        for entry in snapshot.entries
    }
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = _json_object(_loads_json(line, label="status-events"), label="status-events")
        entry_id = _optional_string_field(record, "entry_id", label="status-events")
        if (
            record.get("status") == expected.get(entry_id)
            and record.get("archive_execution_id") == archive_execution_id
        ):
            counts[entry_id] += 1
    return counts
