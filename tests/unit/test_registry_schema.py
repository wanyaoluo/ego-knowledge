from __future__ import annotations

import pytest

from ego_knowledge.errors import ValidationError
from ego_knowledge.registry import REGISTRY_SCHEMA_VERSION

from .support import concept_payload, note_payload, source_payload


def test_registry_lookup_helpers_cover_alias_terms_and_neighbors(fresh_ek) -> None:
    source = fresh_ek.ingest(
        "source",
        source_payload(
            title="注册表来源",
            aliases=["source-alias"],
            tags=["治理"],
        ),
    )
    note = fresh_ek.ingest(
        "note",
        note_payload(source.id, title="注册表笔记", aliases=["note-alias"]),
    )
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="单一真源",
            aliases=["SSOT", "唯一真源"],
            tags=["治理", "knowledge"],
        ),
    )
    fresh_ek.link(note.id, concept.id, rel_type="related")

    registry = fresh_ek._registry

    assert registry.has_entry(concept.id) is True
    assert [entry.id for entry in registry.find_by_aliases(["SSOT", ""])] == [concept.id]
    assert registry.all_titles_for_kind("concept") == [(concept.id, "单一真源")]
    assert (concept.id, "source_refs") not in {
        (target, "source_refs") for target in registry.out_refs(note.id, [])
    }
    assert registry.out_refs(concept.id, ["evidence_refs"]) == [source.id]
    assert registry.neighbors(note.id, rel_type="related", direction="out") == [concept.id]
    assert registry.neighbors(concept.id, direction="in") == [note.id]
    assert registry.entry_aliases_map()[concept.id] == {"SSOT", "唯一真源"}
    assert ("单一真源", concept.id, "title") in registry.all_terms_flat()

    with pytest.raises(ValidationError, match="不支持的关系方向"):
        registry.neighbors(concept.id, direction="sideways")


def test_note_promotion_targets_remain_frontmatter_metadata_across_rebuild(fresh_ek) -> None:
    source = fresh_ek.ingest(
        "source",
        source_payload(title="升格来源"),
    )
    note = fresh_ek.ingest(
        "note",
        note_payload(
            source.id,
            title="可升格笔记",
            promotion_targets=["concept"],
        ),
    )

    registry = fresh_ek._registry
    rows = registry.conn.execute(
        """
        SELECT target_id, type
          FROM relations
         WHERE source_id = ?
         ORDER BY type, target_id
        """,
        (note.id,),
    ).fetchall()
    assert [(row["target_id"], row["type"]) for row in rows] == [(source.id, "source_refs")]

    stats = fresh_ek.build_registry()
    assert stats.entries_failed == 0

    rebuilt_note = fresh_ek.get(note.id)
    assert rebuilt_note.promotion_targets == ["concept"]
    assert fresh_ek._registry.out_refs(note.id, ["promotion_targets"]) == []


def test_schema_version_is_2_3(fresh_ek) -> None:
    assert REGISTRY_SCHEMA_VERSION == "2.3"
    registry = fresh_ek._registry
    row = registry.conn.execute(
        "SELECT value FROM registry_meta WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert row["value"] == "2.3"


def test_maintenance_queue_table_exists(fresh_ek) -> None:
    registry = fresh_ek._registry
    tables = registry.list_tables()
    assert "maintenance_queue" in tables


def test_maintenance_queue_columns_match_spec(fresh_ek) -> None:
    """Verify maintenance_queue DDL matches spec §3.2 exactly."""
    registry = fresh_ek._registry
    columns = [
        row[1] for row in registry.conn.execute("PRAGMA table_info(maintenance_queue)").fetchall()
    ]
    expected_columns = [
        "id",
        "rule_id",
        "severity",
        "entry_id",
        "channel",
        "status",
        "message",
        "details_json",
        "origin",
        "proposed_op",
        "proposed_payload_json",
        "agent_id",
        "created_at",
        "updated_at",
    ]
    assert columns == expected_columns


def test_maintenance_queue_indexes_exist(fresh_ek) -> None:
    registry = fresh_ek._registry
    indexes = {
        row["name"]
        for row in registry.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_mq_%'"
        ).fetchall()
    }
    assert "idx_mq_status_channel" in indexes
    assert "idx_mq_entry" in indexes
    assert "idx_mq_created" in indexes


