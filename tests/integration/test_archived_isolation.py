"""Phase 4 — archived isolation end-to-end test.

Verify that all consumer-facing APIs (search, related, review) exclude
archived entries by default, and only return them when explicitly asked.
"""

from __future__ import annotations

from datetime import date, timedelta

from ego_knowledge.core import EgoKnowledge
from tests.unit.support import (
    dossier_payload,
    source_payload,
)


def _make_source(
    ek: EgoKnowledge, title: str, *, extra_search_terms: list[str] | None = None
) -> str:
    overrides: dict[str, object] = {}
    if extra_search_terms is not None:
        # Merge extra terms into default search_terms
        base_terms = source_payload(title=title)["search_terms"]
        assert isinstance(base_terms, list)
        overrides["search_terms"] = list(dict.fromkeys(extra_search_terms + base_terms))
    return ek.ingest("source", source_payload(title=title, **overrides)).id


def _make_dossier(
    ek: EgoKnowledge,
    evidence_ref: str,
    title: str,
    *,
    review_due_at: str | None = None,
    conflict_policy: str = "strict",
) -> str:
    overrides: dict[str, object] = {}
    if review_due_at is not None:
        overrides["review_due_at"] = review_due_at
    return ek.ingest(
        "dossier",
        dossier_payload(evidence_ref, title=title, **overrides),
        conflict_policy=conflict_policy,
    ).id


class TestArchivedIsolation:
    """1 archived + 1 active entry, verify consumers only return active."""

    def test_archived_excluded_from_search(
        self,
        fresh_ek: EgoKnowledge,
    ) -> None:
        # Bug1 fix: "search-test" is classified as SYMBOL_TOKEN by the tokenizer,
        # which only matches exact search_terms / title / alias / tag entries.
        # Add "search-test" as an independent search_term so exact match succeeds.
        archived_id = _make_source(
            fresh_ek, "archived-search-test", extra_search_terms=["search-test"]
        )
        active_id = _make_source(fresh_ek, "active-search-test", extra_search_terms=["search-test"])

        fresh_ek.update(archived_id, {"status": "archived"})

        results = fresh_ek.search("search-test")
        result_ids = [r.id for r in results]

        assert active_id in result_ids
        assert archived_id not in result_ids

    def test_archived_search_with_include_archived(
        self,
        fresh_ek: EgoKnowledge,
    ) -> None:
        # Same as Bug1 fix: add "include-test" as independent search_term.
        archived_id = _make_source(
            fresh_ek, "archived-include-test", extra_search_terms=["include-test"]
        )

        fresh_ek.update(archived_id, {"status": "archived"})

        # Default: excluded
        results = fresh_ek.search("include-test")
        assert archived_id not in {r.id for r in results}

        # Explicit include
        results = fresh_ek.search("include-test", include_archived=True)
        assert archived_id in {r.id for r in results}

    def test_archived_excluded_from_related(
        self,
        fresh_ek: EgoKnowledge,
    ) -> None:
        source_id = _make_source(fresh_ek, "related-source")
        active_id = _make_source(fresh_ek, "active-related-test")
        archived_id = _make_source(fresh_ek, "archived-related-test")

        # Bug2 fix: use "related" (RelationType.RELATED value), not "related_to".
        fresh_ek.link(source_id, active_id, "related")
        fresh_ek.link(source_id, archived_id, "related")
        fresh_ek.update(archived_id, {"status": "archived"})

        related = fresh_ek.related(source_id, depth=1)
        related_ids = [r.id for r in related]

        assert active_id in related_ids
        assert archived_id not in related_ids

    def test_archived_related_with_include_archived(
        self,
        fresh_ek: EgoKnowledge,
    ) -> None:
        source_id = _make_source(fresh_ek, "related-include-source")
        archived_id = _make_source(fresh_ek, "archived-include-related")

        # Bug2 fix: use "related" (RelationType.RELATED value).
        fresh_ek.link(source_id, archived_id, "related")
        fresh_ek.update(archived_id, {"status": "archived"})

        related = fresh_ek.related(source_id, depth=1, include_archived=True)
        assert archived_id in {r.id for r in related}

    def test_archived_excluded_from_review_queue(
        self,
        fresh_ek: EgoKnowledge,
    ) -> None:
        source_id = _make_source(fresh_ek, "review-source")
        past_due = (date.today() - timedelta(days=7)).isoformat()

        # Bug3 fix: use conflict_policy="allow" because the two dossier titles
        # ("active-review-dossier" / "archived-review-dossier") have high
        # Levenshtein similarity and would trigger conflict detection in strict mode.
        active_id = _make_dossier(
            fresh_ek,
            source_id,
            "active-review-dossier",
            review_due_at=past_due,
            conflict_policy="allow",
        )
        archived_id = _make_dossier(
            fresh_ek,
            source_id,
            "archived-review-dossier",
            review_due_at=past_due,
            conflict_policy="allow",
        )

        fresh_ek.update(archived_id, {"status": "archived"})

        queue = fresh_ek.review_queue(overdue_only=True)
        queue_ids = [e.id for e in queue]

        assert active_id in queue_ids
        assert archived_id not in queue_ids

    def test_archived_review_with_include_archived(
        self,
        fresh_ek: EgoKnowledge,
    ) -> None:
        source_id = _make_source(fresh_ek, "review-include-source")
        past_due = (date.today() - timedelta(days=7)).isoformat()

        archived_id = _make_dossier(
            fresh_ek,
            source_id,
            "archived-include-review",
            review_due_at=past_due,
        )
        fresh_ek.update(archived_id, {"status": "archived"})

        queue = fresh_ek.review_queue(overdue_only=True, include_archived=True)
        assert archived_id in {e.id for e in queue}
