"""Dense semantic search backend."""

from __future__ import annotations

import fcntl
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..._dense_embedder import EMBEDDING_DIM
from ..._dense_index import load_all_embeddings
from ...registry import Registry
from .._types import SearchResult

if TYPE_CHECKING:
    from .._types import DenseSearchEmbedder


DENSE_WEIGHT = 1.5


def cosine_similarity_scores(
    query_embedding: list[float],
    stored_embeddings: list[list[float]],
) -> list[float]:
    if len(query_embedding) != EMBEDDING_DIM or not stored_embeddings:
        return []
    matrix = np.asarray(stored_embeddings, dtype=np.float32)
    query_vector = np.asarray(query_embedding, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[1] != EMBEDDING_DIM:
        return []

    query_norm = float(np.linalg.norm(query_vector))
    if query_norm <= 0:
        return [0.0 for _ in stored_embeddings]
    embedding_norms = np.linalg.norm(matrix, axis=1)
    denominators = embedding_norms * query_norm
    raw_scores = matrix @ query_vector
    scores = np.divide(
        raw_scores,
        denominators,
        out=np.zeros_like(raw_scores, dtype=np.float32),
        where=denominators > 0,
    )
    return [float(score) for score in scores]


def search_dense(
    query: str,
    *,
    limit: int,
    registry: Registry,
    embedder: DenseSearchEmbedder,
    dense_weight: float,
    data_root: Path,
) -> dict[str, SearchResult]:
    try:
        query_result = embedder.embed_batch([query])
        query_embedding = query_result.embeddings[0]
        stored = load_all_embeddings(registry)
        if not stored:
            return {}
        entry_ids = list(stored.keys())
        scores = cosine_similarity_scores(query_embedding, list(stored.values()))
        if len(scores) != len(entry_ids):
            raise ValueError(f"dense 分数数量不匹配: expected {len(entry_ids)}, got {len(scores)}")
    except Exception as exc:
        log_dense_error(data_root, "dense_search_error", str(exc))
        return {}

    ranked: list[tuple[str, float]] = []
    for entry_id, score in zip(entry_ids, scores, strict=True):
        if score <= 0:
            continue
        ranked.append((entry_id, score * dense_weight))
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return {
        entry_id: SearchResult(id=entry_id, score=score, backends=["dense"])
        for entry_id, score in ranked[:limit]
    }


def log_dense_error(data_root: Path, phase: str, message: str) -> None:
    log_dir = data_root / "logs" / "retrieval"
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "code": 0,
        "message": message[:500],
        "wait": 0.0,
    }
    log_file = log_dir / "dense-errors.jsonl"
    with log_file.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
