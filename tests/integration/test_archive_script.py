from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ego_knowledge.core import EgoKnowledge
from ego_knowledge.scripts import archive_dirty_concepts as archive
from tests.unit.support import concept_payload, source_payload


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _seed_concepts(data_root: Path, count: int) -> list[str]:
    ek = EgoKnowledge(data_root)
    try:
        source = ek.ingest("source", source_payload(title="归档脚本来源"))
        ids: list[str] = []
        for index in range(count):
            payload = concept_payload(
                source.id,
                title=f"归档脚本概念 {index:02d}",
                search_terms=[
                    f"归档脚本概念{index:02d}",
                    f"archive-concept-{index:02d}",
                    f"ac{index:02d}",
                    "归档脚本样例",
                    f"unique-alias-{index:02d}",
                ],
            )
            ids.append(ek.ingest("concept", payload, conflict_policy="allow").id)
        return ids
    finally:
        ek.close()


def _status_map(data_root: Path, ids: list[str]) -> dict[str, str]:
    ek = EgoKnowledge(data_root)
    try:
        return {entry_id: ek.get(entry_id).status.value for entry_id in ids}
    finally:
        ek.close()


def test_archive_full_cycle_in_isolated_copy(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 10)
    today = dt.date.today().isoformat()
    archive_dir = tmp_path / "archive"
    snapshot = archive_dir / "cycle.snapshot.jsonl"
    execution = archive_dir / "cycle.execution.jsonl"
    restore_log = archive_dir / "cycle.restore.jsonl"

    dry_run = archive.run_dry_run(
        data_root=data_root,
        filter_expr=f"created_at={today} AND kind=concept AND status != 'archived'",
        snapshot_path=snapshot,
        expected_count=10,
        assume_yes=True,
    )
    assert dry_run["count"] == 10
    loaded = archive.load_snapshot(snapshot)
    assert loaded.payload_sha256
    assert loaded.id_set == set(ids)
    manifest = json.loads(snapshot.read_text(encoding="utf-8").splitlines()[0])
    assert manifest["record_type"] == "manifest"

    execute = archive.run_execute(data_root=data_root, snapshot_path=snapshot, log_path=execution)
    assert execute["total_successes"] == 10
    assert set(_status_map(data_root, ids).values()) == {"archived"}

    archive_reconcile = archive.run_reconcile(
        data_root=data_root,
        snapshot_path=snapshot,
        execution_path=execution,
    )
    assert archive_reconcile["state"] == "archived"

    restored = archive.run_restore(
        data_root=data_root,
        snapshot_path=snapshot,
        log_path=restore_log,
        execution_path=execution,
    )
    assert restored["total_successes"] == 10
    assert set(_status_map(data_root, ids).values()) == {"active"}

    restore_reconcile = archive.run_reconcile(
        data_root=data_root,
        snapshot_path=snapshot,
        execution_path=execution,
        restore_log_path=restore_log,
    )
    assert restore_reconcile["state"] == "restored"

    execution_records = _jsonl(execution)
    assert {"started", "done"}.issubset({record["status"] for record in execution_records})
    archive_execution_id = execution_records[0]["archive_execution_id"]
    status_events = _jsonl(data_root / "logs" / "refresh" / "status-events.jsonl")
    archive_events = [event for event in status_events if event.get("action") == "archive"]
    assert len(archive_events) == 10
    assert {event.get("status") for event in archive_events} == {"archived"}
    assert {event.get("archive_execution_id") for event in archive_events} == {archive_execution_id}
    restore_events = [event for event in status_events if event.get("action") == "restore"]
    assert len(restore_events) == 10
    assert {event.get("archive_execution_id") for event in restore_events} == {archive_execution_id}


