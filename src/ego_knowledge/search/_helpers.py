"""Shared helpers for search backends and main router."""

from __future__ import annotations

from typing import cast

from ..models import Entry


def _merge_backends(current: list[str], new: list[str]) -> list[str]:
    merged = list(current)
    for backend in new:
        if backend not in merged:
            merged.append(backend)
    return merged


def _build_snippet(entry: Entry, needles: list[str]) -> str | None:
    text = (entry.body or "").strip()
    title = entry.title.strip()
    haystack = text or title
    if not haystack:
        return None

    lowered = haystack.casefold()
    for needle in needles:
        query = needle.casefold()
        if not query:
            continue
        index = lowered.find(query)
        if index >= 0:
            start = max(0, index - 24)
            end = min(len(haystack), index + len(needle) + 36)
            return haystack[start:end].strip()
    return haystack[:96].strip()


def _match_filters(entry: Entry, filters: dict[str, object]) -> bool:
    for key, expected in filters.items():
        value = getattr(entry, key, None)
        if key == "tags":
            tags = set(entry.tags)
            if isinstance(expected, (list, tuple, set)):
                expected_tags = {str(item) for item in expected}
                if not expected_tags.issubset(tags):
                    return False
                continue
            if str(expected) not in tags:
                return False
            continue
        if hasattr(value, "value"):
            value = cast(object, getattr(value, "value"))
        if value != expected:
            return False
    return True
