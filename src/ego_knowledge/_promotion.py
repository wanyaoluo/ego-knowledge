"""Promotion workflows for EgoKnowledge kinds."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from ._domains import DomainRegistry
from ._entry_store import (
    EntryStore,
    _log_promote_body_exemption,
    compute_review_due,
    enqueue_local_findings,
    parse_kind,
)
from ._mutations import MutationService, PrimaryUpdate
from ._validation import CachedEmbedder, check_conflicts, validate_ingest_payload
from .errors import ValidationError
from .metrics import _recompute_ids
from .models import (
    ConceptEntry,
    DecisionEntry,
    DossierEntry,
    Entry,
    EvidenceStatus,
    Freshness,
    Kind,
    NoteEntry,
    Relation,
    RelationType,
    Status,
    entry_to_frontmatter,
    generate_id,
)
from .paths import allocate_unique_path, file_path_of
from .registry import REGISTRY_SCHEMA_VERSION, Registry
from .slug import generate_slug


class PromotionService:
    """Promotion state machine across entry kinds."""

    def __init__(
        self,
        data_root: Path,
        registry: Registry,
        entries: EntryStore,
        domains: DomainRegistry,
        mutations: MutationService,
        embedder: CachedEmbedder | None = None,
    ) -> None:
        self._data_root = data_root
        self._registry = registry
        self._entries = entries
        self._domains = domains
        self._mutations = mutations
        self._embedder = embedder

    def promote(self, id: str, target_kind: str, freshness: str = Freshness.WATCH.value) -> Entry:
        source_entry = self._entries.load(id)
        target_kind_enum = parse_kind(target_kind)
        key = (source_entry.kind.value, target_kind_enum.value)
        matrix = {
            (Kind.NOTE.value, Kind.CONCEPT.value): self._promote_note_to_concept,
            (Kind.NOTE.value, Kind.DOSSIER.value): self._promote_note_to_dossier,
            (Kind.DOSSIER.value, Kind.CONCEPT.value): self._promote_dossier_to_concept,
            (Kind.CONCEPT.value, Kind.DECISION.value): self._promote_concept_to_decision,
        }
        try:
            handler = matrix[key]
        except KeyError as exc:
            raise ValidationError(
                f"不允许的升格路径: {source_entry.kind.value} → {target_kind_enum.value}"
            ) from exc
        return handler(source_entry, freshness)

    def _apply_promotion(
        self,
        *,
        source_updates: list[PrimaryUpdate],
        created_entry: Entry,
    ) -> Entry:
        created_frontmatter = entry_to_frontmatter(created_entry)
        body = ""
        validate_ingest_payload(
            created_entry.kind,
            created_frontmatter,
            body,
            skip_body_floor=True,
        )
        _log_promote_body_exemption(created_entry.id, body, self._data_root)
        check_conflicts(
            self._registry,
            created_entry.kind,
            created_frontmatter,
            conflict_policy="strict",
            ignore_ids=set(),
        )
        created_path = allocate_unique_path(self._data_root, self._registry, created_entry)
        if created_path.name != f"{created_entry.slug}.md":
            created_entry.slug = created_path.stem
        updates = [
            *source_updates,
            PrimaryUpdate(old_path=None, new_path=created_path, entry=created_entry, body=""),
        ]
        self._mutations.apply_primary_updates(updates)
        affected = {created_entry.id}
        for update in source_updates:
            affected.add(update.entry.id)
        for relation in created_entry.relations:
            if self._registry.has_entry(relation.target):
                affected.add(relation.target)
        for entry_id in list(affected):
            affected.update(self._registry.neighbors(entry_id, direction="both"))
        _recompute_ids(self._registry, affected)
        enqueue_local_findings(affected, self._registry, embedder=self._embedder)
        created_entry.body = ""
        return self._entries.enrich_runtime_meta(created_entry)

    def _promote_note_to_concept(self, source_entry: Entry, freshness: str) -> Entry:
        del freshness
        if not isinstance(source_entry, NoteEntry):
            raise ValidationError("源条目不是 note")
        if not source_entry.source_refs:
            raise ValidationError("note 缺少 source_refs，无法升格为 concept")
        if (
            source_entry.promotion_targets
            and Kind.CONCEPT.value not in source_entry.promotion_targets
        ):
            raise ValidationError("promotion_targets 未允许升格到 concept")

        source_entry.status = Status.LEGACY
        source_entry.updated_at = _dt.date.today()
        promoted = ConceptEntry(
            id=generate_id(Kind.CONCEPT),
            kind=Kind.CONCEPT,
            title=source_entry.title,
            slug=generate_slug(source_entry.title),
            status=Status.ACTIVE,
            freshness=Freshness.STABLE,
            schema_version=REGISTRY_SCHEMA_VERSION,
            created_at=_dt.date.today(),
            updated_at=_dt.date.today(),
            aliases=list(source_entry.aliases),
            confidence=source_entry.confidence,
            tags=list(source_entry.tags),
            search_terms=list(source_entry.search_terms),
            domain=self._domains.infer_domain(entry_to_frontmatter(source_entry)),
            relations=[Relation(target=source_entry.id, type=RelationType.DERIVED_FROM)],
            evidence_refs=list(source_entry.source_refs),
            evidence_status=_evidence_status_from_refs(source_entry.source_refs),
        )
        return self._apply_promotion(
            source_updates=[
                PrimaryUpdate(
                    old_path=file_path_of(source_entry),
                    new_path=file_path_of(source_entry),
                    entry=source_entry,
                    body=source_entry.body or "",
                )
            ],
            created_entry=promoted,
        )

    def _promote_note_to_dossier(self, source_entry: Entry, freshness: str) -> Entry:
        if not isinstance(source_entry, NoteEntry):
            raise ValidationError("源条目不是 note")
        if not source_entry.source_refs:
            raise ValidationError("note 缺少 source_refs，无法升格为 dossier")
        if (
            source_entry.promotion_targets
            and Kind.DOSSIER.value not in source_entry.promotion_targets
        ):
            raise ValidationError("promotion_targets 未允许升格到 dossier")

        reviewed_at = _dt.date.today()
        dossier = DossierEntry(
            id=generate_id(Kind.DOSSIER),
            kind=Kind.DOSSIER,
            title=source_entry.title,
            slug=generate_slug(source_entry.title),
            status=Status.ACTIVE,
            freshness=Freshness(freshness),
            schema_version=REGISTRY_SCHEMA_VERSION,
            created_at=reviewed_at,
            updated_at=reviewed_at,
            aliases=list(source_entry.aliases),
            confidence=source_entry.confidence,
            tags=list(source_entry.tags),
            search_terms=list(source_entry.search_terms),
            domain=self._domains.infer_domain(entry_to_frontmatter(source_entry)),
            relations=[Relation(target=source_entry.id, type=RelationType.DERIVED_FROM)],
            reviewed_at=reviewed_at,
            review_due_at=compute_review_due(reviewed_at, freshness),
            evidence_refs=list(source_entry.source_refs),
        )
        return self._apply_promotion(source_updates=[], created_entry=dossier)

    def _promote_dossier_to_concept(self, source_entry: Entry, freshness: str) -> Entry:
        del freshness
        if not isinstance(source_entry, DossierEntry):
            raise ValidationError("源条目不是 dossier")
        if source_entry.reviewed_at is None:
            raise ValidationError("dossier 缺少 reviewed_at，无法升格")
        if (_dt.date.today() - source_entry.reviewed_at).days > 30:
            raise ValidationError("dossier reviewed_at 超过 30 天，无法升格为 concept")
        if not source_entry.evidence_refs:
            raise ValidationError(
                "dossier evidence_refs 为空，无法升格为 concept（指标前置校验失败）"
            )

        source_entry.status = Status.LEGACY
        source_entry.updated_at = _dt.date.today()
        concept = ConceptEntry(
            id=generate_id(Kind.CONCEPT),
            kind=Kind.CONCEPT,
            title=source_entry.title,
            slug=generate_slug(source_entry.title),
            status=Status.ACTIVE,
            freshness=Freshness.STABLE,
            schema_version=REGISTRY_SCHEMA_VERSION,
            created_at=_dt.date.today(),
            updated_at=_dt.date.today(),
            aliases=list(source_entry.aliases),
            confidence=source_entry.confidence,
            tags=list(source_entry.tags),
            search_terms=list(source_entry.search_terms),
            domain=self._domains.infer_domain(entry_to_frontmatter(source_entry)),
            relations=[Relation(target=source_entry.id, type=RelationType.SUPERSEDES)],
            evidence_refs=list(source_entry.evidence_refs),
            evidence_status=_evidence_status_from_refs(source_entry.evidence_refs),
        )
        return self._apply_promotion(
            source_updates=[
                PrimaryUpdate(
                    old_path=file_path_of(source_entry),
                    new_path=file_path_of(source_entry),
                    entry=source_entry,
                    body=source_entry.body or "",
                )
            ],
            created_entry=concept,
        )

    def _promote_concept_to_decision(self, source_entry: Entry, freshness: str) -> Entry:
        del freshness
        if not isinstance(source_entry, ConceptEntry):
            raise ValidationError("源条目不是 concept")
        if source_entry.status not in {Status.ACTIVE, Status.AUTHORITATIVE}:
            raise ValidationError("concept.status 必须是 active/authoritative 才能升格为 decision")
        if not source_entry.evidence_refs:
            raise ValidationError(
                "concept evidence_refs 为空，无法升格为 decision（治理红线 9：证据链不可断）"
            )

        decided_at = _dt.date.today()
        decision = DecisionEntry(
            id=generate_id(Kind.DECISION),
            kind=Kind.DECISION,
            title=source_entry.title,
            slug=generate_slug(source_entry.title),
            status=Status.ACTIVE,
            freshness=Freshness.STABLE,
            schema_version=REGISTRY_SCHEMA_VERSION,
            created_at=decided_at,
            updated_at=decided_at,
            aliases=list(source_entry.aliases),
            confidence=source_entry.confidence,
            tags=list(source_entry.tags),
            search_terms=list(source_entry.search_terms),
            domain=source_entry.domain,
            relations=[],
            decided_at=decided_at,
            decision_status="active",
            superseded_by=None,
            evidence_refs=list(source_entry.evidence_refs),
        )
        source_entry.updated_at = decided_at
        source_entry.relations = sorted(
            [
                *source_entry.relations,
                Relation(target=decision.id, type=RelationType.APPLIED_IN),
            ],
            key=lambda item: (item.target, item.type.value),
        )
        return self._apply_promotion(
            source_updates=[
                PrimaryUpdate(
                    old_path=file_path_of(source_entry),
                    new_path=file_path_of(source_entry),
                    entry=source_entry,
                    body=source_entry.body or "",
                )
            ],
            created_entry=decision,
        )


def _evidence_status_from_refs(refs: list[str]) -> EvidenceStatus:
    unique_count = len(set(refs))
    if unique_count >= 2:
        return "solid"
    if unique_count == 1:
        return "partial"
    return "weak"
