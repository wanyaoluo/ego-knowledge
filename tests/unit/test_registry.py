from __future__ import annotations

from datetime import date
from enum import Enum
from pathlib import Path

import pytest

from ego_knowledge.frontmatter import write_file
from ego_knowledge.models import (
    ConceptEntry,
    Freshness,
    Kind,
    SourceEntry,
    Status,
    entry_to_frontmatter,
)
from ego_knowledge.registry import REGISTRY_SCHEMA_VERSION, Registry, build_registry

EXPECTED_TABLES = {
    "entries",
    "aliases",
    "entry_tags",
    "entry_search_terms",
    "entries_fts_cn",
    "entries_fts_en",
    "entries_fts_tri",
    "relations",
    "source_fields",
    "note_fields",
    "dossier_fields",
    "concept_fields",
    "decision_fields",
    "view_fields",
    "entry_metrics",
    "access_log",
    "registry_meta",
    "semantic_index_meta",
    "maintenance_queue",
    "external_watch",
}


def _source_entry(entry_id: str) -> SourceEntry:
    return SourceEntry(
        id=entry_id,
        kind=Kind.SOURCE,
        title="源材料",
        slug="源材料",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com/source",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:source",
    )


def _concept_entry(entry_id: str, evidence_refs: list[str] | None = None) -> ConceptEntry:
    return ConceptEntry(
        id=entry_id,
        kind=Kind.CONCEPT,
        title="单一真源",
        slug="单一真源",
        status=Status.ACTIVE,
        freshness=Freshness.STABLE,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        evidence_status="solid",
        evidence_refs=evidence_refs or [],
    )


def _write_entry(path: Path, entry: SourceEntry | ConceptEntry, body: str) -> None:
    def _yamlify(value: object) -> object:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list):
            return [_yamlify(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _yamlify(item) for key, item in value.items()}
        return value

    path.parent.mkdir(parents=True, exist_ok=True)
    write_file(
        str(path),
        {key: _yamlify(value) for key, value in entry_to_frontmatter(entry).items()},
        body,
    )


def test_registry_init_schema_creates_phase2_tables(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "catalog.sqlite")
    registry.init_schema()

    assert EXPECTED_TABLES.issubset(set(registry.list_tables()))
    row = registry.conn.execute(
        "SELECT value FROM registry_meta WHERE key = 'schema_version'"
    ).fetchone()
    assert row is not None
    assert row["value"] == REGISTRY_SCHEMA_VERSION

    registry.close()


def test_build_registry_creates_database_from_empty_root(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"

    stats = build_registry(data_root)

    assert stats.entries_ok == 0
    assert stats.entries_failed == 0
    assert stats.errors == []

    registry = Registry(data_root / "registry" / "catalog.sqlite")
    assert EXPECTED_TABLES.issubset(set(registry.list_tables()))
    registry.close()


def test_registry_init_schema_rejects_retired_lower_schema(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "catalog.sqlite")
    registry.init_schema()
    registry.conn.execute(
        "UPDATE registry_meta SET value = '2.2' WHERE key = 'schema_version'"
    )
    registry.commit()
    registry.close()

    reopened = Registry(tmp_path / "catalog.sqlite")
    try:
        with pytest.raises(RuntimeError) as exc_info:
            reopened.init_schema()
        assert str(exc_info.value) == (
            f"catalog schema_version=2.2 低于目标 {REGISTRY_SCHEMA_VERSION}，"
            "历史一次性迁移脚本已退役；"
            "请从备份恢复到匹配版本或重建数据根后再升级。"
        )
    finally:
        reopened.close()


def test_registry_init_schema_rejects_future_schema(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "catalog.sqlite")
    registry.init_schema()
    registry.conn.execute(
        "UPDATE registry_meta SET value = '9.9' WHERE key = 'schema_version'"
    )
    registry.commit()
    registry.close()

    reopened = Registry(tmp_path / "catalog.sqlite")
    try:
        with pytest.raises(RuntimeError) as exc_info:
            reopened.init_schema()
        assert str(exc_info.value) == (
            f"catalog schema_version=9.9 高于代码目标 {REGISTRY_SCHEMA_VERSION}，"
            "请升级代码或检查 catalog。"
        )
    finally:
        reopened.close()


def test_build_registry_rebuilds_existing_files(tmp_path: Path) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    source = _source_entry("ek_src_01HXYZ1234ABCDEFGHJKMNPQRS")
    concept = _concept_entry(
        "ek_con_01HXYZ1234ABCDEFGHJKMNPQRT",
        evidence_refs=[source.id],
    )

    _write_entry(data_root / "entries" / "concept.md", concept, "概念正文")
    _write_entry(data_root / "sources" / "source.md", source, "来源正文")

    stats = build_registry(data_root)

    assert stats.entries_ok == 2
    assert stats.entries_failed == 0
    registry = Registry(data_root / "registry" / "catalog.sqlite")
    assert registry.all_entry_ids() == [concept.id, source.id]
    rebuilt_source = registry.get_entry(source.id)
    rebuilt_concept = registry.get_entry(concept.id)
    assert rebuilt_source.file_path == str(data_root / "sources" / "source.md")
    assert rebuilt_source.body == "来源正文"
    assert rebuilt_concept.evidence_refs == [source.id]

    row = registry.conn.execute("SELECT COUNT(*) AS total FROM entry_metrics").fetchone()
    assert row is not None
    assert row["total"] == 2
    registry.close()


def test_upsert_entry_materializes_kind_specific_relations(tmp_path: Path) -> None:
    registry = Registry(tmp_path / "catalog.sqlite")
    registry.init_schema()
    source = _source_entry("ek_src_01HXYZ1234ABCDEFGHJKMNPQRS")
    concept = _concept_entry(
        "ek_con_01HXYZ1234ABCDEFGHJKMNPQRT",
        evidence_refs=[source.id],
    )

    registry.upsert_entry(source, tmp_path / "source.md", "来源正文")
    registry.upsert_entry(concept, tmp_path / "concept.md", "概念正文")
    registry.commit()

    rows = registry.conn.execute(
        """
        SELECT target_id, type, origin
          FROM relations
         WHERE source_id = ?
         ORDER BY target_id, type
        """,
        (concept.id,),
    ).fetchall()

    assert [(row["target_id"], row["type"], row["origin"]) for row in rows] == [
        (source.id, "evidence_refs", "confirmed")
    ]
    registry.close()
