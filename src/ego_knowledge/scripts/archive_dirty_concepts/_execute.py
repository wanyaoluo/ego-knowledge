"""Execute stage: archive entries from a signed snapshot."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ego_knowledge.core import EgoKnowledge
from ego_knowledge.errors import ValidationError
from ego_knowledge.frontmatter import read_file
from ego_knowledge.metrics import _log_status_transition
from ego_knowledge.registry import Registry

from ._helpers import (
    ArchiveError,
    Snapshot,
    _append_log,
    _archive_execution_id,
    _archived_event_counts,
    _entry_path,
    _file_sha256,
    _log_record,
    _read_log_done_ids,
    _read_log_state,
    _registry_status_map,
    _string_field,
    load_snapshot,
)


def run_execute(
    *,
    data_root: Path,
    snapshot_path: Path,
    log_path: Path,
) -> dict[str, object]:
    snapshot = load_snapshot(snapshot_path)
    archive_execution_id = _archive_execution_id(snapshot, log_path)
    existing_successes = _read_log_done_ids(
        log_path,
        action="archive",
        snapshot=snapshot,
        archive_execution_id=archive_execution_id,
    )
    incomplete_ids = _read_log_incomplete_ids(
        log_path,
        action="archive",
        snapshot=snapshot,
        archive_execution_id=archive_execution_id,
    )
    if incomplete_ids:
        _repair_incomplete_archive_log(
            data_root,
            snapshot,
            log_path,
            incomplete_ids,
            archive_execution_id=archive_execution_id,
        )
        existing_successes = _read_log_done_ids(
            log_path,
            action="archive",
            snapshot=snapshot,
            archive_execution_id=archive_execution_id,
        )
    remaining = [
        entry
        for entry in snapshot.entries
        if _string_field(entry, "id", label="snapshot.entry") not in existing_successes
    ]
    _preflight_execute(data_root, remaining)

    successes = 0
    failures = 0
    ek = EgoKnowledge(data_root)
    try:
        for entry in remaining:
            entry_id = _string_field(entry, "id", label="snapshot.entry")
            status_before = _string_field(entry, "status_before", label="snapshot.entry")
            _append_log(
                log_path,
                _log_record(
                    action="archive",
                    status="started",
                    entry_id=entry_id,
                    snapshot=snapshot,
                    archive_execution_id=archive_execution_id,
                    status_before=status_before,
                    status_after="archived",
                ),
            )
            try:
                updated = ek.update(
                    entry_id,
                    {"status": "archived"},
                    status_context={
                        "source": "archive_dirty_concepts",
                        "action": "archive",
                        "archive_execution_id": archive_execution_id,
                        "snapshot": str(snapshot.path),
                        "snapshot_payload_sha256": snapshot.payload_sha256,
                    },
                )
            except ValidationError as exc:
                # Fallback: dirty entries with full-width punctuation fail schema
                # validation via ek.update(), but archiving only needs to change
                # status.  Bypass the normal update path and write directly.
                _fallback_archive_status(
                    data_root=data_root,
                    entry_id=entry_id,
                    status_before=status_before,
                    snapshot=snapshot,
                    archive_execution_id=archive_execution_id,
                )
                successes += 1
                _append_log(
                    log_path,
                    _log_record(
                        action="archive",
                        status="done",
                        entry_id=entry_id,
                        snapshot=snapshot,
                        archive_execution_id=archive_execution_id,
                        status_before=status_before,
                        status_after="archived",
                        fallback_reason=str(exc),
                    ),
                )
                continue
            except Exception as exc:  # noqa: BLE001
                failures += 1
                _append_log(
                    log_path,
                    _log_record(
                        action="archive",
                        status="failed",
                        entry_id=entry_id,
                        snapshot=snapshot,
                        archive_execution_id=archive_execution_id,
                        error=str(exc),
                    ),
                )
                continue
            successes += 1
            _append_log(
                log_path,
                _log_record(
                    action="archive",
                    status="done",
                    entry_id=entry_id,
                    snapshot=snapshot,
                    archive_execution_id=archive_execution_id,
                    status_before=status_before,
                    status_after=updated.status.value,
                ),
            )
    finally:
        ek.close()

    total_successes = len(existing_successes) + successes
    result = {
        "ok": failures == 0,
        "mode": "execute",
        "snapshot_count": len(snapshot.entries),
        "already_successful": len(existing_successes),
        "successes": successes,
        "failures": failures,
        "total_successes": total_successes,
        "log": str(log_path),
    }
    if failures:
        raise ArchiveError(json.dumps(result, ensure_ascii=False))
    return result


def _preflight_execute(data_root: Path, entries: list[dict[str, object]]) -> None:
    entry_ids = [_string_field(entry, "id", label="snapshot.entry") for entry in entries]
    current = _registry_status_map(data_root, entry_ids)
    mismatches: list[str] = []
    for entry in entries:
        path = _entry_path(data_root, _string_field(entry, "file_path", label="snapshot.entry"))
        entry_id = _string_field(entry, "id", label="snapshot.entry")
        status_before = _string_field(entry, "status_before", label="snapshot.entry")
        if current.get(entry_id) != status_before:
            mismatches.append(f"{entry_id}: registry status changed before execute")
            continue
        current_hash = _file_sha256(path)
        file_hash_before = _string_field(entry, "file_hash_before", label="snapshot.entry")
        if current_hash != file_hash_before:
            mismatches.append(f"{entry_id}: file hash changed")
            continue
        try:
            frontmatter, _ = read_file(str(path))
        except Exception:  # noqa: BLE001 — dirty entries may fail schema validation
            continue  # 脏条目本身就有 schema 问题，跳过 frontmatter 校验
        if frontmatter.get("status") != status_before:
            mismatches.append(f"{entry_id}: status changed before execute")
    if mismatches:
        raise ArchiveError("execute 前置校验失败: " + "; ".join(mismatches[:5]))


def _fallback_archive_status(
    *,
    data_root: Path,
    entry_id: str,
    status_before: str,
    snapshot: Snapshot,
    archive_execution_id: str,
) -> None:
    """Bypass ek.update() for dirty entries: write archived to registry/events/frontmatter."""
    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        registry.init_schema()
        registry.conn.execute(
            "UPDATE entries SET status='archived' WHERE id=?",
            (entry_id,),
        )
        registry.conn.commit()
    finally:
        registry.close()

    _log_status_transition(
        data_root,
        entry_id,
        "archived",
        status_before=status_before,
        context={
            "source": "archive_dirty_concepts",
            "action": "archive",
            "archive_execution_id": archive_execution_id,
            "snapshot": str(snapshot.path),
            "snapshot_payload_sha256": snapshot.payload_sha256,
            "fallback": True,
        },
    )

    entry_by_id = {_string_field(e, "id", label="snapshot.entry"): e for e in snapshot.entries}
    entry_data = entry_by_id[entry_id]
    file_path = _entry_path(
        data_root, _string_field(entry_data, "file_path", label="snapshot.entry")
    )
    _patch_yaml_status(file_path, "archived")


def _patch_yaml_status(file_path: Path, new_status: str) -> None:
    """Patch frontmatter status line via regex (fallback for dirty entries)."""
    text = file_path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        return
    _, fm_raw, body = parts
    # Replace the status line via regex to avoid full YAML round-trip
    updated_fm = re.sub(
        r"^(status\s*:\s*).+$",
        rf"\g<1>{new_status}",
        fm_raw,
        flags=re.MULTILINE,
    )
    file_path.write_text(f"---\n{updated_fm}---\n{body}", encoding="utf-8")


def _repair_incomplete_archive_log(
    data_root: Path,
    snapshot: Snapshot,
    log_path: Path,
    incomplete_ids: set[str],
    *,
    archive_execution_id: str,
) -> None:
    current = _registry_status_map(data_root, sorted(incomplete_ids))
    entry_by_id = {
        _string_field(entry, "id", label="snapshot.entry"): entry for entry in snapshot.entries
    }
    event_counts = _archived_event_counts(
        data_root,
        snapshot,
        archive_execution_id=archive_execution_id,
    )
    blocked: list[str] = []
    for entry_id in sorted(incomplete_ids):
        status_before = _string_field(
            entry_by_id[entry_id],
            "status_before",
            label="snapshot.entry",
        )
        if current.get(entry_id) == status_before:
            continue
        if current.get(entry_id) != "archived":
            blocked.append(entry_id)
            continue
        if event_counts.get(entry_id, 0) == 0:
            _log_status_transition(
                data_root,
                entry_id,
                "archived",
                status_before=status_before,
                context={
                    "source": "archive_dirty_concepts",
                    "action": "archive_repair",
                    "archive_execution_id": archive_execution_id,
                    "snapshot": str(snapshot.path),
                    "snapshot_payload_sha256": snapshot.payload_sha256,
                },
            )
        _append_log(
            log_path,
            _log_record(
                action="archive",
                status="done",
                entry_id=entry_id,
                snapshot=snapshot,
                archive_execution_id=archive_execution_id,
                status_before=status_before,
                status_after="archived",
                repaired=True,
            ),
        )
    if blocked:
        raise ArchiveError(
            f"execution log 存在未完成 ID 且当前未 archived，需人工确认: {blocked[:5]}"
        )


def _read_log_incomplete_ids(
    log_path: Path,
    *,
    action: str,
    snapshot: Snapshot,
    archive_execution_id: str | None = None,
) -> set[str]:
    if not log_path.exists():
        return set()
    state = _read_log_state(log_path, action=action, snapshot=snapshot)
    if archive_execution_id is not None:
        log_archive_execution_ids = state.log_archive_execution_ids
        if log_archive_execution_ids and log_archive_execution_ids != {archive_execution_id}:
            raise ArchiveError(
                "archive execution log 与当前 execution 路径不匹配: "
                f"expected={archive_execution_id} actual={sorted(log_archive_execution_ids)}"
            )
    return state.started - state.done