def test_registry_meta_has_maintenance_queue_version(fresh_ek) -> None:
    registry = fresh_ek._registry
    row = registry.conn.execute(
        "SELECT value FROM registry_meta WHERE key = 'maintenance_queue_version'"
    ).fetchone()
    assert row is not None
    assert row["value"] == "1"


def test_maintenance_queue_foreign_key_on_entry_id(fresh_ek) -> None:
    """Verify FK constraint: entry_id references entries(id) ON DELETE SET NULL."""
    registry = fresh_ek._registry
    fk_rows = registry.conn.execute("PRAGMA foreign_key_list(maintenance_queue)").fetchall()
    # Find the FK for entry_id
    entry_fk = [row for row in fk_rows if row["from"] == "entry_id"]
    assert len(entry_fk) == 1
    assert entry_fk[0]["table"] == "entries"
    assert entry_fk[0]["on_delete"] == "SET NULL"


# ---------------------------------------------------------------------------
# PR-A4 remediation: 4 tests for previously uncovered ReadOps / RelationsOps
# ---------------------------------------------------------------------------


def test_neighbors_exclude_archived(fresh_ek) -> None:
    """neighbors(include_archived=False) must filter out archived neighbors."""
    source = fresh_ek.ingest("source", source_payload(title="归档邻居测试来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="待归档概念"),
    )
    note = fresh_ek.ingest(
        "note",
        note_payload(source.id, title="归档邻居测试笔记"),
    )
    fresh_ek.link(note.id, concept.id, rel_type="related")

    registry = fresh_ek._registry

    # Before archiving, the concept appears as a neighbor (via "related").
    assert concept.id in registry.neighbors(note.id, direction="out", include_archived=True)
    assert concept.id in registry.neighbors(note.id, direction="out", include_archived=False)

    # Mark concept as archived.
    registry.conn.execute(
        "UPDATE entries SET status = 'archived' WHERE id = ?",
        (concept.id,),
    )

    # include_archived=False should now exclude the archived concept.
    assert concept.id not in registry.neighbors(note.id, direction="out", include_archived=False)
    # include_archived=True still includes it.
    assert concept.id in registry.neighbors(note.id, direction="out", include_archived=True)


def test_all_entries_except_kind(fresh_ek) -> None:
    """all_entries_except_kind must return entries whose kind != the given kind."""
    source = fresh_ek.ingest("source", source_payload(title="排除 kind 来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="排除 kind 概念"),
    )
    note = fresh_ek.ingest(
        "note",
        note_payload(source.id, title="排除 kind 笔记"),
    )

    registry = fresh_ek._registry

    result_ids = [e.id for e in registry.all_entries_except_kind("concept")]
    assert concept.id not in result_ids
    assert source.id in result_ids
    assert note.id in result_ids

    result_ids_2 = [e.id for e in registry.all_entries_except_kind("source")]
    assert source.id not in result_ids_2
    assert concept.id in result_ids_2
    assert note.id in result_ids_2


def test_all_sources_returns_only_sources(fresh_ek) -> None:
    """all_sources must return only entries of kind 'source'."""
    source = fresh_ek.ingest("source", source_payload(title="all_sources 来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="all_sources 概念"),
    )

    registry = fresh_ek._registry
    source_ids = [s.id for s in registry.all_sources()]

    assert len(source_ids) == 1
    assert source_ids[0] == source.id


def test_all_aliases_and_all_tags(fresh_ek) -> None:
    """all_aliases returns all alias NFC values; all_tags returns distinct tags."""
    fresh_ek.ingest(
        "source",
        source_payload(
            title="别名标签来源",
            aliases=["alias-alpha", "alias-β"],
            tags=["tag-A", "tag-B"],
        ),
    )
    fresh_ek.ingest(
        "source",
        source_payload(
            title="别名标签来源2",
            aliases=["alias-gamma"],
            tags=["tag-A", "tag-C"],
        ),
    )

    registry = fresh_ek._registry

    aliases = registry.all_aliases()
    assert "alias-alpha" in aliases
    assert "alias-gamma" in aliases

    tags = registry.all_tags()
    assert "tag-A" in tags
    assert "tag-B" in tags
    assert "tag-C" in tags
    # tag-A appears in both entries but all_tags uses DISTINCT.
    assert tags.count("tag-A") == 1
