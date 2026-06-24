"""Shared helper functions for L4 diagnose rules.

Centralizes utility functions previously duplicated across
structure/decay/action/push modules.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import TYPE_CHECKING

from ..models import Entry, NoteEntry, SourceEntry

if TYPE_CHECKING:
    from ..registry import Registry

# Shared by action.crystallize / push.crystallize.
_NOTE_GROUP_IGNORED_TAGS = frozenset({"测试"})


def metric_value(entry: Entry, key: str) -> float:
    """Read a numeric metric from an entry, defaulting to 0.0."""
    if entry.metrics is None:
        return 0.0
    value = entry.metrics.get(key, 0.0)
    return float(value) if isinstance(value, int | float) else 0.0


def target_path(entry: Entry) -> str | None:
    """Return file_path string or None."""
    return str(entry.file_path) if entry.file_path is not None else None


def collect_source_refs(
    entry_id: str,
    registry: Registry,
    max_depth: int = 10,
) -> list[SourceEntry]:
    """BFS walk to collect reachable SourceEntry nodes."""
    visited: set[str] = {entry_id}
    frontier: deque[tuple[str, int]] = deque([(entry_id, 0)])
    sources: dict[str, SourceEntry] = {}
    while frontier:
        current_id, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        current = registry.get_entry(current_id)
        if isinstance(current, SourceEntry):
            sources[current.id] = current
            continue
        for neighbor_id in registry.out_refs(
            current_id,
            types=["source_refs", "evidence_refs", "derived_from"],
        ):
            if neighbor_id in visited:
                continue
            visited.add(neighbor_id)
            frontier.append((neighbor_id, depth + 1))
    return list(sources.values())


def contradicts_count(entry_id: str, registry: Registry) -> int:
    """Count total contradicts relations (in + out)."""
    return registry.count_in_relations(entry_id, "contradicts") + registry.count_out_relations(
        entry_id,
        "contradicts",
    )


def unabsorbed_note_groups(registry: Registry) -> dict[str, list[NoteEntry]]:
    """Group unabsorbed notes by tag or source key."""
    groups: dict[str, list[NoteEntry]] = defaultdict(list)
    for entry in registry.all_entries_by_kind("note"):
        note = entry if isinstance(entry, NoteEntry) else None
        if note is None or registry.count_in_relations(note.id, "derived_from") > 0:
            continue
        for key in note_group_keys(note):
            groups[key].append(note)
    return groups


def note_group_keys(note: NoteEntry) -> set[str]:
    """Derive grouping keys from a note's tags or source refs."""
    keys = {f"tag:{tag}" for tag in note.tags if tag not in _NOTE_GROUP_IGNORED_TAGS}
    if keys:
        return keys
    if note.source_refs:
        return {f"source:{source_id}" for source_id in note.source_refs}
    return set()
