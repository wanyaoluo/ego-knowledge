"""Delayed dense embedding queue for ingest/update paths."""

from __future__ import annotations

import fcntl
import json
import os
import time
from pathlib import Path

from ._dense_index import _build_embed_text, store_embedding
from ._embedding_hash import compute_embedding_content_hash
from ._validation import CachedEmbedder as CachedEmbedder
from .errors import NotFoundError
from .registry import Registry

QUEUE_DEPTH_WARN = 5000
QUEUE_DEPTH_HARD = 20000


def enqueue(data_root: Path, entry_id: str) -> bool:
    """Append an entry id to the delayed dense embedding queue."""

    queue_dir = _queue_dir(data_root)
    queue_dir.mkdir(parents=True, exist_ok=True)
    depth = _count_pending(queue_dir)
    if depth >= QUEUE_DEPTH_HARD:
        _log_queue_event(data_root, "queue_depth_hard", depth, entry_id)
        return False

    queue_file = queue_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
    record = {"entry_id": entry_id, "queued_at": _utc_now_text()}
    with queue_file.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    if depth >= QUEUE_DEPTH_WARN:
        _log_queue_event(data_root, "queue_depth_warn", depth + 1, entry_id)
    return True


def drain(
    registry: Registry,
    embedder: CachedEmbedder,
    data_root: Path,
    *,
    max_items: int = 100,
) -> dict[str, int]:
    """Consume queued entry ids idempotently and rewrite only unhandled records."""

    queue_dir = _queue_dir(data_root)
    stats = {"processed": 0, "ok": 0, "skipped": 0, "failed": 0}
    if max_items <= 0 or not queue_dir.exists():
        return stats

    records = _read_queue_records(queue_dir)
    pending_ids = list(dict.fromkeys(record["entry_id"] for record in records))
    handled_ids: set[str] = set()

    for entry_id in pending_ids[:max_items]:
        stats["processed"] += 1
        handled_ids.add(entry_id)
        try:
            entry = registry.get_entry(entry_id)
        except NotFoundError:
            stats["skipped"] += 1
            continue
        if getattr(entry.status, "value", entry.status) == "archived":
            stats["skipped"] += 1
            continue

        current_hash = compute_embedding_content_hash(entry)
        stored = registry.conn.execute(
            "SELECT embedding_content_hash FROM dense_embeddings WHERE entry_id = ?",
            (entry_id,),
        ).fetchone()
        if stored and stored["embedding_content_hash"] == current_hash:
            stats["skipped"] += 1
            continue

        try:
            embedding = embedder.embed_cached(entry_id, current_hash, _build_embed_text(entry))
            model_revision = getattr(embedder, "last_model_revision", None)
            if not isinstance(model_revision, str) or not model_revision:
                model_revision = time.strftime("%Y-%m-%d")
            store_embedding(registry, entry_id, embedding, current_hash, model_revision)
            stats["ok"] += 1
        except Exception as exc:
            stats["failed"] += 1
            handled_ids.remove(entry_id)
            _log_queue_event(data_root, "drain_failed", 0, entry_id, message=str(exc))

    _rewrite_remaining(queue_dir, records, handled_ids)
    return stats


def _read_queue_records(queue_dir: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for queue_file in sorted(queue_dir.glob("*.jsonl")):
        for line in queue_file.read_text(encoding="utf-8").splitlines():
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict) and isinstance(raw.get("entry_id"), str):
                records.append(
                    {"entry_id": raw["entry_id"], "queued_at": str(raw.get("queued_at", ""))}
                )
    return records


def _rewrite_remaining(
    queue_dir: Path,
    records: list[dict[str, str]],
    handled_ids: set[str],
) -> None:
    queue_dir.mkdir(parents=True, exist_ok=True)
    remaining_ids = [
        entry_id
        for entry_id in dict.fromkeys(record["entry_id"] for record in records)
        if entry_id not in handled_ids
    ]
    if not remaining_ids:
        for queue_file in sorted(queue_dir.glob("*.jsonl")):
            queue_file.unlink()
        return
    queue_file = queue_dir / f"{time.strftime('%Y-%m-%d')}.jsonl"
    tmp_file = queue_dir / f".{queue_file.name}.{os.getpid()}.tmp"
    with tmp_file.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            for entry_id in remaining_ids:
                handle.write(
                    json.dumps(
                        {"entry_id": entry_id, "queued_at": _utc_now_text(), "requeued": True},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    tmp_file.replace(queue_file)
    for old_file in sorted(queue_dir.glob("*.jsonl")):
        if old_file != queue_file:
            old_file.unlink()


def _count_pending(queue_dir: Path) -> int:
    total = 0
    for queue_file in queue_dir.glob("*.jsonl"):
        with queue_file.open("r", encoding="utf-8") as handle:
            total += sum(1 for _ in handle)
    return total


def _queue_dir(data_root: Path) -> Path:
    return data_root / "queue" / "dense_embed"


def _log_queue_event(
    data_root: Path,
    phase: str,
    depth: int,
    entry_id: str,
    *,
    message: str = "",
) -> None:
    log_dir = data_root / "logs" / "retrieval"
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _utc_now_text(),
        "phase": phase,
        "code": 0,
        "message": (message or f"entry_id={entry_id} depth={depth}")[:500],
        "wait": 0.0,
    }
    with (log_dir / "dense-errors.jsonl").open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _utc_now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
