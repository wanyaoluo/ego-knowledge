"""Entry <-> frontmatter serialization and deserialization.

Depends on _frontmatter_coercion for type coercion; does NOT import registry.py.
"""

from __future__ import annotations

import json
from datetime import date

from ._frontmatter_coercion import (
    JsonMap,
    _normalize_freshness,
    _normalize_kind,
    _normalize_status,
    _optional_confidence,
    _optional_date,
    _optional_decision_status,
    _optional_evidence_status,
    _optional_str,
    _optional_string_list,
    _parse_relations,
    _require_date,
    _require_str,
)
from .errors import StorageError
from .models import (
    ConceptEntry,
    DecisionEntry,
    DossierEntry,
    Entry,
    Kind,
    NoteEntry,
    RelationSource,
    RelationType,
    SourceEntry,
    ViewEntry,
    entry_to_frontmatter,
)

type RelationRow = tuple[str, str, str, str]


def _entry_from_frontmatter(
    frontmatter_map: JsonMap,
    *,
    file_path: str | None = None,
    body: str | None = None,
    metrics: JsonMap | None = None,
) -> Entry:
    kind = _normalize_kind(_require_str(frontmatter_map, "kind"))
    entry_id = _require_str(frontmatter_map, "id")
    title = _require_str(frontmatter_map, "title")
    slug = _require_str(frontmatter_map, "slug")
    status = _normalize_status(_require_str(frontmatter_map, "status"))
    freshness = _normalize_freshness(_require_str(frontmatter_map, "freshness"))
    schema_version = _require_str(frontmatter_map, "schema_version")
    created_at = _require_date(frontmatter_map, "created_at")
    updated_at = _require_date(frontmatter_map, "updated_at")
    aliases = _optional_string_list(frontmatter_map, "aliases")
    confidence = _optional_confidence(frontmatter_map.get("confidence"))
    tags = _optional_string_list(frontmatter_map, "tags")
    search_terms = _optional_string_list(frontmatter_map, "search_terms")
    domain = _optional_str(frontmatter_map.get("domain"))
    relations = _parse_relations(frontmatter_map.get("relations"))

    if kind is Kind.SOURCE:
        return SourceEntry(
            id=entry_id,
            kind=kind,
            title=title,
            slug=slug,
            status=status,
            freshness=freshness,
            schema_version=schema_version,
            created_at=created_at,
            updated_at=updated_at,
            aliases=aliases,
            confidence=confidence,
            tags=tags,
            search_terms=search_terms,
            domain=domain,
            relations=relations,
            file_path=file_path,
            body=body,
            metrics=metrics,
            source_type=_require_str(frontmatter_map, "source_type"),
            source_url=_require_str(frontmatter_map, "source_url"),
            captured_at=_optional_date(frontmatter_map.get("captured_at")),
            content_hash=_require_str(frontmatter_map, "content_hash"),
            watch_target=_optional_str(frontmatter_map.get("watch_target")),
            superseded_by=_optional_string_list(frontmatter_map, "superseded_by"),
        )
    if kind is Kind.NOTE:
        return NoteEntry(
            id=entry_id,
            kind=kind,
            title=title,
            slug=slug,
            status=status,
            freshness=freshness,
            schema_version=schema_version,
            created_at=created_at,
            updated_at=updated_at,
            aliases=aliases,
            confidence=confidence,
            tags=tags,
            search_terms=search_terms,
            domain=domain,
            relations=relations,
            file_path=file_path,
            body=body,
            metrics=metrics,
            source_refs=_optional_string_list(frontmatter_map, "source_refs"),
            extracted_at=_optional_date(frontmatter_map.get("extracted_at")),
            promotion_targets=_optional_string_list(
                frontmatter_map,
                "promotion_targets",
            ),
        )
    if kind is Kind.DOSSIER:
        return DossierEntry(
            id=entry_id,
            kind=kind,
            title=title,
            slug=slug,
            status=status,
            freshness=freshness,
            schema_version=schema_version,
            created_at=created_at,
            updated_at=updated_at,
            aliases=aliases,
            confidence=confidence,
            tags=tags,
            search_terms=search_terms,
            domain=domain,
            relations=relations,
            file_path=file_path,
            body=body,
            metrics=metrics,
            reviewed_at=_optional_date(frontmatter_map.get("reviewed_at")),
            review_due_at=_optional_date(frontmatter_map.get("review_due_at")),
            evidence_refs=_optional_string_list(frontmatter_map, "evidence_refs"),
        )
    if kind is Kind.CONCEPT:
        return ConceptEntry(
            id=entry_id,
            kind=kind,
            title=title,
            slug=slug,
            status=status,
            freshness=freshness,
            schema_version=schema_version,
            created_at=created_at,
            updated_at=updated_at,
            aliases=aliases,
            confidence=confidence,
            tags=tags,
            search_terms=search_terms,
            domain=domain,
            relations=relations,
            file_path=file_path,
            body=body,
            metrics=metrics,
            evidence_refs=_optional_string_list(frontmatter_map, "evidence_refs"),
            evidence_status=_optional_evidence_status(frontmatter_map.get("evidence_status")),
        )
    if kind is Kind.DECISION:
        return DecisionEntry(
            id=entry_id,
            kind=kind,
            title=title,
            slug=slug,
            status=status,
            freshness=freshness,
            schema_version=schema_version,
            created_at=created_at,
            updated_at=updated_at,
            aliases=aliases,
            confidence=confidence,
            tags=tags,
            search_terms=search_terms,
            domain=domain,
            relations=relations,
            file_path=file_path,
            body=body,
            metrics=metrics,
            decided_at=_optional_date(frontmatter_map.get("decided_at")),
            decision_status=_optional_decision_status(frontmatter_map.get("decision_status")),
            superseded_by=_optional_str(frontmatter_map.get("superseded_by")),
            evidence_refs=_optional_string_list(frontmatter_map, "evidence_refs"),
        )
    return ViewEntry(
        id=entry_id,
        kind=kind,
        title=title,
        slug=slug,
        status=status,
        freshness=freshness,
        schema_version=schema_version,
        created_at=created_at,
        updated_at=updated_at,
        aliases=aliases,
        confidence=confidence,
        tags=tags,
        search_terms=search_terms,
        domain=domain,
        relations=relations,
        file_path=file_path,
        body=body,
        metrics=metrics,
        generator=_require_str(frontmatter_map, "generator"),
        generated_at=_optional_date(frontmatter_map.get("generated_at")),
        source_query=_require_str(frontmatter_map, "source_query"),
    )


