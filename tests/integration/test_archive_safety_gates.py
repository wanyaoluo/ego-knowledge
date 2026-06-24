"""Integration tests for archive execute/restore safety gates.

Covers G16-G28 from the QA remediation matrix: preflight execute checks
(registry status change, file hash change), fallback archive, and preflight
restore checks (non-archived status, status_before=archived, event chain).
These tests require real Registry + file system.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ego_knowledge.core import EgoKnowledge
from ego_knowledge.scripts import archive_dirty_concepts as archive
from ego_knowledge.scripts.archive_dirty_concepts._execute import _preflight_execute
from ego_knowledge.scripts.archive_dirty_concepts._helpers import (
    ArchiveError,
    _archive_execution_id,
    load_snapshot,
)
from ego_knowledge.scripts.archive_dirty_concepts._restore import _preflight_restore
from tests.unit.support import concept_payload, source_payload

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _seed_concepts(data_root: Path, count: int) -> list[str]:
    ek = EgoKnowledge(data_root)
    try:
        source = ek.ingest("source", source_payload(title="安全门禁来源"))
        ids: list[str] = []
        for index in range(count):
            payload = concept_payload(
                source.id,
                title=f"安全门禁概念 {index:02d}",
                search_terms=[
                    f"安全门禁概念{index:02d}",
                    f"safety-gate-{index:02d}",
                    f"sg{index:02d}",
                    "安全门禁样例",
                    f"sg-alias-{index:02d}",
                ],
            )
            ids.append(ek.ingest("concept", payload, conflict_policy="allow").id)
        return ids
    finally:
        ek.close()


def _run_dry_run(
    data_root: Path,
    snapshot_path: Path,
    count: int,
) -> None:
    # 从 registry 读出 concept 实际写入的 created_at，而非 dt.date.today()，
    # 消除"种子写入与 dry_run filter 计算跨日"导致的 filter 失配（理论 flaky 路径）。
    from ego_knowledge.registry import Registry

    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        registry.init_schema()
        row = registry.conn.execute(
            "SELECT created_at FROM entries WHERE kind='concept' LIMIT 1"
        ).fetchone()
        target_date = row[0] if row and row[0] else dt.date.today().isoformat()
    finally:
        registry.close()

    archive.run_dry_run(
        data_root=data_root,
        filter_expr=f"created_at={target_date} AND kind=concept AND status != 'archived'",
        snapshot_path=snapshot_path,
        expected_count=count,
        assume_yes=True,
    )


# ===========================================================================
# G16: preflight execute — registry status changed since snapshot
# ===========================================================================


def test_preflight_execute_rejects_registry_status_change(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 1)
    snapshot_path = tmp_path / "archive" / "g16.snapshot.jsonl"
    _run_dry_run(data_root, snapshot_path, 1)
    snapshot = load_snapshot(snapshot_path)

    # Change registry status to something else
    ek = EgoKnowledge(data_root)
    try:
        ek.update(ids[0], {"status": "archived"})
    finally:
        ek.close()

    with pytest.raises(ArchiveError, match="registry status changed"):
        _preflight_execute(data_root, snapshot.entries)


# ===========================================================================
# G17: preflight execute — file hash changed since snapshot
# ===========================================================================


def test_preflight_execute_rejects_file_hash_change(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 1)
    snapshot_path = tmp_path / "archive" / "g17.snapshot.jsonl"
    _run_dry_run(data_root, snapshot_path, 1)
    snapshot = load_snapshot(snapshot_path)

    # Tamper the entry file to change its hash
    ek = EgoKnowledge(data_root)
    try:
        entry = ek.get(ids[0])
    finally:
        ek.close()
    file_path = data_root / entry.file_path
    original = file_path.read_text(encoding="utf-8")
    file_path.write_text(original + "# tampered\n", encoding="utf-8")

    with pytest.raises(ArchiveError, match="file hash changed"):
        _preflight_execute(data_root, snapshot.entries)


# ===========================================================================
# G19: fallback archive status succeeds for dirty entries
# ===========================================================================


def test_fallback_archive_status_succeeds(tmp_path: Path) -> None:
    """Simulate the fallback path: direct registry write + frontmatter patch."""
    from ego_knowledge.scripts.archive_dirty_concepts._execute import (
        _fallback_archive_status,
    )

    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 1)
    snapshot_path = tmp_path / "archive" / "g19.snapshot.jsonl"
    execution_path = tmp_path / "archive" / "g19.execution.jsonl"
    _run_dry_run(data_root, snapshot_path, 1)
    snapshot = load_snapshot(snapshot_path)
    archive_execution_id = _archive_execution_id(snapshot, execution_path)

    # Call fallback directly (simulates ValidationError-triggered path)
    _fallback_archive_status(
        data_root=data_root,
        entry_id=ids[0],
        status_before="active",
        snapshot=snapshot,
        archive_execution_id=archive_execution_id,
    )

    # Verify registry status is now archived
    from ego_knowledge.registry import Registry

    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        registry.init_schema()
        row = registry.conn.execute("SELECT status FROM entries WHERE id=?", (ids[0],)).fetchone()
        assert row is not None and row["status"] == "archived"
    finally:
        registry.close()


# ===========================================================================
# G25: preflight restore — target ID not in archived state
# ===========================================================================


def test_preflight_restore_rejects_non_archived(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 2)
    snapshot_path = tmp_path / "archive" / "g25.snapshot.jsonl"
    execution_path = tmp_path / "archive" / "g25.execution.jsonl"
    _run_dry_run(data_root, snapshot_path, 2)
    snapshot = load_snapshot(snapshot_path)

    # Archive only one entry via execute
    archive.run_execute(data_root=data_root, snapshot_path=snapshot_path, log_path=execution_path)

    # Manually un-archive one entry to simulate non-archived state
    ek = EgoKnowledge(data_root)
    try:
        ek.update(ids[0], {"status": "active"})
    finally:
        ek.close()

    # remaining = all entries (simulate fresh restore attempt)
    archive_execution_id = _archive_execution_id(snapshot, execution_path)
    with pytest.raises(ArchiveError, match="必须全部为 archived"):
        _preflight_restore(
            data_root,
            snapshot,
            snapshot.entries,
            restored_ids=set(),
            archive_done_ids=snapshot.id_set,
            archive_execution_id=archive_execution_id,
        )


# ===========================================================================
# G26: preflight restore — status_before is already "archived", reject
# ===========================================================================


def test_preflight_restore_rejects_archived_status_before(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    _seed_concepts(data_root, 1)
    snapshot_path = tmp_path / "archive" / "g26.snapshot.jsonl"
    execution_path = tmp_path / "archive" / "g26.execution.jsonl"
    _run_dry_run(data_root, snapshot_path, 1)

    # Tamper snapshot to make status_before = "archived"
    snapshot = load_snapshot(snapshot_path)
    tampered_entries = []
    for entry in snapshot.entries:
        tampered = dict(entry)
        tampered["status_before"] = "archived"
        tampered_entries.append(tampered)

    # Execute normally so registry is archived
    archive.run_execute(data_root=data_root, snapshot_path=snapshot_path, log_path=execution_path)

    # Use a manually constructed Snapshot with status_before=archived
    fake_snapshot = archive.Snapshot(
        path=snapshot.path,
        manifest=snapshot.manifest,
        entries=tampered_entries,
    )
    archive_execution_id = _archive_execution_id(snapshot, execution_path)

    with pytest.raises(ArchiveError, match="已经是 archived"):
        _preflight_restore(
            data_root,
            fake_snapshot,
            fake_snapshot.entries,
            restored_ids=set(),
            archive_done_ids=fake_snapshot.id_set,
            archive_execution_id=archive_execution_id,
        )


# ===========================================================================
# G28: preflight restore — status-events event chain abnormal
# ===========================================================================


def test_preflight_restore_rejects_bad_event_chain(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    _seed_concepts(data_root, 1)
    snapshot_path = tmp_path / "archive" / "g28.snapshot.jsonl"
    execution_path = tmp_path / "archive" / "g28.execution.jsonl"
    _run_dry_run(data_root, snapshot_path, 1)
    snapshot = load_snapshot(snapshot_path)

    # Execute to get archived status
    archive.run_execute(data_root=data_root, snapshot_path=snapshot_path, log_path=execution_path)
    archive_execution_id = _archive_execution_id(snapshot, execution_path)

    # Tamper status-events to break event chain (delete events)
    events_file = data_root / "logs" / "refresh" / "status-events.jsonl"
    if events_file.exists():
        events_file.write_text("", encoding="utf-8")

    with pytest.raises(ArchiveError, match="事件链异常"):
        _preflight_restore(
            data_root,
            snapshot,
            snapshot.entries,
            restored_ids=set(),
            archive_done_ids=snapshot.id_set,
            archive_execution_id=archive_execution_id,
        )
