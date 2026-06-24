"""Exact-match backend: SQL statements and term matching."""

from __future__ import annotations

import sqlite3
from typing import cast

from ...errors import StorageError
from ...registry import Registry
from ...unicode_utils import to_nfc
from .._types import SearchResult


def _exact_sql_statements(
    include_title_substring: bool,
) -> list[tuple[str, float]]:
    statements = [
        ("SELECT id FROM entries WHERE lower(title) = lower(?)", 80.0),
        (
            "SELECT entry_id AS id FROM aliases WHERE lower(alias_nfc) = lower(?)",
            90.0,
        ),
        (
            "SELECT entry_id AS id FROM entry_search_terms WHERE lower(term) = lower(?)",
            75.0,
        ),
        ("SELECT entry_id AS id FROM entry_tags WHERE lower(tag) = lower(?)", 70.0),
    ]
    if include_title_substring:
        statements.append(
            (
                "SELECT id FROM entries WHERE instr(lower(title), lower(?)) > 0",
                55.0,
            )
        )
    return statements


def exact_term_matches(
    registry: Registry,
    term: str,
    *,
    include_title_substring: bool,
) -> dict[str, SearchResult]:
    from .._helpers import _merge_backends

    normalized = to_nfc(term).strip()
    if not normalized:
        return {}

    matches: dict[str, SearchResult] = {}
    for sql, score in _exact_sql_statements(include_title_substring):
        try:
            rows = registry.conn.execute(sql, (normalized,)).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(f"执行精确检索失败: {exc}") from exc
        for row in rows:
            entry_id = cast(str, row["id"])
            existing = matches.get(entry_id)
            if existing is None or score > existing.score:
                matches[entry_id] = SearchResult(
                    id=entry_id,
                    score=score,
                    backends=["exact"],
                )
            else:
                existing.backends = _merge_backends(existing.backends, ["exact"])
    return matches


def exact_query_matches(
    registry: Registry,
    query: str,
) -> dict[str, SearchResult]:
    matches = exact_term_matches(registry, query, include_title_substring=False)
    if registry.has_entry(query):
        existing = matches.get(query)
        if existing is None or existing.score < 1000.0:
            matches[query] = SearchResult(id=query, score=1000.0, backends=["exact"])
    return matches
