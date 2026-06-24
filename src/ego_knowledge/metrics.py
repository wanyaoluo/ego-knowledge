"""Five meta-indicators for EgoKnowledge entries."""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from .errors import StorageError, ValidationError
from .models import Kind, SourceEntry
from .registry import Registry


@dataclass(slots=True)
class Metrics:
    evidence_strength: float = 0.0
    drift_score: float = 0.0
    compression_ratio: float = 0.0
    action_relevance: float = 0.0
    retrieval_heat: float = 0.0


def compute_evidence_strength(entry_id: str, registry: Registry) -> float:
    refs = _collect_source_refs_recursive(entry_id, registry)
    if not refs:
        return 0.0
    count = len(refs)
    diversity = len({ref.source_type for ref in refs})
    freshness = sum(_freshness_score(ref.captured_at) for ref in refs) / count
    return count * diversity * freshness


def compute_drift_score(entry_id: str, registry: Registry) -> float:
    refs = _collect_source_refs_recursive(entry_id, registry)
    if not refs:
        return 0.0
    superseded = sum(1 for ref in refs if registry.is_superseded(ref.id))
    return superseded / len(refs)


def compute_compression_ratio(entry_id: str, registry: Registry) -> float:
    return float(registry.count_out_relations(entry_id, rel_type="derived_from"))


def compute_action_relevance(entry_id: str, registry: Registry) -> float:
    entry = registry.get_entry(entry_id)
    if entry.kind == Kind.CONCEPT:
        total = registry.count_in_evidence_refs(entry_id, from_kind=Kind.DECISION.value)
        total += registry.count_in_relations(
            entry_id,
            rel_type="depends_on",
            from_kind=Kind.CONCEPT.value,
        )
        return float(total)
    if entry.kind == Kind.DOSSIER:
        total = registry.count_in_evidence_refs(entry_id, from_kind=Kind.CONCEPT.value)
        total += registry.count_in_relations(
            entry_id,
            rel_type="related",
            from_kind=Kind.DECISION.value,
        )
        return float(total)
    return 0.0


def _resolve_heat_params(registry: Registry) -> tuple[int, int]:
    """Resolve retrieval_heat parameters from registry_meta → env → defaults."""
    window_days: int | None = None
    half_life_days: int | None = None

    # Try registry_meta first
    for key, raw_value in registry.conn.execute(
        "SELECT key, value FROM registry_meta WHERE key IN (?, ?)",
        ("retrieval_heat.window_days", "retrieval_heat.half_life_days"),
    ).fetchall():
        if key == "retrieval_heat.window_days":
            try:
                window_days = int(raw_value)
            except (ValueError, TypeError):
                raise ValidationError(f"retrieval_heat.window_days 配置值非法: {raw_value!r}")
        elif key == "retrieval_heat.half_life_days":
            try:
                half_life_days = int(raw_value)
            except (ValueError, TypeError):
                raise ValidationError(f"retrieval_heat.half_life_days 配置值非法: {raw_value!r}")

    # Env fallback
    if window_days is None:
        env_w = os.environ.get("EGOKNOWLEDGE_RETRIEVAL_HEAT_WINDOW_DAYS")
        if env_w is not None:
            try:
                window_days = int(env_w)
            except ValueError:
                raise ValidationError(
                    f"环境变量 EGOKNOWLEDGE_RETRIEVAL_HEAT_WINDOW_DAYS 非法: {env_w!r}"
                )

    if half_life_days is None:
        env_h = os.environ.get("EGOKNOWLEDGE_RETRIEVAL_HEAT_HALF_LIFE_DAYS")
        if env_h is not None:
            try:
                half_life_days = int(env_h)
            except ValueError:
                raise ValidationError(
                    f"环境变量 EGOKNOWLEDGE_RETRIEVAL_HEAT_HALF_LIFE_DAYS 非法: {env_h!r}"
                )

    # Defaults
    if window_days is None:
        window_days = 90
    if half_life_days is None:
        half_life_days = 30

    if window_days <= 0 or half_life_days <= 0:
        raise ValidationError("window_days 与 half_life_days 必须为正整数")

    return window_days, half_life_days


def compute_retrieval_heat(entry_id: str, registry: Registry) -> float:
    window_days, half_life_days = _resolve_heat_params(registry)
    return registry.compute_heat_from_log(
        entry_id, window_days=window_days, half_life_days=half_life_days
    )


def compute_all(entry_id: str, registry: Registry) -> Metrics:
    return Metrics(
        evidence_strength=compute_evidence_strength(entry_id, registry),
        drift_score=compute_drift_score(entry_id, registry),
        compression_ratio=compute_compression_ratio(entry_id, registry),
        action_relevance=compute_action_relevance(entry_id, registry),
        retrieval_heat=compute_retrieval_heat(entry_id, registry),
    )


def recompute_metrics(entry_id: str, registry: Registry) -> Metrics:
    metrics = compute_all(entry_id, registry)
    registry.upsert_metrics(entry_id, metrics)
    return metrics


