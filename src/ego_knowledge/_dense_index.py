"""Dense embedding storage and rebuild orchestration for EgoKnowledge."""

from __future__ import annotations

import json
import struct
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from packaging.version import Version

from ._dense_embedder import EMBEDDING_DIM, MAX_BATCH, EmbedResult
from ._embedding_hash import compute_embedding_content_hash
from .errors import NotFoundError
from .registry import REGISTRY_SCHEMA_VERSION, Registry

if TYPE_CHECKING:
    from .models import Entry


class Embedder(Protocol):
    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        """Return an object with embeddings and model_revision attributes."""


MODEL_ID = "bge-m3"
SEMANTIC_INDEX_NAME = "dense_bge_m3"
INDEX_SCHEMA_VERSION = "2.2"
DEFAULT_BATCH_SIZE = MAX_BATCH
DEFAULT_BATCH_INTERVAL_SECONDS = 0.5
PROGRESS_LOG_NAME = "rebuild-dense-index-progress.jsonl"


def store_embedding(
    registry: Registry,
    entry_id: str,
    embedding: list[float],
    embedding_content_hash: str,
    model_revision: str,
) -> None:
    """Serialize one float32 embedding into ``dense_embeddings``."""

    _ensure_dense_schema(registry)
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(f"embedding 维度错: expected {EMBEDDING_DIM}, got {len(embedding)}")
    blob = struct.pack(f"<{EMBEDDING_DIM}f", *embedding)
    registry.conn.execute(
        """
        INSERT INTO dense_embeddings(
            entry_id, embedding, embedding_content_hash,
            model_id, model_revision, indexed_at
        )
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(entry_id) DO UPDATE SET
            embedding = excluded.embedding,
            embedding_content_hash = excluded.embedding_content_hash,
            model_id = excluded.model_id,
            model_revision = excluded.model_revision,
            indexed_at = excluded.indexed_at
        """,
        (entry_id, blob, embedding_content_hash, MODEL_ID, model_revision),
    )
    registry.commit()


def load_all_embeddings(registry: Registry) -> dict[str, list[float]]:
    """Load all stored dense vectors into memory for later semantic search."""

    _ensure_dense_schema(registry)
    rows = registry.conn.execute(
        "SELECT entry_id, embedding FROM dense_embeddings ORDER BY entry_id"
    ).fetchall()
    result: dict[str, list[float]] = {}
    expected_bytes = EMBEDDING_DIM * 4
    for row in rows:
        blob = bytes(cast(bytes, row["embedding"]))
        if len(blob) != expected_bytes:
            raise ValueError(
                f"embedding BLOB 长度错 {row['entry_id']}: "
                f"expected {expected_bytes}, got {len(blob)}"
            )
        result[cast(str, row["entry_id"])] = list(struct.unpack(f"<{EMBEDDING_DIM}f", blob))
    return result


def stale_entry_ids(registry: Registry) -> list[str]:
    """Return active entries whose stored dense hash is missing or out of date."""

    _ensure_dense_schema(registry)
    rows = registry.conn.execute(
        """
        SELECT e.id, d.embedding_content_hash AS stored_hash
          FROM entries AS e
          LEFT JOIN dense_embeddings AS d
            ON d.entry_id = e.id
         WHERE e.status != 'archived'
         ORDER BY e.id
        """
    ).fetchall()
    stale: list[str] = []
    for row in rows:
        entry_id = cast(str, row["id"])
        try:
            entry = registry.get_entry(entry_id)
        except NotFoundError:
            continue
        current_hash = compute_embedding_content_hash(entry)
        stored_hash = cast(str | None, row["stored_hash"])
        if stored_hash is None or stored_hash != current_hash:
            stale.append(entry_id)
    return stale