def test_archive_rejects_expected_count_mismatch(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    _seed_concepts(data_root, 3)
    today = dt.date.today().isoformat()
    snapshot = tmp_path / "archive" / "mismatch.snapshot.jsonl"

    with pytest.raises(archive.ArchiveError, match="expected-count=4"):
        archive.run_dry_run(
            data_root=data_root,
            filter_expr=f"created_at={today} AND kind=concept AND status != 'archived'",
            snapshot_path=snapshot,
            expected_count=4,
            assume_yes=True,
        )

    assert not snapshot.exists()


def test_execute_repairs_started_without_done_after_update_window(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 1)
    today = dt.date.today().isoformat()
    archive_dir = tmp_path / "archive"
    snapshot = archive_dir / "repair.snapshot.jsonl"
    execution = archive_dir / "repair.execution.jsonl"
    archive.run_dry_run(
        data_root=data_root,
        filter_expr=f"created_at={today} AND kind=concept AND status != 'archived'",
        snapshot_path=snapshot,
        expected_count=1,
        assume_yes=True,
    )
    loaded = archive.load_snapshot(snapshot)
    archive_execution_id = archive._archive_execution_id(loaded, execution)
    archive._append_log(
        execution,
        archive._log_record(
            action="archive",
            status="started",
            entry_id=ids[0],
            snapshot=loaded,
            archive_execution_id=archive_execution_id,
            status_before="active",
            status_after="archived",
        ),
    )
    ek = EgoKnowledge(data_root)
    try:
        ek.update(ids[0], {"status": "archived"})
    finally:
        ek.close()

    result = archive.run_execute(data_root=data_root, snapshot_path=snapshot, log_path=execution)

    assert result["total_successes"] == 1
    done_records = [record for record in _jsonl(execution) if record.get("status") == "done"]
    assert done_records[-1]["repaired"] is True


def test_restore_requires_archive_execution_done_coverage(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    ids = _seed_concepts(data_root, 1)
    today = dt.date.today().isoformat()
    archive_dir = tmp_path / "archive"
    snapshot = archive_dir / "restore-guard.snapshot.jsonl"
    restore_log = archive_dir / "restore-guard.restore.jsonl"
    execution = archive_dir / "restore-guard.execution.jsonl"
    archive.run_dry_run(
        data_root=data_root,
        filter_expr=f"created_at={today} AND kind=concept AND status != 'archived'",
        snapshot_path=snapshot,
        expected_count=1,
        assume_yes=True,
    )
    ek = EgoKnowledge(data_root)
    try:
        ek.update(ids[0], {"status": "archived"})
    finally:
        ek.close()

    with pytest.raises(archive.ArchiveError, match="execution"):
        archive.run_restore(
            data_root=data_root,
            snapshot_path=snapshot,
            log_path=restore_log,
            execution_path=execution,
        )


def test_reconcile_rejects_restore_log_bound_to_other_execution(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    _seed_concepts(data_root, 1)
    today = dt.date.today().isoformat()
    archive_dir = tmp_path / "archive"
    snapshot = archive_dir / "bind.snapshot.jsonl"
    execution = archive_dir / "bind.execution.jsonl"
    wrong_execution = archive_dir / "other.execution.jsonl"
    restore_log = archive_dir / "bind.restore.jsonl"
    archive.run_dry_run(
        data_root=data_root,
        filter_expr=f"created_at={today} AND kind=concept AND status != 'archived'",
        snapshot_path=snapshot,
        expected_count=1,
        assume_yes=True,
    )
    archive.run_execute(data_root=data_root, snapshot_path=snapshot, log_path=execution)
    archive.run_restore(
        data_root=data_root,
        snapshot_path=snapshot,
        log_path=restore_log,
        execution_path=execution,
    )
    loaded = archive.load_snapshot(snapshot)
    wrong_archive_execution_id = archive._archive_execution_id(loaded, wrong_execution)
    wrong_records = _jsonl(execution)
    for record in wrong_records:
        record["archive_execution_id"] = wrong_archive_execution_id
    wrong_execution.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in wrong_records),
        encoding="utf-8",
    )

    with pytest.raises(archive.ArchiveError, match="restore log 未绑定当前 archive execution"):
        archive.run_reconcile(
            data_root=data_root,
            snapshot_path=snapshot,
            execution_path=wrong_execution,
            restore_log_path=restore_log,
        )
