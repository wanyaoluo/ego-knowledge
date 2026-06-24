"""Restore stage: restore archived entries to their pre-archive status."""

from __future__ import annotations

import json
from pathlib import Path

from ego_knowledge.core import EgoKnowledge

from ._helpers import (
    ArchiveError,
    Snapshot,
    _append_log,
    _archive_execution_id,
    _archived_event_counts,
    _assert_exact_coverage,
    _log_record,
    _read_log_done_ids,
    _read_log_state,
    _registry_status_map,
    _string_field,
    load_snapshot,
)


def run_restore(
    *,
    data_root: Path,
    snapshot_path: Path,
    log_path: Path,
    execution_path: Path | None = None,
) -> dict[str, object]:
    if execution_path is None:
        raise ArchiveError(
            "restore requires archive execution log coverage; pass execution_path/--execution"
        )
    snapshot = load_snapshot(snapshot_path)
    archive_state = _read_log_state(execution_path, action="archive", snapshot=snapshot)
    archive_done_ids = archive_state.done
    _assert_exact_coverage(snapshot, archive_done_ids, label="execution")
    archive_execution_id = _archive_execution_id(snapshot, execution_path)
    archive_execution_ids = archive_state.log_archive_execution_ids
    if archive_execution_ids and archive_execution_ids != {archive_execution_id}:
        raise ArchiveError(
            "archive execution log 与当前 execution 路径不匹配: "
            f"expected={archive_execution_id} actual={sorted(archive_execution_ids)}"
        )
    restore_execution_id = _restore_execution_id(snapshot, log_path, archive_execution_id)
    existing_successes = _read_log_done_ids(
        log_path,
        action="restore",
        snapshot=snapshot,
        archive_execution_id=archive_execution_id,
    )
    remaining = [
        entry
        for entry in snapshot.entries
        if _string_field(entry, "id", label="snapshot.entry") not in existing_successes
    ]
    _preflight_restore(
        data_root,
        snapshot,
        remaining,
        existing_successes,
        archive_done_ids=archive_done_ids,
        archive_execution_id=archive_execution_id,
    )

    successes = 0
    failures = 0
    ek = EgoKnowledge(data_root)
    try:
        for entry in remaining:
            entry_id = _string_field(entry, "id", label="snapshot.entry")
            target_status = _string_field(entry, "status_before", label="snapshot.entry")
            try:
                updated = ek.update(
                    entry_id,
                    {"status": target_status},
                    status_context={
                        "source": "archive_dirty_concepts",
                        "action": "restore",
                        "archive_execution_id": archive_execution_id,
                        "restore_execution_id": restore_execution_id,
                        "snapshot": str(snapshot.path),
                        "snapshot_payload_sha256": snapshot.payload_sha256,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                failures += 1
                _append_log(
                    log_path,
                    _log_record(
                        action="restore",
                        status="failed",
                        entry_id=entry_id,
                        snapshot=snapshot,
                        archive_execution_id=archive_execution_id,
                        restore_execution_id=restore_execution_id,
                        error=str(exc),
                    ),
                )
                continue
            successes += 1
            _append_log(
                log_path,
                _log_record(
                    action="restore",
                    status="done",
                    entry_id=entry_id,
                    snapshot=snapshot,
                    archive_execution_id=archive_execution_id,
                    restore_execution_id=restore_execution_id,
                    status_before="archived",
                    status_after=updated.status.value,
                ),
            )
    finally:
        ek.close()

    result = {
        "ok": failures == 0,
        "mode": "restore",
        "snapshot_count": len(snapshot.entries),
        "already_successful": len(existing_successes),
        "successes": successes,
        "failures": failures,
        "total_successes": len(existing_successes) + successes,
        "log": str(log_path),
    }
    if failures:
        raise ArchiveError(json.dumps(result, ensure_ascii=False))
    return result


def _preflight_restore(
    data_root: Path,
    snapshot: Snapshot,
    remaining: list[dict[str, object]],
    restored_ids: set[str],
    *,
    archive_done_ids: set[str],
    archive_execution_id: str,
) -> None:
    if snapshot.id_set - archive_done_ids:
        raise ArchiveError("restore 仅允许恢复 archive execution 中 done 覆盖的 ID")
    current = _registry_status_map(data_root, snapshot.ids)
    remaining_ids = {_string_field(entry, "id", label="snapshot.entry") for entry in remaining}
    not_archived = sorted(
        entry_id for entry_id in remaining_ids if current.get(entry_id) != "archived"
    )
    if not_archived:
        raise ArchiveError(f"restore 前目标 ID 必须全部为 archived，异常: {not_archived[:5]}")
    expected = {
        _string_field(entry, "id", label="snapshot.entry"): _string_field(
            entry,
            "status_before",
            label="snapshot.entry",
        )
        for entry in snapshot.entries
    }
    bad_restored = sorted(
        entry_id for entry_id in restored_ids if current.get(entry_id) != expected.get(entry_id)
    )
    if bad_restored:
        raise ArchiveError(f"restore 续跑发现已恢复 ID 状态异常: {bad_restored[:5]}")
    event_counts = _archived_event_counts(
        data_root,
        snapshot,
        archive_execution_id=archive_execution_id,
    )
    bad_events = sorted(entry_id for entry_id in snapshot.ids if event_counts.get(entry_id, 0) != 1)
    if bad_events:
        raise ArchiveError(f"restore 前 status-events archived 事件链异常: {bad_events[:5]}")
    for entry in remaining:
        status_before = _string_field(entry, "status_before", label="snapshot.entry")
        if status_before == "archived":
            entry_id = _string_field(entry, "id", label="snapshot.entry")
            raise ArchiveError(f"快照原始状态已经是 archived，拒绝 restore: {entry_id}")


def _restore_execution_id(
    snapshot: Snapshot,
    log_path: Path,
    archive_execution_id: str,
) -> str:
    import hashlib

    seed = "\n".join(
        [
            str(snapshot.path),
            str(log_path),
            snapshot.payload_sha256,
            archive_execution_id,
        ]
    )
    return "restore-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