def rebuild_all(
    registry: Registry,
    embedder: Embedder,
    *,
    only_stale: bool = False,
    progress_log: Path | None = None,
    resume: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    batch_interval_seconds: float = DEFAULT_BATCH_INTERVAL_SECONDS,
) -> dict[str, int]:
    """Rebuild dense index with batching, progress logging and resume support."""

    _ensure_dense_schema(registry)
    if batch_size < 1 or batch_size > MAX_BATCH:
        raise ValueError(f"batch_size 必须在 1..{MAX_BATCH} 之间")

    target_ids = stale_entry_ids(registry) if only_stale else _all_entry_ids(registry)
    completed_ids = _read_completed_ids(progress_log) if resume and progress_log else set()
    if completed_ids:
        target_ids = [entry_id for entry_id in target_ids if entry_id not in completed_ids]

    stats = {"total": len(target_ids), "ok": 0, "failed": 0, "skipped": 0}
    for batch_start in range(0, len(target_ids), batch_size):
        batch_ids = target_ids[batch_start : batch_start + batch_size]
        batch_data: list[tuple[str, str, str]] = []
        for entry_id in batch_ids:
            try:
                entry = registry.get_entry(entry_id)
            except NotFoundError:
                stats["skipped"] += 1
                _append_progress(progress_log, entry_id, "skipped", "entry_missing")
                continue
            text = _build_embed_text(entry)
            if not text.strip():
                stats["skipped"] += 1
                _append_progress(progress_log, entry_id, "skipped", "empty_text")
                continue
            batch_data.append((entry_id, text, compute_embedding_content_hash(entry)))

        if not batch_data:
            continue

        try:
            result = embedder.embed_batch([item[1] for item in batch_data])
            if len(result.embeddings) != len(batch_data):
                raise ValueError(
                    "embedding 数量不匹配: "
                    f"expected {len(batch_data)}, got {len(result.embeddings)}"
                )
            for embedding in result.embeddings:
                if len(embedding) != EMBEDDING_DIM:
                    raise ValueError(
                        f"embedding 维度错: expected {EMBEDDING_DIM}, got {len(embedding)}"
                    )
            for (entry_id, _text, entry_hash), embedding in zip(
                batch_data,
                result.embeddings,
                strict=True,
            ):
                store_embedding(registry, entry_id, embedding, entry_hash, result.model_revision)
                _append_progress(progress_log, entry_id, "ok", result.model_revision)
            stats["ok"] += len(batch_data)
        except Exception as exc:
            message = str(exc)
            stats["failed"] += len(batch_data)
            for entry_id, _text, _entry_hash in batch_data:
                _append_progress(progress_log, entry_id, "failed", message)
            continue

        if batch_interval_seconds > 0:
            time.sleep(batch_interval_seconds)

    _update_semantic_index_meta(registry)
    return stats


def default_progress_log(data_root: Path) -> Path:
    """Return the canonical rebuild progress log path under data_root."""

    return data_root / "logs" / "retrieval" / PROGRESS_LOG_NAME


def _build_embed_text(entry: Entry) -> str:
    """Build the text payload used for entry embeddings."""

    parts = [
        entry.title,
        " ".join(entry.tags),
        " ".join(entry.aliases),
        " ".join(entry.search_terms),
        (entry.body or "")[:2000],
    ]
    return "\n".join(part for part in parts if part)


def _update_semantic_index_meta(registry: Registry) -> None:
    """Upsert semantic index metadata using the existing five-column schema."""

    _ensure_dense_schema(registry)
    model_revision = time.strftime("%Y-%m-%d")
    registry.conn.execute(
        """
        INSERT INTO semantic_index_meta(
            index_name, model_id, model_revision, index_schema_version, indexed_at
        )
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(index_name) DO UPDATE SET
            model_id = excluded.model_id,
            model_revision = excluded.model_revision,
            index_schema_version = excluded.index_schema_version,
            indexed_at = excluded.indexed_at
        """,
        (SEMANTIC_INDEX_NAME, MODEL_ID, model_revision, INDEX_SCHEMA_VERSION),
    )
    registry.commit()


def _ensure_dense_schema(registry: Registry) -> None:
    row = registry.conn.execute(
        "SELECT value FROM registry_meta WHERE key = 'schema_version'"
    ).fetchone()
    version = cast(str | None, row["value"] if row is not None else None)
    if (
        version is None
        or Version(version) < Version(INDEX_SCHEMA_VERSION)
        or Version(version) > Version(REGISTRY_SCHEMA_VERSION)
    ):
        raise RuntimeError(
            "dense 索引需要 schema 2.2+；历史一次性迁移脚本已退役；"
            "请从备份恢复到匹配版本或重建 2.3 数据根后再生成索引。"
        )
    tables = {
        cast(str, item["name"])
        for item in registry.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "dense_embeddings" not in tables or "semantic_index_meta" not in tables:
        raise RuntimeError("dense 索引表缺失；请重建 2.3 数据根后再生成索引。")


def _all_entry_ids(registry: Registry) -> list[str]:
    rows = registry.conn.execute(
        "SELECT id FROM entries WHERE status != 'archived' ORDER BY id"
    ).fetchall()
    return [cast(str, row["id"]) for row in rows]


def _append_progress(
    progress_log: Path | None,
    entry_id: str,
    status: str,
    message: str,
) -> None:
    if progress_log is None:
        return
    progress_log.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entry_id": entry_id,
        "status": status,
        "message": message[:500],
    }
    with progress_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_completed_ids(progress_log: Path | None) -> set[str]:
    if progress_log is None or not progress_log.exists():
        return set()
    completed: set[str] = set()
    for line in progress_log.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        entry_id = record.get("entry_id")
        if status in {"ok", "skipped"} and isinstance(entry_id, str):
            completed.add(entry_id)
    return completed