def recompute_for_neighbors(entry_id: str, registry: Registry) -> None:
    affected = {entry_id} | set(registry.neighbors(entry_id, direction="both"))
    for affected_id in sorted(affected):
        recompute_metrics(affected_id, registry)


def recompute_metrics_with_neighbors(entry_id: str, registry: Registry) -> list[str]:
    affected = {entry_id} | set(registry.neighbors(entry_id, direction="both"))
    for affected_id in sorted(affected):
        recompute_metrics(affected_id, registry)
    return sorted(affected)


def full_recompute(registry: Registry) -> int:
    from ._graph_authority import compute_pagerank, persist_authority_scores

    total = 0
    owns_transaction = not registry.conn.in_transaction
    try:
        if owns_transaction:
            registry.conn.execute("BEGIN IMMEDIATE")
        for entry_id in registry.all_entry_ids():
            recompute_metrics(entry_id, registry)
            total += 1
        scores = compute_pagerank(registry)
        persist_authority_scores(registry, scores, commit=False)
        if owns_transaction:
            registry.commit()
    except Exception:
        if owns_transaction:
            registry.conn.rollback()
        raise
    return total


def _zero_metrics() -> dict[str, object]:
    return {
        "evidence_strength": 0.0,
        "drift_score": 0.0,
        "compression_ratio": 0.0,
        "action_relevance": 0.0,
        "retrieval_heat": 0.0,
    }


def _access_log_dir(registry: Registry) -> Path:
    """Return the jsonl access log directory derived from registry's data root."""
    return registry._data_root / "logs" / "access"


def _append_jsonl(log_dir: Path, records: list[dict[str, str]]) -> None:
    """Append records to a date-partitioned jsonl file. Raises StorageError on failure."""
    if not records:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    log_file = log_dir / f"{today}.jsonl"
    try:
        with log_file.open("a", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                for record in records:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise StorageError(f"写入 access log jsonl 失败: {exc}") from exc


def _record_access(registry: Registry, entry_id: str, *, op: str) -> None:
    accessed_at = _utc_now_text()
    registry.conn.execute(
        """
        INSERT INTO access_log(entry_id, op, accessed_at)
        VALUES(?, ?, ?)
        """,
        (entry_id, op, accessed_at),
    )
    registry.commit()
    _append_jsonl(
        _access_log_dir(registry),
        [{"entry_id": entry_id, "op": op, "accessed_at": accessed_at}],
    )


def _record_access_many(registry: Registry, entry_ids: list[str], *, op: str) -> None:
    if not entry_ids:
        return
    accessed_at = _utc_now_text()
    rows = [(entry_id, op, accessed_at) for entry_id in entry_ids]
    registry.conn.executemany(
        """
        INSERT INTO access_log(entry_id, op, accessed_at)
        VALUES(?, ?, ?)
        """,
        rows,
    )
    registry.commit()
    _append_jsonl(
        _access_log_dir(registry),
        [{"entry_id": eid, "op": op, "accessed_at": accessed_at} for eid in entry_ids],
    )


def _recompute_ids(registry: Registry, entry_ids: set[str]) -> None:
    if len(entry_ids) == 1:
        only_id = next(iter(entry_ids))
        if registry.has_entry(only_id):
            recompute_for_neighbors(only_id, registry)
            registry.commit()
        return
    for entry_id in sorted(entry_ids):
        if registry.has_entry(entry_id):
            recompute_metrics(entry_id, registry)
    registry.commit()


def _log_status_transition(
    data_root: Path,
    entry_id: str,
    status: str,
    *,
    status_before: str | None = None,
    context: dict[str, object] | None = None,
) -> None:
    log_dir = data_root / "logs" / "refresh"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "status-events.jsonl"
    record: dict[str, object] = {
        "entry_id": entry_id,
        "status": status,
        "logged_at": _utc_now_text(),
    }
    if status_before is not None:
        record["status_before"] = status_before
    if context:
        record.update(context)
    try:
        with log_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def _collect_source_refs_recursive(
    entry_id: str,
    registry: Registry,
    max_depth: int = 10,
) -> list[SourceEntry]:
    visited: set[str] = {entry_id}
    queue: deque[tuple[str, int]] = deque([(entry_id, 0)])
    sources: dict[str, SourceEntry] = {}

    while queue:
        current_id, depth = queue.popleft()
        if depth >= max_depth:
            continue
        current = registry.get_entry(current_id)
        if current.kind == Kind.SOURCE:
            sources[current.id] = cast(SourceEntry, current)
            continue
        for neighbor_id in registry.out_refs(
            current_id,
            types=["source_refs", "evidence_refs", "derived_from"],
        ):
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            queue.append((neighbor_id, depth + 1))

    return list(sources.values())


def _freshness_score(captured_at: date | None) -> float:
    if captured_at is None:
        return 0.5
    days = (date.today() - captured_at).days
    if days < 0:
        return 0.5
    return float(0.5 ** (days / 365.0))


def _utc_now_text() -> str:
    return _dt.datetime.now(_dt.UTC).replace(microsecond=0).isoformat()
