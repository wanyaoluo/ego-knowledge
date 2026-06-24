from __future__ import annotations

from datetime import date

import pytest

from ego_knowledge.models import (
    ConceptEntry,
    Freshness,
    Kind,
    Relation,
    RelationSource,
    RelationType,
    SourceEntry,
    Status,
    entry_to_frontmatter,
    generate_id,
    parse_id,
)


def _base_kwargs() -> dict[str, object]:
    return {
        "id": "ek_con_01HXYZ1234ABCDEFGHJKMNPQRS",
        "kind": Kind.CONCEPT,
        "title": "单一真源",
        "slug": "单一真源",
        "status": Status.ACTIVE,
        "freshness": Freshness.STABLE,
        "schema_version": "1.0",
        "created_at": date(2026, 4, 16),
        "updated_at": date(2026, 4, 16),
    }


def test_entry_to_frontmatter_filters_runtime_meta() -> None:
    entry = ConceptEntry(
        **_base_kwargs(),
        evidence_status="solid",
        evidence_refs=["ek_note_01HXYZ1234ABCDEFGHJKMNPQRS"],
        relations=[
            Relation(
                target="ek_note_01HXYZ1234ABCDEFGHJKMNPQRT",
                type=RelationType.EVIDENCE_FOR,
                source=RelationSource.AI_CONFIRMED,
            )
        ],
        file_path="/tmp/demo.md",
        body="正文",
        metrics={"tokens": 12},
    )

    frontmatter = entry_to_frontmatter(entry)

    assert "file_path" not in frontmatter
    assert "body" not in frontmatter
    assert "metrics" not in frontmatter
    assert frontmatter["title"] == "单一真源"
    assert frontmatter["evidence_status"] == "solid"
    assert frontmatter["relations"] == [
        {
            "target": "ek_note_01HXYZ1234ABCDEFGHJKMNPQRT",
            "type": RelationType.EVIDENCE_FOR,
            "source": RelationSource.AI_CONFIRMED,
        }
    ]


def test_kind_specific_dataclass_fields() -> None:
    entry = SourceEntry(
        id="ek_src_01HXYZ1234ABCDEFGHJKMNPQRS",
        kind=Kind.SOURCE,
        title="原始材料",
        slug="原始材料",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:abc",
    )

    assert entry.source_type == "web"
    assert entry.kind is Kind.SOURCE


def test_id_roundtrip() -> None:
    for kind in Kind:
        generated = generate_id(kind)
        parsed_kind, ulid_part = parse_id(generated)
        assert parsed_kind is kind
        assert len(ulid_part) == 26


@pytest.mark.parametrize(
    ("bad_id", "message"),
    [
        ("wrong", "Invalid ID prefix"),
        ("ek_unknown_01HXYZ1234ABCDEFGHJKMNPQRS", "Unknown kind short"),
        ("ek_con_not-a-ulid", "Invalid ULID payload"),
    ],
)
def test_parse_id_rejects_invalid_values(bad_id: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_id(bad_id)


def test_relation_type_covers_all_11_values() -> None:
    """Schema 2.0: RelationType must cover 8 original + 3 materialized field names."""
    expected = {
        "derived_from",
        "related",
        "supersedes",
        "applied_in",
        "evidence_for",
        "part_of",
        "contradicts",
        "depends_on",
        # v2 additions: materialized field names now in enum
        "source_refs",
        "evidence_refs",
        "superseded_by",
    }
    assert set(RelationType) == expected
