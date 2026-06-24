"""Graph neighbor expansion backend."""

from __future__ import annotations

from ...registry import Registry
from .._types import SearchResult


def graph_neighbors(
    registry: Registry,
    seeds: dict[str, SearchResult],
    *,
    include_archived: bool = False,
) -> dict[str, SearchResult]:
    neighbors: dict[str, SearchResult] = {}
    for seed_id, seed_result in seeds.items():
        for neighbor_id in registry.neighbors(
            seed_id,
            direction="both",
            include_archived=include_archived,
        ):
            if neighbor_id == seed_id or neighbor_id in seeds:
                continue
            score = seed_result.score * 0.5
            existing = neighbors.get(neighbor_id)
            if existing is None or score > existing.score:
                neighbors[neighbor_id] = SearchResult(
                    id=neighbor_id,
                    score=score,
                    backends=["graph"],
                )
            elif "graph" not in existing.backends:
                existing.backends.append("graph")
    return neighbors
