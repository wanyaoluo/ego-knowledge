"""Shared helpers: types, snapshot IO, hash, status log (≥2 stages)."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from ego_knowledge.paths import sha256_text_hex
from ego_knowledge.registry import Registry

JsonMap = dict[str, object]


class ArchiveError(RuntimeError):
    """Raised when a safety gate blocks the archive workflow."""


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: Path
    manifest: dict[str, object]
    entries: list[dict[str, object]]

    @property
    def ids(self) -> list[str]:
        return [_string_field(entry, "id", label="snapshot.entry") for entry in self.entries]

    @property
    def id_set(self) -> set[str]:
        return set(self.ids)

    @property
    def payload_sha256(self) -> str:
        return _string_field(self.manifest, "payload_sha256", label="snapshot.manifest")

    @property
    def snapshot_ts(self) -> str:
        return _string_field(self.manifest, "snapshot_ts", label="snapshot.manifest")


@dataclass(frozen=True, slots=True)
class ExecutionLogState:
    started: set[str]
    done: set[str]
    failed: set[str]
    log_archive_execution_ids: set[str]


def load_snapshot(snapshot_path: Path) -> Snapshot:
    if not snapshot_path.exists():
        raise ArchiveError(f"快照不存在: {snapshot_path}")
    _validate_sidecar_hash_if_present(snapshot_path)
    raw_lines = snapshot_path.read_text(encoding="utf-8").splitlines(keepends=True)
    if len(raw_lines) < 1:
        raise ArchiveError("快照为空")
    if any(not line.strip() for line in raw_lines):
        raise ArchiveError("快照含空行，已拒绝")

    manifest = _parse_json_line(raw_lines[0], label="manifest")
    if manifest.get("record_type") != "manifest":
        raise ArchiveError("快照首行必须是 manifest")
    entry_raw_lines = raw_lines[1:]
    payload_hash = sha256_text_hex("".join(entry_raw_lines))
    if manifest.get("payload_sha256") != payload_hash:
        raise ArchiveError("快照 payload sha256 校验失败")
    entries = [_parse_json_line(line, label="entry") for line in entry_raw_lines]
    if any(entry.get("record_type") != "entry" for entry in entries):
        raise ArchiveError("快照 entry 行缺少 record_type=entry")
    ids = [_string_field(entry, "id", label="snapshot.entry") for entry in entries]
    duplicates = sorted(_duplicates(ids))
    if duplicates:
        raise ArchiveError(f"快照含重复 ID: {duplicates}")
    entry_count = manifest.get("entry_count")
    if not isinstance(entry_count, int) or entry_count != len(entries):
        raise ArchiveError("快照 manifest entry_count 与实际行数不一致")
    if manifest.get("entry_ids_sha256") != _ids_sha256(ids):
        raise ArchiveError("快照 ID 集 sha256 校验失败")
    return Snapshot(path=snapshot_path, manifest=manifest, entries=entries)


def _validate_sidecar_hash_if_present(snapshot_path: Path) -> None:
    sidecar = snapshot_path.with_suffix(snapshot_path.suffix + ".sha256")
    if not sidecar.exists():
        raise ArchiveError("快照 sha256 sidecar 不存在，已拒绝")
    expected = sidecar.read_text(encoding="utf-8").strip().split()[0]
    actual = sha256_text_hex(snapshot_path.read_text(encoding="utf-8"))
    if expected != actual:
        raise ArchiveError("快照文件 sha256 sidecar 校验失败")


def _read_log_done_ids(
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
    return state.done


def _read_log_state(
    log_path: Path,
    *,
    action: str,
    snapshot: Snapshot,
) -> ExecutionLogState:
    if not log_path.exists():
        raise ArchiveError(f"execution log 不存在: {log_path}")
    started: set[str] = set()
    done: set[str] = set()
    failed: set[str] = set()
    log_archive_execution_ids: set[str] = set()
    for line_no, line in enumerate(
        log_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        record = _json_object(_loads_json(line, label="execution-log"), label="execution-log")
        if record.get("snapshot_payload_sha256") != snapshot.payload_sha256:
            raise ArchiveError(f"{log_path} 第 {line_no} 行不属于当前快照")
        if record.get("action") != action:
            continue
        archive_execution_id = record.get("archive_execution_id")
        if isinstance(archive_execution_id, str) and archive_execution_id:
            log_archive_execution_ids.add(archive_execution_id)
        elif record.get("status") not in {"success"}:
            raise ArchiveError(f"{log_path} 第 {line_no} 行缺少 archive_execution_id")
        entry_id = _optional_string_field(record, "id", label="execution-log")
        if record.get("status") in {"started", "done", "failed", "success"}:
            if entry_id not in snapshot.id_set:
                raise ArchiveError(f"日志 ID 不在快照内: {entry_id}")
        if record.get("status") == "started":
            started.add(entry_id)
        elif record.get("status") in {"done", "success"}:
            done.add(entry_id)
            failed.discard(entry_id)
        elif record.get("status") == "failed":
            if entry_id not in done:
                failed.add(entry_id)
    return ExecutionLogState(
        started=started,
        done=done,
        failed=failed,
        log_archive_execution_ids=log_archive_execution_ids,
    )


def _append_log(log_path: Path, record: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(_json_line(record))
        handle.flush()
        os.fsync(handle.fileno())


def _log_record(
    *,
    action: str,
    status: str,
    entry_id: str,
    snapshot: Snapshot,
    status_before: str | None = None,
    status_after: str | None = None,
    error: str | None = None,
    archive_execution_id: str | None = None,
    restore_execution_id: str | None = None,
    repaired: bool = False,
    fallback_reason: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "action": action,
        "status": status,
        "id": entry_id,
        "snapshot": str(snapshot.path),
        "snapshot_payload_sha256": snapshot.payload_sha256,
        "logged_at": _utc_now_text(),
    }
    optional = {
        "status_before": status_before,
        "status_after": status_after,
        "error": error,
        "archive_execution_id": archive_execution_id,
        "restore_execution_id": restore_execution_id,
        "fallback_reason": fallback_reason,
    }
    record.update({k: v for k, v in optional.items() if v is not None})
    if repaired:
        record["repaired"] = True
    return record


def _archive_execution_id(snapshot: Snapshot, log_path: Path) -> str:
    seed = "\n".join([str(snapshot.path), str(log_path), snapshot.payload_sha256])
    return "archive-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]


def _assert_exact_coverage(
    snapshot: Snapshot,
    success_ids: set[str],
    *,
    label: str,
) -> None:
    missing = sorted(snapshot.id_set - success_ids)
    extra = sorted(success_ids - snapshot.id_set)
    if missing or extra:
        raise ArchiveError(f"{label} 覆盖率对账失败: missing={missing[:5]} extra={extra[:5]}")


def _registry_status_map(data_root: Path, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        registry.init_schema()
        placeholders = ",".join("?" for _ in ids)
        rows = registry.conn.execute(
            f"SELECT id, status FROM entries WHERE id IN ({placeholders})",
            tuple(ids),
        ).fetchall()
        return {
            _row_string(row, "id", label="entries"): _row_string(row, "status", label="entries")
            for row in rows
        }
    finally:
        registry.close()


def _archived_event_counts(
    data_root: Path,
    snapshot: Snapshot,
    *,
    archive_execution_id: str,
) -> Counter[str]:
    log_file = data_root / "logs" / "refresh" / "status-events.jsonl"
    counts: Counter[str] = Counter()
    if not log_file.exists():
        return counts
    snapshot_ids = snapshot.id_set
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = _json_object(_loads_json(line, label="status-events"), label="status-events")
        entry_id = _optional_string_field(record, "entry_id", label="status-events")
        if (
            entry_id in snapshot_ids
            and record.get("status") == "archived"
            and record.get("archive_execution_id") == archive_execution_id
        ):
            counts[entry_id] += 1
    return counts


def _entry_path(data_root: Path, file_path: str) -> Path:
    path = Path(file_path)
    target = path if path.is_absolute() else data_root / path
    try:
        target.resolve(strict=False).relative_to(data_root.resolve(strict=False))
    except ValueError as exc:
        raise ArchiveError(f"entry file_path 越过 data-root: {file_path}") from exc
    return target


def _file_sha256(path: Path) -> str:
    if not path.exists():
        raise ArchiveError(f"条目文件不存在: {path}")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _ids_sha256(ids: list[str]) -> str:
    # 经 paths.sha256_text_hex 薄包装：保持与 snapshot payload / sidecar 哈希
    # 同走真源，消除本文件对 hashlib 的字面重复调用。尾部 ``\n`` 是历史契约，
    # 已写盘的旧 snapshot 用此格式生成 entry_ids_sha256，不可省略。
    return sha256_text_hex("\n".join(ids) + ("\n" if ids else ""))


def _json_line(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _parse_json_line(line: str, *, label: str) -> dict[str, object]:
    parsed = _loads_json(line, label=f"快照 {label}")
    return _json_object(parsed, label=f"快照 {label}")


def _loads_json(line: str, *, label: str) -> object:
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ArchiveError(f"{label} JSON 解析失败: {exc}") from exc


def _is_string_dict(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(k, str) for k in value)


def _json_object(value: object, *, label: str) -> dict[str, object]:
    if not _is_string_dict(value):
        raise ArchiveError(f"{label} 不是 JSON object")
    return value


def _row_string(row: Mapping[str, object], key: str, *, label: str) -> str:
    value = row[key]
    if not isinstance(value, str) or not value:
        raise ArchiveError(f"{label}.{key} 缺少字符串值")
    return value


def _string_field(mapping: dict[str, object], key: str, *, label: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value:
        raise ArchiveError(f"{label}.{key} 缺少字符串值")
    return value


def _optional_string_field(mapping: dict[str, object], key: str, *, label: str) -> str:
    value = mapping.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ArchiveError(f"{label}.{key} 必须是字符串")
    return value


def _duplicates(values: list[str]) -> set[str]:
    return {v for v, c in Counter(values).items() if c > 1}


def _utc_now_text() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
