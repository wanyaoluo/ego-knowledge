"""Integration tests for ek watch-github (L1 GitHub releases polling)."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ego_knowledge._external_watch import (
    _build_search_terms,
    _hash_url_tag,
    _is_new_release,
    add_watch,
    list_watches,
    poll_all,
)
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.errors import ConflictError, ValidationError


@pytest.fixture()
def ek_with_registry(fresh_ek: EgoKnowledge) -> EgoKnowledge:
    """Provide an EgoKnowledge instance with initialized registry."""
    return fresh_ek


# ---------------------------------------------------------------------------
# add_watch / list_watches
# ---------------------------------------------------------------------------


class TestAddWatch:
    def test_add_valid_target(self, ek_with_registry: EgoKnowledge) -> None:
        watch_id = add_watch(ek_with_registry._registry, "owner/repo")
        assert watch_id.startswith("ew_")

    def test_add_invalid_format(self, ek_with_registry: EgoKnowledge) -> None:
        with pytest.raises(ValidationError, match="格式应为"):
            add_watch(ek_with_registry._registry, "invalid-no-slash")

    def test_add_duplicate(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "owner/repo")
        with pytest.raises(ConflictError, match="已存在"):
            add_watch(ek_with_registry._registry, "owner/repo")


class TestListWatches:
    def test_list_empty(self, ek_with_registry: EgoKnowledge) -> None:
        result = list_watches(ek_with_registry._registry)
        assert result == []

    def test_list_with_entries(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "octocat/hello-world")
        add_watch(ek_with_registry._registry, "python/cpython")
        result = list_watches(ek_with_registry._registry)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _is_new_release
# ---------------------------------------------------------------------------


class TestIsNewRelease:
    def test_no_cursor_is_new(self) -> None:
        record: dict[str, object] = {"cursor": None}
        latest: dict[str, object] = {"tag_name": "v1.0.0"}
        assert _is_new_release(record, latest) is True

    def test_same_cursor_not_new(self) -> None:
        record: dict[str, object] = {"cursor": "v1.0.0"}
        latest: dict[str, object] = {"tag_name": "v1.0.0"}
        assert _is_new_release(record, latest) is False

    def test_different_cursor_is_new(self) -> None:
        record: dict[str, object] = {"cursor": "v1.0.0"}
        latest: dict[str, object] = {"tag_name": "v2.0.0"}
        assert _is_new_release(record, latest) is True


# ---------------------------------------------------------------------------
# _hash_url_tag / _build_search_terms
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_hash_url_tag_stable(self) -> None:
        h1 = _hash_url_tag("https://example.com", "v1.0")
        h2 = _hash_url_tag("https://example.com", "v1.0")
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_url_tag_different(self) -> None:
        h1 = _hash_url_tag("https://example.com", "v1.0")
        h2 = _hash_url_tag("https://example.com", "v2.0")
        assert h1 != h2

    def test_build_search_terms_at_least_5(self) -> None:
        latest: dict[str, object] = {
            "tag_name": "v1.0.0",
            "name": "Release 1.0.0",
            "body": "First release",
        }
        terms = _build_search_terms("owner/repo", "v1.0.0", latest)
        assert len(terms) >= 5
        assert "owner/repo" in terms
        assert "repo" in terms
        assert "v1.0.0" in terms
        assert "github_release" in terms
        # Chinese bucket coverage
        assert any("释出" in t for t in terms)
        # No empty strings (previous hack removed)
        assert "" not in terms

    def test_build_search_terms_no_body(self) -> None:
        latest: dict[str, object] = {
            "tag_name": "v2.0",
            "name": None,
            "body": None,
        }
        terms = _build_search_terms("org/project", "v2.0", latest)
        assert len(terms) >= 5
        assert "" not in terms


# ---------------------------------------------------------------------------
# poll_all with mocked GitHub API
# ---------------------------------------------------------------------------


class TestPollAll:
    def test_poll_empty(self, ek_with_registry: EgoKnowledge) -> None:
        result = poll_all(ek_with_registry._registry, data_root=ek_with_registry._data_root)
        assert result.processed == 0
        assert result.new == 0

    def test_poll_no_new_release(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "octocat/hello-world")
        # Set cursor to current release to simulate no new release
        ek_with_registry._registry.conn.execute(
            "UPDATE external_watch SET cursor = 'v1.0.0' WHERE target = 'octocat/hello-world'"
        )
        ek_with_registry._registry.commit()

        mock_release = {
            "tag_name": "v1.0.0",
            "html_url": "https://github.com/octocat/hello-world/releases/tag/v1.0.0",
        }

        with patch(
            "ego_knowledge._external_watch._fetch_latest_release", return_value=mock_release
        ):
            result = poll_all(ek_with_registry._registry, data_root=ek_with_registry._data_root)

        assert result.processed == 1
        assert result.new == 0

    def test_poll_with_new_release_ingests(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "test/repo")

        mock_release = {
            "tag_name": "v2.0.0",
            "name": "Version 2.0.0",
            "html_url": "https://github.com/test/repo/releases/tag/v2.0.0",
            "published_at": "2026-05-10T12:00:00Z",
            "body": "New features",
        }

        with patch(
            "ego_knowledge._external_watch._fetch_latest_release", return_value=mock_release
        ):
            result = poll_all(ek_with_registry._registry, data_root=ek_with_registry._data_root)

        assert result.processed == 1
        assert result.new == 1

        # Verify source was ingested
        sources = ek_with_registry._registry.all_entries_by_kind("source")
        assert len(sources) >= 1

        # Verify cursor updated
        watches = list_watches(ek_with_registry._registry)
        assert watches[0]["cursor"] == "v2.0.0"

    def test_poll_404_skips(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "nonexistent/repo")

        with patch("ego_knowledge._external_watch._fetch_latest_release", return_value=None):
            result = poll_all(ek_with_registry._registry, data_root=ek_with_registry._data_root)

        assert result.processed == 1
        assert result.new == 0
        assert result.errors == []

    def test_poll_api_error_recorded(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "error/repo")

        with patch(
            "ego_knowledge._external_watch._fetch_latest_release",
            side_effect=RuntimeError("GitHub API 限流"),
        ):
            result = poll_all(ek_with_registry._registry, data_root=ek_with_registry._data_root)

        assert result.processed == 1
        assert result.new == 0
        assert len(result.errors) == 1
        assert result.errors[0]["target"] == "error/repo"


# ---------------------------------------------------------------------------
# on_new_release chain tests (Task 5.4)
# ---------------------------------------------------------------------------


class TestOnNewRelease:
    def test_on_new_release_creates_source(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "test/new-release")
        record = {
            "id": list_watches(ek_with_registry._registry)[0]["id"],
            "target": "test/new-release",
            "cursor": None,
            "linked_dossiers_json": "[]",
        }
        latest: dict[str, object] = {
            "tag_name": "v1.0.0",
            "name": "First Release",
            "html_url": "https://github.com/test/new-release/releases/tag/v1.0.0",
            "published_at": "2026-05-10T12:00:00Z",
            "body": "Initial release",
        }

        from ego_knowledge._external_watch import _on_new_release

        _on_new_release(
            ek_with_registry._registry, record, latest, data_root=ek_with_registry._data_root
        )

        sources = ek_with_registry._registry.all_entries_by_kind("source")
        assert len(sources) == 1
        assert "test/new-release" in sources[0].title

    def test_on_new_release_updates_cursor(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "test/cursor-test")
        watch_id = list_watches(ek_with_registry._registry)[0]["id"]
        record = {
            "id": watch_id,
            "target": "test/cursor-test",
            "cursor": None,
            "linked_dossiers_json": "[]",
        }
        latest: dict[str, object] = {
            "tag_name": "v3.0.0",
            "name": "v3",
            "html_url": "https://github.com/test/cursor-test/releases/tag/v3.0.0",
            "published_at": "2026-05-10T12:00:00Z",
            "body": "",
        }

        from ego_knowledge._external_watch import _on_new_release

        _on_new_release(
            ek_with_registry._registry, record, latest, data_root=ek_with_registry._data_root
        )

        watches = list_watches(ek_with_registry._registry)
        assert watches[0]["cursor"] == "v3.0.0"

    def test_on_new_release_skip_existing(self, ek_with_registry: EgoKnowledge) -> None:
        """If the same content_hash is already ingested, ConflictError → skip."""
        add_watch(ek_with_registry._registry, "test/skip-existing")
        watch_id = list_watches(ek_with_registry._registry)[0]["id"]
        record = {
            "id": watch_id,
            "target": "test/skip-existing",
            "cursor": None,
            "linked_dossiers_json": "[]",
        }
        latest: dict[str, object] = {
            "tag_name": "v1.0.0",
            "name": "v1",
            "html_url": "https://github.com/test/skip-existing/releases/tag/v1.0.0",
            "published_at": "2026-05-10T12:00:00Z",
            "body": "",
        }

        from ego_knowledge._external_watch import _on_new_release

        # First ingestion
        _on_new_release(
            ek_with_registry._registry, record, latest, data_root=ek_with_registry._data_root
        )

        # Reset cursor to simulate "hasn't been processed"
        ek_with_registry._registry.conn.execute(
            "UPDATE external_watch SET cursor = NULL WHERE id = ?", (watch_id,)
        )
        ek_with_registry._registry.commit()

        # Second run with same release → should hit ConflictError, just update cursor
        _on_new_release(
            ek_with_registry._registry, record, latest, data_root=ek_with_registry._data_root
        )

        # Should still have only 1 source
        sources = ek_with_registry._registry.all_entries_by_kind("source")
        assert len(sources) == 1

    def test_on_new_release_supersedes_old(self, ek_with_registry: EgoKnowledge) -> None:
        add_watch(ek_with_registry._registry, "test/supersede")
        watch_id = list_watches(ek_with_registry._registry)[0]["id"]

        # First release
        record = {
            "id": watch_id,
            "target": "test/supersede",
            "cursor": None,
            "linked_dossiers_json": "[]",
        }
        v1: dict[str, object] = {
            "tag_name": "v1.0.0",
            "name": "v1",
            "html_url": "https://github.com/test/supersede/releases/tag/v1.0.0",
            "published_at": "2026-05-10T12:00:00Z",
            "body": "",
        }

        from ego_knowledge._external_watch import _on_new_release

        _on_new_release(
            ek_with_registry._registry, record, v1, data_root=ek_with_registry._data_root
        )

        # Reset cursor so v2 triggers as new
        ek_with_registry._registry.conn.execute(
            "UPDATE external_watch SET cursor = NULL WHERE id = ?", (watch_id,)
        )
        ek_with_registry._registry.commit()

        # Second release
        v2: dict[str, object] = {
            "tag_name": "v2.0.0",
            "name": "v2",
            "html_url": "https://github.com/test/supersede/releases/tag/v2.0.0",
            "published_at": "2026-05-11T12:00:00Z",
            "body": "New version",
        }
        _on_new_release(
            ek_with_registry._registry, record, v2, data_root=ek_with_registry._data_root
        )

        # Should have 2 sources, and v1 should be superseded by v2
        sources = ek_with_registry._registry.all_entries_by_kind("source")
        assert len(sources) == 2

        # Check superseded_by field on old source entry (stored in frontmatter, not relations table)
        from ego_knowledge.models import SourceEntry

        old_sources = [
            s for s in sources if isinstance(s, SourceEntry) and s.watch_target == "test/supersede"
        ]
        v1_entry = next(s for s in old_sources if "v1.0.0" in s.title)
        v2_entry = next(s for s in old_sources if "v2.0.0" in s.title)
        assert v1_entry.superseded_by is not None
        assert v2_entry.id in v1_entry.superseded_by

    def test_on_new_release_notifies_linked_dossier(self, ek_with_registry: EgoKnowledge) -> None:
        # Create a source to use as evidence_ref for the dossier
        evidence_src = ek_with_registry.ingest(
            "source",
            {
                "title": "证据来源",
                "source_url": "https://example.com/evidence",
                "source_type": "web",
                "content_hash": "ev_hash_001",
                "captured_at": "2026-05-10",
                "tags": ["evidence"],
                "search_terms": ["证据", "evidence", "来源", "source", "web"],
            },
        )
        # Create a dossier to link
        dossier = ek_with_registry.ingest(
            "dossier",
            {
                "title": "测试档案",
                "search_terms": ["测试", "test", "档案", "dossier", "别称"],
                "evidence_refs": [evidence_src.id],
                "body": "x" * 50,
            },
        )

        # Add watch with linked dossier
        add_watch(ek_with_registry._registry, "test/notify")
        watch_id = list_watches(ek_with_registry._registry)[0]["id"]

        # Update linked_dossiers_json
        ek_with_registry._registry.conn.execute(
            "UPDATE external_watch SET linked_dossiers_json = ? WHERE id = ?",
            (json.dumps([dossier.id]), watch_id),
        )
        ek_with_registry._registry.commit()

        record = {
            "id": watch_id,
            "target": "test/notify",
            "cursor": None,
            "linked_dossiers_json": json.dumps([dossier.id]),
        }
        latest: dict[str, object] = {
            "tag_name": "v1.0.0",
            "name": "v1",
            "html_url": "https://github.com/test/notify/releases/tag/v1.0.0",
            "published_at": "2026-05-10T12:00:00Z",
            "body": "",
        }

        from ego_knowledge._external_watch import _on_new_release

        _on_new_release(
            ek_with_registry._registry, record, latest, data_root=ek_with_registry._data_root
        )

        # Verify dossier updated_at was touched (E1)
        row = ek_with_registry._registry.conn.execute(
            "SELECT updated_at FROM entries WHERE id = ?", (dossier.id,)
        ).fetchone()
        assert row is not None
        # updated_at should be today
        from datetime import date as _date

        assert row["updated_at"] == _date.today().isoformat()