def _materialized_relation_rows(entry: Entry) -> list[RelationRow]:
    rows: list[RelationRow] = []
    seen: set[tuple[str, str]] = set()

    def add_rows(targets: list[str], rel_type: str) -> None:
        for target in targets:
            key = (target, rel_type)
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                (
                    entry.id,
                    target,
                    rel_type,
                    RelationSource.CONFIRMED.value,
                )
            )

    if isinstance(entry, NoteEntry):
        add_rows(entry.source_refs, RelationType.SOURCE_REFS.value)
        # promotion_targets stores allowed target kinds like "concept"/"dossier",
        # not entry ids, so it must stay in frontmatter instead of relations.
    if isinstance(entry, (DossierEntry, ConceptEntry, DecisionEntry)):
        add_rows(entry.evidence_refs, RelationType.EVIDENCE_REFS.value)
    if isinstance(entry, DecisionEntry) and entry.superseded_by is not None:
        add_rows([entry.superseded_by], RelationType.SUPERSEDED_BY.value)
    return rows


def _serialize_frontmatter(entry: Entry) -> str:
    frontmatter_map = entry_to_frontmatter(entry)
    try:
        return json.dumps(
            frontmatter_map,
            ensure_ascii=False,
            sort_keys=True,
            default=_json_default,
        )
    except TypeError as exc:
        raise StorageError(f"frontmatter_json 序列化失败 {entry.id}: {exc}") from exc


def _json_default(value: object) -> str:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
