"""Core facade for EgoKnowledge CRUD and relationship management."""

from __future__ import annotations

import datetime as _dt
import logging
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypeGuard

from ._dense_embedder import DenseEmbedder, EmbedResult
from ._dense_queue import enqueue as enqueue_dense_embedding
from ._domains import DomainRegistry, MigrateResult
from ._entry_store import EntryStore, PostCommitError, enqueue_local_findings
from ._mutations import MutationService, PrimaryUpdate
from ._promotion import PromotionService
from ._relations import RelationService
from ._validation import CachedEmbedder, check_conflicts, validate_search_terms
from .errors import BodyBatchNotSupported, ConflictError, ValidationError
from .metrics import _recompute_ids, _record_access_many
from .models import Entry, Freshness, Kind, Relation, SourceEntry
from .paths import file_path_of, path_for_entry, relative_path
from .registry import Registry, RegistryStats
from .registry import build_registry as rebuild_registry
from .search import SearchResult
from .search import search as run_search
from .transactions import write_snapshot

if TYPE_CHECKING:
    from ._external_watch import PollResult
    from .diagnose import DiagnoseReport
    from .doctor import DoctorReport

type DenseEmbedderOverride = CachedEmbedder | None
_LOGGER = logging.getLogger(__name__)


class DenseSearchCapable(CachedEmbedder, Protocol):
    def embed_batch(self, texts: Sequence[str]) -> EmbedResult:
        """Return embeddings for dense search queries."""


__all__ = [
    "EgoKnowledge",
    "PostCommitError",
]


def _resolve_dense_embedder(
    override: DenseEmbedderOverride,
    disabled: bool,
    *,
    default_factory: Callable[[], DenseEmbedder | None],
) -> CachedEmbedder | None:
    if disabled:
        return None
    if override is None:
        return default_factory()
    return override


def _supports_dense_search(embedder: CachedEmbedder | None) -> TypeGuard[DenseSearchCapable]:
    return embedder is not None and callable(getattr(embedder, "embed_batch", None))


