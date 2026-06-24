"""Data models for EgoKnowledge entries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from typing import Literal, cast

import ulid


class Kind(StrEnum):
    """Supported entry kinds."""

    SOURCE = "source"
    NOTE = "note"
    DOSSIER = "dossier"
    CONCEPT = "concept"
    DECISION = "decision"
    VIEW = "view"


class Status(StrEnum):
    """Lifecycle status for an entry."""

    DRAFT = "draft"
    ACTIVE = "active"
    AUTHORITATIVE = "authoritative"
    LEGACY = "legacy"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class Freshness(StrEnum):
    """Change cadence / volatility marker."""

    STABLE = "stable"
    WATCH = "watch"
    VOLATILE = "volatile"


class RelationType(StrEnum):
    """Supported relation types between entries.

    Schema 2.0: 8 original + 3 materialized field names (source_refs,
    evidence_refs, superseded_by).  promotion_targets is NOT a relation type
    — it stores allowed target *kinds*, not entry ids.
    """

    DERIVED_FROM = "derived_from"
    RELATED = "related"
    SUPERSEDES = "supersedes"
    APPLIED_IN = "applied_in"
    EVIDENCE_FOR = "evidence_for"
    PART_OF = "part_of"
    CONTRADICTS = "contradicts"
    DEPENDS_ON = "depends_on"
    # v2: materialized field names now in enum
    SOURCE_REFS = "source_refs"
    EVIDENCE_REFS = "evidence_refs"
    SUPERSEDED_BY = "superseded_by"


class RelationSource(StrEnum):
    """How a relation was produced."""

    CONFIRMED = "confirmed"
    AI_SUGGESTED = "ai_suggested"
    AI_CONFIRMED = "ai_confirmed"


type Confidence = Literal["low", "medium", "high"]
type EvidenceStatus = Literal["solid", "partial", "weak"]
type DecisionStatus = Literal["active", "superseded"]
type MetricsMap = dict[str, object]


@dataclass(slots=True)
class Relation:
    """A typed link to another entry."""

    target: str
    type: RelationType
    source: RelationSource = RelationSource.CONFIRMED


@dataclass(slots=True)
class EntryBase:
    """Common persistent fields shared by every entry kind."""

    id: str
    kind: Kind
    title: str
    slug: str
    status: Status
    freshness: Freshness
    schema_version: str
    created_at: date
    updated_at: date
    aliases: list[str] = field(default_factory=list)
    confidence: Confidence | None = None
    tags: list[str] = field(default_factory=list)
    search_terms: list[str] = field(default_factory=list)
    domain: str | None = None
    relations: list[Relation] = field(default_factory=list)
    file_path: str | None = field(default=None, compare=False, repr=False)
    body: str | None = field(default=None, compare=False, repr=False)
    metrics: MetricsMap | None = field(default=None, compare=False, repr=False)


@dataclass(slots=True)
class SourceEntry(EntryBase):
    source_type: str = ""
    source_url: str = ""
    captured_at: date | None = None
    content_hash: str = ""
    watch_target: str | None = None
    superseded_by: list[str] = field(default_factory=list)


@dataclass(slots=True)
class NoteEntry(EntryBase):
    source_refs: list[str] = field(default_factory=list)
    extracted_at: date | None = None
    promotion_targets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DossierEntry(EntryBase):
    reviewed_at: date | None = None
    review_due_at: date | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConceptEntry(EntryBase):
    evidence_refs: list[str] = field(default_factory=list)
    evidence_status: EvidenceStatus = "weak"


@dataclass(slots=True)
class DecisionEntry(EntryBase):
    decided_at: date | None = None
    decision_status: DecisionStatus = "active"
    superseded_by: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ViewEntry(EntryBase):
    generator: str = ""
    generated_at: date | None = None
    source_query: str = ""


type Entry = SourceEntry | NoteEntry | DossierEntry | ConceptEntry | DecisionEntry | ViewEntry

_KIND_SHORT: dict[Kind, str] = {
    Kind.SOURCE: "src",
    Kind.NOTE: "note",
    Kind.DOSSIER: "dos",
    Kind.CONCEPT: "con",
    Kind.DECISION: "dec",
    Kind.VIEW: "view",
}

_SHORT_TO_KIND: dict[str, Kind] = {short: kind for kind, short in _KIND_SHORT.items()}

_PERSISTENT_FIELDS: frozenset[str] = frozenset(
    {
        "id",
        "kind",
        "title",
        "slug",
        "status",
        "freshness",
        "schema_version",
        "created_at",
        "updated_at",
        "aliases",
        "confidence",
        "tags",
        "search_terms",
        "domain",
        "relations",
        "source_type",
        "source_url",
        "captured_at",
        "content_hash",
        "source_refs",
        "extracted_at",
        "promotion_targets",
        "reviewed_at",
        "review_due_at",
        "evidence_refs",
        "evidence_status",
        "decided_at",
        "decision_status",
        "superseded_by",
        "generator",
        "generated_at",
        "source_query",
        "watch_target",
    }
)


KIND_TO_CLASS: dict[Kind, type[EntryBase]] = {
    Kind.SOURCE: SourceEntry,
    Kind.NOTE: NoteEntry,
    Kind.DOSSIER: DossierEntry,
    Kind.CONCEPT: ConceptEntry,
    Kind.DECISION: DecisionEntry,
    Kind.VIEW: ViewEntry,
}


def generate_id(kind: Kind) -> str:
    """Generate a ULID-based EgoKnowledge entry id."""

    return f"ek_{_KIND_SHORT[kind]}_{ulid.new().str}"


def parse_id(id_str: str) -> tuple[Kind, str]:
    """Parse a stored id into kind and ULID payload."""

    if not id_str.startswith("ek_"):
        raise ValueError(f"Invalid ID prefix: {id_str}")

    parts = id_str.split("_", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid ID structure: {id_str}")

    _, kind_short, ulid_part = parts
    kind = _SHORT_TO_KIND.get(kind_short)
    if kind is None:
        raise ValueError(f"Unknown kind short: {kind_short}")

    try:
        ulid.from_str(ulid_part)
    except ValueError as exc:
        raise ValueError(f"Invalid ULID payload: {ulid_part}") from exc

    return kind, ulid_part


def entry_to_frontmatter(entry: EntryBase) -> dict[str, object]:
    """Serialize persistent fields only for YAML frontmatter."""

    data = cast(dict[str, object], asdict(entry))
    return {key: value for key, value in data.items() if key in _PERSISTENT_FIELDS}
