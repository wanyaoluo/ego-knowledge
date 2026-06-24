"""Relation graph services for EgoKnowledge."""

from __future__ import annotations

import datetime as _dt

from ._entry_store import EntryStore, enqueue_local_findings
from ._validation import CachedEmbedder
from .errors import NotFoundError, ValidationError
from .frontmatter import _render_markdown
from .metrics import _recompute_ids, _record_access_many
from .models import Entry, Relation, RelationSource, RelationType, entry_to_frontmatter
from .paths import file_path_of
from .registry import Registry
from .transactions import transactional_write

_KIND_SPECIFIC_REF_FIELDS = frozenset(
    {"evidence_refs", "source_refs", "promotion_targets", "superseded_by"}
)


class RelationService:
    """Graph relationship mutations and traversals."""

    def __init__(
        self,
        registry: Registry,
        entries: EntryStore,
        *,
        embedder: CachedEmbedder | None = None,
    ) -> None:
        self._registry = registry
        self._entries = entries
        self._embedder = embedder

    def link(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        source: str = RelationSource.CONFIRMED.value,
    ) -> Relation:
        if rel_type in _KIND_SPECIFIC_REF_FIELDS:
            raise ValidationError(
                f"rel_type '{rel_type}' 是类型专属字段，应通过 update() 修改对应 Entry 字段；"
                "link() 只管通用 RelationType 枚举成员"
            )
        try:
            relation_type = RelationType(rel_type)
        except ValueError as exc:
            raise ValidationError(f"rel_type '{rel_type}' 不在 RelationType 枚举中") from exc
        try:
            relation_source = RelationSource(source)
        except ValueError as exc:
            raise ValidationError(f"不支持的 relation.source: {source}") from exc

        source_entry = self._entries.load(source_id)
        self._entries.load(target_id)
        existing_neighbors = set(self._registry.neighbors(source_id, direction="both"))
        relation = Relation(target=target_id, type=relation_type, source=relation_source)

        relations = list(source_entry.relations)
        replaced = False
        for index, current in enumerate(relations):
            if current.target == target_id and current.type == relation_type:
                relations[index] = relation
                replaced = True
                break
        if not replaced:
            relations.append(relation)
        relations.sort(key=lambda item: (item.target, item.type.value))
        source_entry.relations = relations
        source_entry.updated_at = _dt.date.today()

        frontmatter = entry_to_frontmatter(source_entry)
        body = source_entry.body or ""
        content = _render_markdown(frontmatter, body)
        source_path = file_path_of(source_entry)
        with transactional_write(source_path, content, self._registry.conn):
            self._registry.upsert_entry(source_entry, source_path, body)
        updated_neighbors = set(self._registry.neighbors(source_id, direction="both"))
        touched_set = {source_id, target_id} | existing_neighbors | updated_neighbors
        _recompute_ids(self._registry, touched_set)
        enqueue_local_findings(touched_set, self._registry, embedder=self._embedder)
        return relation

    def unlink(self, source_id: str, target_id: str) -> None:
        source_entry = self._entries.load(source_id)
        self._entries.load(target_id)
        existing_neighbors = set(self._registry.neighbors(source_id, direction="both"))
        original_relations = list(source_entry.relations)
        remaining = [relation for relation in original_relations if relation.target != target_id]
        if len(remaining) == len(original_relations):
            return
        source_entry.relations = remaining
        source_entry.updated_at = _dt.date.today()
        frontmatter = entry_to_frontmatter(source_entry)
        body = source_entry.body or ""
        source_path = file_path_of(source_entry)
        content = _render_markdown(frontmatter, body)
        with transactional_write(source_path, content, self._registry.conn):
            self._registry.upsert_entry(source_entry, source_path, body)
        updated_neighbors = set(self._registry.neighbors(source_id, direction="both"))
        touched_set = {source_id, target_id} | existing_neighbors | updated_neighbors
        _recompute_ids(self._registry, touched_set)
        enqueue_local_findings(touched_set, self._registry, embedder=self._embedder)

    def related(
        self,
        id: str,
        depth: int = 1,
        rel_type: str | None = None,
        *,
        include_archived: bool = False,
    ) -> list[Entry]:
        if not self._registry.has_entry(id):
            raise NotFoundError(f"未找到条目 {id}")
        if depth <= 0:
            raise ValidationError("depth 必须是正整数")

        visited = {id}
        current_layer = {id}
        ordered_ids: list[str] = []

        for _ in range(depth):
            next_layer: set[str] = set()
            for current_id in sorted(current_layer):
                for neighbor_id in self._registry.neighbors(
                    current_id,
                    rel_type=rel_type,
                    direction="both",
                    include_archived=include_archived,
                ):
                    if neighbor_id in visited:
                        continue
                    visited.add(neighbor_id)
                    next_layer.add(neighbor_id)
                    ordered_ids.append(neighbor_id)
            if not next_layer:
                break
            current_layer = next_layer

        entries = [
            self._entries.enrich_runtime_meta(self._entries.load(entry_id), include_body=True)
            for entry_id in ordered_ids
        ]
        _record_access_many(self._registry, ordered_ids, op="related")
        return entries

    def related_basic(self, id: str) -> list[Entry]:
        return self.related(id=id, depth=1, rel_type=None)
