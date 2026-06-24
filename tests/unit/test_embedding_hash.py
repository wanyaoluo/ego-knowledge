"""Unit tests for _embedding_hash.compute_embedding_content_hash."""

from __future__ import annotations

from datetime import date

from ego_knowledge._embedding_hash import compute_embedding_content_hash
from ego_knowledge.models import (
    ConceptEntry,
    Freshness,
    Kind,
    NoteEntry,
    SourceEntry,
    Status,
)


def _make_source(**overrides: object) -> SourceEntry:
    defaults: dict[str, object] = {
        "id": "ek_src_TEST01",
        "kind": Kind.SOURCE,
        "title": "测试 source",
        "slug": "test-source",
        "status": Status.ACTIVE,
        "freshness": Freshness.STABLE,
        "schema_version": "2.2",
        "created_at": date(2026, 1, 1),
        "updated_at": date(2026, 1, 1),
        "source_type": "web",
        "source_url": "https://example.com",
        "content_hash": "abc123",
        "tags": ["python", "test"],
        "aliases": ["别称"],
        "search_terms": ["搜索词"],
        "body": "这是一段测试正文。",
    }
    defaults.update(overrides)
    return SourceEntry(**defaults)  # type: ignore[arg-type]


def _make_note(**overrides: object) -> NoteEntry:
    defaults: dict[str, object] = {
        "id": "ek_note_TEST01",
        "kind": Kind.NOTE,
        "title": "测试笔记",
        "slug": "test-note",
        "status": Status.ACTIVE,
        "freshness": Freshness.STABLE,
        "schema_version": "2.2",
        "created_at": date(2026, 1, 1),
        "updated_at": date(2026, 1, 1),
        "tags": [],
        "aliases": [],
        "search_terms": [],
        "body": "笔记内容。",
    }
    defaults.update(overrides)
    return NoteEntry(**defaults)  # type: ignore[arg-type]


def _make_concept(**overrides: object) -> ConceptEntry:
    defaults: dict[str, object] = {
        "id": "ek_con_TEST01",
        "kind": Kind.CONCEPT,
        "title": "测试概念",
        "slug": "test-concept",
        "status": Status.ACTIVE,
        "freshness": Freshness.STABLE,
        "schema_version": "2.2",
        "created_at": date(2026, 1, 1),
        "updated_at": date(2026, 1, 1),
        "tags": [],
        "aliases": [],
        "search_terms": [],
        "body": "概念定义。",
    }
    defaults.update(overrides)
    return ConceptEntry(**defaults)  # type: ignore[arg-type]


class TestEmbeddingContentHash:
    def test_hash_stable_for_same_entry(self) -> None:
        """Same entry content produces the same hash."""
        entry = _make_source()
        h1 = compute_embedding_content_hash(entry)
        h2 = compute_embedding_content_hash(entry)
        assert h1 == h2
        assert len(h1) == 16

    def test_hash_differs_on_body_change(self) -> None:
        """Body change produces a different hash."""
        e1 = _make_source(body="正文 A")
        e2 = _make_source(body="正文 B")
        assert compute_embedding_content_hash(e1) != compute_embedding_content_hash(e2)

    def test_hash_differs_on_title_change(self) -> None:
        """Title change produces a different hash."""
        e1 = _make_source(title="标题 A")
        e2 = _make_source(title="标题 B")
        assert compute_embedding_content_hash(e1) != compute_embedding_content_hash(e2)

    def test_hash_differs_on_tags_change(self) -> None:
        """Tags change produces a different hash."""
        e1 = _make_source(tags=["a", "b"])
        e2 = _make_source(tags=["a", "c"])
        assert compute_embedding_content_hash(e1) != compute_embedding_content_hash(e2)

    def test_source_kind_includes_url_and_content_hash(self) -> None:
        """Source entries: source_url and content_hash affect the hash."""
        e1 = _make_source(source_url="https://a.com", content_hash="h1")
        e2 = _make_source(source_url="https://b.com", content_hash="h1")
        e3 = _make_source(source_url="https://a.com", content_hash="h2")
        h1 = compute_embedding_content_hash(e1)
        assert compute_embedding_content_hash(e2) != h1
        assert compute_embedding_content_hash(e3) != h1

    def test_note_kind_ignores_source_fields(self) -> None:
        """Non-source entries don't include source_url / content_hash."""
        e1 = _make_note(title="同标题")
        h1 = compute_embedding_content_hash(e1)
        assert isinstance(h1, str) and len(h1) == 16

    def test_empty_body(self) -> None:
        """Entry with empty body still produces a valid hash."""
        entry = _make_concept(body="")
        h = compute_embedding_content_hash(entry)
        assert isinstance(h, str) and len(h) == 16

    def test_none_body(self) -> None:
        """Entry with None body produces a valid hash."""
        entry = _make_concept(body=None)
        h = compute_embedding_content_hash(entry)
        assert isinstance(h, str) and len(h) == 16

    def test_body_truncation_at_2000_chars(self) -> None:
        """Body beyond 2000 chars is truncated; content after 2000 does not change hash."""
        short_body = "A" * 2000
        long_body = "A" * 2000 + "B" * 500
        e1 = _make_note(body=short_body)
        e2 = _make_note(body=long_body)
        assert compute_embedding_content_hash(e1) == compute_embedding_content_hash(e2)

    def test_hash_is_hex_string(self) -> None:
        """Hash is a 16-char hex string."""
        entry = _make_source()
        h = compute_embedding_content_hash(entry)
        assert len(h) == 16
        int(h, 16)  # Should not raise