class EgoKnowledge:
    """Application-service facade. CLI / MCP / HTTP are thin shells on top."""

    def __init__(
        self,
        data_root: Path | str,
        *,
        registry: Registry | None = None,
        entry_store: EntryStore | None = None,
        domain_registry: DomainRegistry | None = None,
        mutation_service: MutationService | None = None,
        relation_service: RelationService | None = None,
        promotion_service: PromotionService | None = None,
        dense_embedder: DenseEmbedderOverride = None,
        dense_disabled: bool = False,
    ) -> None:
        self._data_root = Path(data_root)
        self._data_root.mkdir(parents=True, exist_ok=True)
        db_path = self._data_root / "registry" / "catalog.sqlite"
        self._registry = registry or Registry(db_path)
        self._registry.init_schema()
        self._dense_embedder = _resolve_dense_embedder(
            dense_embedder,
            dense_disabled,
            default_factory=self._build_default_dense_embedder,
        )
        self._reset_collaborators(
            entry_store=entry_store,
            domain_registry=domain_registry,
            mutation_service=mutation_service,
            relation_service=relation_service,
            promotion_service=promotion_service,
        )

    def _build_default_dense_embedder(self) -> DenseEmbedder | None:
        from ._secrets import get_siliconflow_api_key

        api_key = get_siliconflow_api_key()
        if not api_key:
            return None
        return DenseEmbedder(
            api_key,
            cache_dir=self._data_root / "cache" / "embeddings",
            log_dir=self._data_root / "logs" / "retrieval",
        )

    def _reset_collaborators(
        self,
        *,
        entry_store: EntryStore | None = None,
        domain_registry: DomainRegistry | None = None,
        mutation_service: MutationService | None = None,
        relation_service: RelationService | None = None,
        promotion_service: PromotionService | None = None,
    ) -> None:
        self._domains = domain_registry or DomainRegistry(self._data_root, self._registry)
        self._entries = entry_store or EntryStore(
            self._data_root,
            self._registry,
            infer_domain=self._domains.infer_domain,
            embedder=self._dense_embedder,
        )
        self._mutations = mutation_service or MutationService(
            self._data_root,
            self._registry,
            self._entries,
            move_file=lambda src, dst: os.rename(src, dst),
        )
        self._relations = relation_service or RelationService(
            self._registry,
            self._entries,
            embedder=self._dense_embedder,
        )
        self._promotion = promotion_service or PromotionService(
            self._data_root,
            self._registry,
            self._entries,
            self._domains,
            self._mutations,
            embedder=self._dense_embedder,
        )

    def close(self) -> None:
        self._registry.close()

    def build_registry(self) -> RegistryStats:
        self._registry.close()
        stats = rebuild_registry(self._data_root)
        db_path = self._data_root / "registry" / "catalog.sqlite"
        self._registry = Registry(db_path)
        self._registry.init_schema()
        self._dense_embedder = self._build_default_dense_embedder()
        self._reset_collaborators()
        return stats

    def ingest(
        self,
        kind: str,
        payload: dict[str, object],
        conflict_policy: str = "strict",
    ) -> Entry:
        entry = self._entries.ingest(kind, payload, conflict_policy=conflict_policy)
        enqueue_dense_embedding(self._data_root, entry.id)
        return entry

    def update(
        self,
        id: str,
        changes: dict[str, object],
        *,
        status_context: dict[str, object] | None = None,
    ) -> Entry:
        # Runtime MCP/JSON payloads can bypass the static ``str`` annotation.
        if "body" in changes and not isinstance(id, str):
            raise BodyBatchNotSupported()
        entry = self._entries.update(
            id,
            changes,
            apply_moved_entry=self._mutations.apply_moved_entry,
            status_context=status_context,
        )
        # dense_enqueue 是 facade 层的第 3 个 post-commit step（前 2 个在 EntryStore 内），
        # 复用 EntryStore.run_post_commit_step 统一登记失败，保证 post_commit_errors 队列
        # 覆盖全部 post-commit step（玻璃盒「失败必须能被外部看见」一致）。
        def _enqueue_dense() -> None:
            enqueue_dense_embedding(self._data_root, entry.id)

        self._entries.run_post_commit_step(
            "dense_enqueue_failed",
            entry_id=entry.id,
            step=_enqueue_dense,
        )
        return entry

    @property
    def post_commit_errors(self) -> list[PostCommitError]:
        """透传 ``EntryStore.post_commit_errors``——本次 update() 的 post-commit step 失败记录。

        CLI / MCP / AI orchestration 等外部调用方持有 ``EgoKnowledge`` 实例，通过此公共
        property 感知失败（事务已提交无法回滚），自行决定补偿动作。每次 ``update()`` 开头
        清空，不跨操作累积。
        """
        return self._entries.post_commit_errors

    def get(self, id: str) -> Entry:
        return self._entries.get(id)

    def touch(self, id: str) -> Entry:
        """Lightweight updated_at touch — sync file + DB + FTS, no full validation."""
        return self._entries.touch(id)

    def search(
        self,
        query: str,
        kinds: list[str] | None = None,
        filters: dict[str, object] | None = None,
        backends: list[str] | None = None,
        limit: int = 20,
        expand_graph: bool = True,
        include_archived: bool = False,
    ) -> list[SearchResult]:
        if self._dense_embedder is not None:
            from ._dense_queue import drain as drain_dense_queue

            drain_dense_queue(
                self._registry,
                self._dense_embedder,
                self._data_root,
                max_items=10,
            )
        search_embedder = (
            self._dense_embedder if _supports_dense_search(self._dense_embedder) else None
        )
        results = run_search(
            self._registry,
            query=query,
            kinds=kinds,
            filters=filters,
            backends=backends,
            limit=limit,
            expand_graph=expand_graph,
            data_root=self._data_root,
            embedder=search_embedder,
            include_archived=include_archived,
        )
        _record_access_many(self._registry, [result.id for result in results], op="search")
        return results

    def dense_index_populated(self) -> bool:
        return self._registry.dense_index_populated()

    def dense_embedder_available(self) -> bool:
        return self._dense_embedder is not None

    def rebuild_dense_index(
        self,
        embedder: DenseEmbedder,
        *,
        only_stale: bool,
        resume: bool,
        batch_size: int,
    ) -> dict[str, int]:
        from ._dense_index import default_progress_log, rebuild_all

        return rebuild_all(
            self._registry,
            embedder,
            only_stale=only_stale,
            progress_log=default_progress_log(self._data_root),
            resume=resume,
            batch_size=batch_size,
        )

    def list_sources_by_target(self, target: str) -> list[SourceEntry]:
        all_sources = self._registry.all_entries_by_kind("source")
        return [
            entry
            for entry in all_sources
            if isinstance(entry, SourceEntry) and entry.watch_target == target
        ]

    def source_exists_by_hash(self, content_hash: str) -> bool:
        row = self._registry.conn.execute(
            "SELECT 1 FROM source_fields WHERE content_hash = ? LIMIT 1",
            (content_hash,),
        ).fetchone()
        return row is not None

    def link(
        self,
        source_id: str,
        target_id: str,
        rel_type: str,
        source: str = "confirmed",
    ) -> Relation:
        return self._relations.link(source_id, target_id, rel_type, source=source)

    def unlink(self, source_id: str, target_id: str) -> None:
        self._relations.unlink(source_id, target_id)

    def promote(self, id: str, target_kind: str, freshness: str = Freshness.WATCH.value) -> Entry:
        return self._promotion.promote(id, target_kind, freshness)

    def rename(self, id: str, new_slug: str) -> Entry:
        return self._mutations.rename(id, new_slug)

    def domains_list(self) -> list[dict[str, object]]:
        return self._domains.list_domains()

    def domains_add(self, name: str) -> None:
        self._domains.add(name)

    def domains_migrate(self, entries: list[str], target_domain: str) -> MigrateResult:
        if not entries:
            raise ValidationError("domains_migrate 需要至少一个 entry id")
        normalized_domain = self._domains.normalize_name(target_domain)
        if normalized_domain not in self._domains.load_vocab():
            raise ValidationError(f"目标 domain 不存在: {normalized_domain}")

        rewrites: list[PrimaryUpdate] = []
        migrated_ids: list[str] = []
        for entry_id in entries:
            entry = self._entries.load(entry_id)
            entry.domain = normalized_domain
            entry.updated_at = _dt.date.today()
            old_path = file_path_of(entry)
            new_path = old_path
            if entry.kind in {Kind.CONCEPT, Kind.DOSSIER}:
                new_path = path_for_entry(self._data_root, entry, slug=entry.slug)
                if new_path != old_path and new_path.exists():
                    raise ConflictError(f"domain 迁移目标已存在: {new_path.name}")
            rewrites.append(
                PrimaryUpdate(
                    old_path=old_path,
                    new_path=new_path,
                    entry=entry,
                    body=entry.body or "",
                )
            )
            migrated_ids.append(entry_id)

        mutations = self._mutations.apply_primary_updates(rewrites)
        touched_set = set(migrated_ids)
        for entry_id in list(touched_set):
            touched_set |= set(self._registry.neighbors(entry_id, direction="both"))
        _recompute_ids(self._registry, touched_set)
        enqueue_local_findings(touched_set, self._registry, embedder=self._dense_embedder)
        return MigrateResult(
            entry_ids=sorted(migrated_ids),
            rewritten_paths=sorted(
                {relative_path(self._data_root, mutation.new_path) for mutation in mutations}
            ),
            target_domain=normalized_domain,
        )

    def related(
        self,
        id: str,
        depth: int = 1,
        rel_type: str | None = None,
        include_archived: bool = False,
    ) -> list[Entry]:
        return self._relations.related(
            id,
            depth=depth,
            rel_type=rel_type,
            include_archived=include_archived,
        )

    def related_basic(self, id: str) -> list[Entry]:
        return self._relations.related_basic(id)

    def doctor(self, repair: bool = False) -> DoctorReport:
        from .doctor import doctor as run_doctor

        return run_doctor(self._registry, self._data_root, repair=repair)

    def diagnose(self) -> DiagnoseReport:
        from .diagnose import diagnose as run_diagnose

        return run_diagnose(self._registry, self._data_root)

    def recompute_authority(self) -> int:
        from .metrics import full_recompute

        return full_recompute(self._registry)

    def establish_diagnose_baseline(self) -> Path:
        from .diagnose import establish_baseline

        return establish_baseline(self._registry, self._data_root)

    def stats(self, group_by: str | None = None) -> dict[str, object]:
        return self._registry.stats(group_by=group_by)

    def write_stats_snapshot(self, data: dict[str, object]) -> Path:
        return self._write_snapshot(data)

    def add_external_watch(self, target: str) -> str:
        from ._external_watch import add_watch

        return add_watch(self._registry, target)

    def list_external_watches(self) -> list[dict[str, object]]:
        from ._external_watch import list_watches

        return list_watches(self._registry)

    def poll_external_watches(self, *, token: str | None = None) -> PollResult:
        from ._external_watch import poll_all

        return poll_all(self._registry, data_root=self._data_root, token=token)

    def review_queue(
        self,
        overdue_only: bool = False,
        *,
        include_archived: bool = False,
    ) -> list[Entry]:
        return [
            self._entries.enrich_runtime_meta(entry)
            for entry in self._registry.review_queue(
                overdue_only=overdue_only,
                include_archived=include_archived,
            )
        ]

    def maintenance_queue_review(
        self,
        *,
        queue_id: str | None = None,
        resolve_id: str | None = None,
        dismiss_id: str | None = None,
        origin: str | None = None,
    ) -> dict[str, object]:
        """Interact with the maintenance_queue for ``ek review`` CLI.

        Modes:
        - ``queue_id``: return details of a single queue item.
        - ``resolve_id``: mark item as resolved.
        - ``dismiss_id``: mark item as dismissed.
        - all None (default): list pending items and update ``last_reviewed_at``.
        """
        from .maintenance_queue_store import dismiss as mq_dismiss
        from .maintenance_queue_store import list_queue as mq_list
        from .maintenance_queue_store import resolve as mq_resolve

        # Mutating operations first
        if resolve_id is not None:
            mq_resolve(self._registry, resolve_id)
            return {"ok": True, "action": "resolved", "id": resolve_id}

        if dismiss_id is not None:
            mq_dismiss(self._registry, dismiss_id)
            return {"ok": True, "action": "dismissed", "id": dismiss_id}

        # Detail view
        if queue_id is not None:
            rows = mq_list(self._registry, status=None, origin=origin)
            for row in rows:
                if row["id"] == queue_id:
                    return row
            from .errors import NotFoundError

            raise NotFoundError(f"队列条目不存在: {queue_id}")

        # Default: list pending + update last_reviewed_at
        pending = mq_list(self._registry, status="pending", origin=origin)

        # Read last_reviewed_at from registry_meta
        row = self._registry.conn.execute(
            "SELECT value FROM registry_meta WHERE key = 'last_reviewed_at'"
        ).fetchone()
        last_reviewed_at = row["value"] if row else "1970-01-01T00:00:00"

        # Update last_reviewed_at
        from datetime import UTC, datetime

        now_iso = datetime.now(tz=UTC).isoformat(timespec="microseconds")
        self._registry.conn.execute(
            """
            INSERT INTO registry_meta(key, value)
            VALUES('last_reviewed_at', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (now_iso,),
        )
        self._registry.commit()

        # Separate new items (created strictly after last_reviewed_at)
        # Microsecond precision avoids false collisions when items are created
        # within the same wall-clock second as the previous review.
        def _is_new_queue_item(item: dict[str, object]) -> bool:
            created_at = item.get("created_at")
            return isinstance(created_at, str) and created_at >= last_reviewed_at

        new_items = [item for item in pending if _is_new_queue_item(item)]

        # 当指定 origin 过滤时，保持原有扁平列表输出
        if origin is not None:
            return {
                "total": len(pending),
                "new_count": len(new_items),
                "items": pending,
                "new_items": new_items,
                "last_reviewed_at": last_reviewed_at,
                "reviewed_at": now_iso,
            }

        # 默认：按 origin 三态分组输出（空段隐藏）
        grouped: dict[str, list[dict[str, object]]] = {}
        for item in pending:
            item_origin = str(item.get("origin", "human"))
            grouped.setdefault(item_origin, []).append(item)

        return {
            "total": len(pending),
            "new_count": len(new_items),
            "grouped": grouped,
            "last_reviewed_at": last_reviewed_at,
            "reviewed_at": now_iso,
        }

    def _write_snapshot(self, data: dict[str, object], data_root: Path | None = None) -> Path:
        return write_snapshot(data, data_root or self._data_root)

    def _validate_search_terms(self, terms: list[str], title: str) -> None:
        validate_search_terms(terms, title)

    def _check_conflicts(
        self,
        kind: Kind,
        payload: dict[str, object],
        *,
        conflict_policy: str,
        ignore_ids: set[str],
    ) -> None:
        check_conflicts(
            self._registry,
            kind,
            payload,
            conflict_policy=conflict_policy,
            ignore_ids=ignore_ids,
        )
