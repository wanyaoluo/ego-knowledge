"""Graph authority propagation via PageRank over EgoKnowledge relations."""

from __future__ import annotations

import datetime as _dt
import logging
from typing import cast

from .registry import Registry

DAMPING = 0.85
MAX_ITER = 100
TOLERANCE = 1e-6

log = logging.getLogger(__name__)


def compute_pagerank(registry: Registry, *, include_archived: bool = False) -> dict[str, float]:
    """Compute PageRank scores for the active graph.

    The default graph matches consumer-facing EgoKnowledge APIs: archived entries
    are excluded so bulk archived test data cannot dilute active authority.
    """

    all_ids = _fetch_entry_ids(registry, include_archived=include_archived)
    if not all_ids:
        return {}

    out_edges = _fetch_out_edges(registry, all_ids)
    n = len(all_ids)
    base_rank = (1.0 - DAMPING) / n
    ranks = {entry_id: 1.0 / n for entry_id in all_ids}
    converged = False
    last_delta = 0.0

    for iteration in range(1, MAX_ITER + 1):
        dangling_rank = sum(ranks[src] for src, targets in out_edges.items() if not targets)
        shared_dangling = DAMPING * dangling_rank / n
        new_ranks = {entry_id: base_rank + shared_dangling for entry_id in all_ids}

        for src, targets in out_edges.items():
            if not targets:
                continue
            contribution = DAMPING * ranks[src] / len(targets)
            for target in targets:
                new_ranks[target] += contribution

        last_delta = sum(abs(new_ranks[entry_id] - ranks[entry_id]) for entry_id in all_ids)
        ranks = new_ranks
        if last_delta < TOLERANCE:
            converged = True
            break

    if not converged:
        _log_non_convergence(registry, MAX_ITER, last_delta)

    return ranks


def persist_authority_scores(
    registry: Registry,
    scores: dict[str, float],
    *,
    commit: bool = True,
) -> None:
    """Write PageRank results back to entry_metrics.authority_score.

    Rows outside ``scores`` are reset to zero to avoid stale authority after an
    entry becomes archived or disappears from the active graph.
    """

    registry.conn.execute("UPDATE entry_metrics SET authority_score = 0")
    if scores:
        registry.conn.executemany(
            "UPDATE entry_metrics SET authority_score = ? WHERE entry_id = ?",
            [(score, entry_id) for entry_id, score in scores.items()],
        )
    if commit:
        registry.commit()


def _fetch_entry_ids(registry: Registry, *, include_archived: bool) -> list[str]:
    if include_archived:
        rows = registry.conn.execute("SELECT id FROM entries ORDER BY id").fetchall()
    else:
        rows = registry.conn.execute(
            "SELECT id FROM entries WHERE status != 'archived' ORDER BY id"
        ).fetchall()
    return [cast(str, row["id"]) for row in rows]


def _fetch_out_edges(registry: Registry, all_ids: list[str]) -> dict[str, list[str]]:
    id_set = set(all_ids)
    out_edges: dict[str, list[str]] = {entry_id: [] for entry_id in all_ids}
    rows = registry.conn.execute("SELECT source_id, target_id FROM relations").fetchall()
    for row in rows:
        source_id = cast(str, row["source_id"])
        target_id = cast(str, row["target_id"])
        if source_id in id_set and target_id in id_set:
            out_edges[source_id].append(target_id)
    return out_edges


def _log_non_convergence(registry: Registry, iterations: int, delta: float) -> None:
    message = f"PageRank reached max_iter={iterations} with delta={delta:.8f}"
    log.warning(message)
    log_path = registry._data_root / "logs" / "pagerank.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = _dt.datetime.now(tz=_dt.UTC).replace(microsecond=0).isoformat()
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        log.debug("failed to write pagerank warning log", exc_info=True)
