"""Transactional file + SQLite write helpers."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ._validation import _asdict, _is_dataclass_instance
from .errors import BodyRecoveryFailedSnapshotMissing, StorageError


@dataclass(frozen=True, slots=True)
class TransactionSnapshot:
    txn_id: str
    snapshot_dir: Path
    manifest_path: Path
    snapshot_name: str
    original_path: Path


@contextmanager
def transactional_write(
    target_path: Path,
    new_content: str,
    conn: sqlite3.Connection,
    *,
    snapshot_entry_id: str | None = None,
) -> Iterator[sqlite3.Cursor]:
    """Write file + SQLite changes under a single recovery-aware protocol."""

    snapshot_requested = snapshot_entry_id is not None
    snapshot = _create_transaction_snapshot(snapshot_entry_id, target_path)
    tmp_path = target_path.with_suffix(target_path.suffix + ".tmp")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    _write_tmp_file(tmp_path, new_content)

    cursor = conn.cursor()
    renamed = False
    try:
        try:
            cursor.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            _cleanup_tmp(tmp_path)
            raise StorageError(f"无法获取 SQLite 写锁: {exc}") from exc

        try:
            yield cursor
            os.rename(tmp_path, target_path)
            renamed = True
            conn.commit()
        except Exception as exc:
            if renamed:
                _restore_after_rename(snapshot, target_path, snapshot_requested, exc)
                message = (
                    "SQLite 事务失败，文件与数据库已回滚或已按快照恢复。"
                    if isinstance(exc, sqlite3.OperationalError)
                    else "事务失败，文件与数据库已回滚或已按快照恢复。"
                )
                raise StorageError(message) from exc
            if isinstance(exc, sqlite3.OperationalError):
                raise StorageError("SQLite 事务失败，文件与数据库已回滚或已按快照恢复。") from exc
            if isinstance(exc, OSError):
                raise StorageError(f"文件 rename 失败 {target_path}: {exc}") from exc
            raise StorageError(f"事务写入失败 {target_path}: {exc}") from exc
    except StorageError:
        conn.rollback()
        _cleanup_tmp(tmp_path)
        raise
    finally:
        cursor.close()


def _create_transaction_snapshot(
    entry_id: str | None,
    target_path: Path,
) -> TransactionSnapshot | None:
    if entry_id is None or not target_path.exists():
        return None
    txn_id = uuid.uuid4().hex
    snapshot_dir = _snapshot_root(target_path) / txn_id
    snapshot_name = f"{hashlib.sha256(entry_id.encode('utf-8')).hexdigest()}.md"
    try:
        snapshot_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chmod(snapshot_dir, 0o700)
        shutil.copy2(target_path, snapshot_dir / snapshot_name)
        manifest_path = snapshot_dir / "manifest.json"
        manifest: dict[str, object] = {
            "txn_id": txn_id,
            "created_at": _dt.datetime.now(tz=_dt.UTC).isoformat(),
            "entries": [
                {
                    "entry_id": entry_id,
                    "snapshot_name": snapshot_name,
                    "original_path": str(target_path),
                }
            ],
        }
        _write_manifest(manifest_path, manifest)
    except OSError as exc:
        try:
            _write_recovery_log(target_path, f"SNAPSHOT_FAILED entry={entry_id} err={exc}")
        except OSError:
            pass
        return None
    return TransactionSnapshot(
        txn_id=txn_id,
        snapshot_dir=snapshot_dir,
        manifest_path=manifest_path,
        snapshot_name=snapshot_name,
        original_path=target_path,
    )


def _snapshot_root(target_path: Path) -> Path:
    return _find_data_root(target_path) / ".txn-snapshots"


def _write_manifest(path: Path, manifest: dict[str, object]) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp_path, 0o600)
    os.rename(tmp_path, path)
    os.chmod(path, 0o600)


def _restore_from_snapshot(snapshot: TransactionSnapshot | None, target_path: Path) -> None:
    if snapshot is None:
        _write_recovery_log_best_effort(target_path, "RESTORE_FAILED snapshot missing")
        raise BodyRecoveryFailedSnapshotMissing()
    snapshot_path = snapshot.snapshot_dir / snapshot.snapshot_name
    try:
        if not snapshot_path.exists():
            raise FileNotFoundError(snapshot_path)
        shutil.copy2(snapshot_path, snapshot.original_path)
    except OSError as exc:
        _write_recovery_log_best_effort(
            target_path,
            f"RESTORE_FAILED txn={snapshot.txn_id} err={exc}",
        )
        try:
            recovery_log = snapshot.snapshot_dir / "recovery.log"
            recovery_log.write_text(
                f"RESTORE_FAILED txn={snapshot.txn_id} err={exc}\n",
                encoding="utf-8",
            )
            os.chmod(recovery_log, 0o600)
        except OSError:
            pass
        raise BodyRecoveryFailedSnapshotMissing() from exc


def _restore_after_rename(
    snapshot: TransactionSnapshot | None,
    target_path: Path,
    snapshot_requested: bool,
    exc: Exception,
) -> None:
    if snapshot is None and snapshot_requested:
        _restore_from_snapshot(snapshot, target_path)
    if snapshot is None:
        _write_recovery_log_best_effort(target_path, f"COMMIT 失败: {exc}")
        raise StorageError(
            "commit 失败且无 body 快照；请运行 ek doctor --repair 检查文件/registry 漂移。"
        ) from exc
    _restore_from_snapshot(snapshot, target_path)
    _write_recovery_log_best_effort(target_path, f"COMMIT 失败: {exc}")


def _write_recovery_log_best_effort(path: Path, message: str) -> None:
    try:
        _write_recovery_log(path, message)
    except OSError:
        pass


def _write_recovery_log(path: Path, message: str) -> None:
    """Append a recovery record for a half-failed transactional write."""

    data_root = _find_data_root(path)
    log_dir = data_root / "logs" / "refresh"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "recovery.log"

    record: dict[str, str] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "target_path": str(path),
        "message": message,
    }
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.chmod(log_file, 0o600)


def _find_data_root(path: Path) -> Path:
    """Walk upwards until finding the EgoKnowledge data root with registry/."""

    resolved = path.resolve()
    fallback = path.parent.parent
    for candidate in (resolved, *resolved.parents):
        if (candidate / "registry").is_dir() and (
            candidate == fallback or candidate.name == "EgoKnowledge"
        ):
            return candidate
    return fallback


def _write_tmp_file(tmp_path: Path, new_content: str) -> None:
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(new_content)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        _cleanup_tmp(tmp_path)
        raise StorageError(f"临时文件写入失败 {tmp_path}: {exc}") from exc


def _cleanup_tmp(tmp_path: Path) -> None:
    tmp_path.unlink(missing_ok=True)


def write_snapshot(data: dict[str, object], data_root: Path) -> Path:
    snap_dir = data_root / "logs" / "stats"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_path = snap_dir / f"{_dt.date.today().isoformat()}.json"
    snap_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return snap_path


def _json_default(obj: object) -> object:
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if _is_dataclass_instance(obj):
        return _asdict(obj)
    if isinstance(obj, Path):
        return obj.as_posix()
    return obj
