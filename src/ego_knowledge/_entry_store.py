"""Entry repository helpers for EgoKnowledge."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import fcntl
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ._md_format import format_body
from ._validation import (
    CachedEmbedder,
    _validate_source_url,
    check_conflicts,
    ensure_reference_targets,
    is_materialized_change,
    revalidate_body_length,
    validate_ingest_payload,
    validate_schema,
    validate_search_terms,
    validate_update_fields,
)
from .errors import BodyBatchNotSupported, ConflictError, StorageError, ValidationError
from .frontmatter import (
    _extract_body,
    _fm_to_entry,
    _normalize_payload_map,
    _render_markdown,
)
from .local_rules import check_local_rules
from .maintenance_queue_store import enqueue as mq_enqueue
from .metrics import (
    _log_status_transition,
    _recompute_ids,
    _record_access,
    _zero_metrics,
)
from .models import (
    KIND_TO_CLASS,
    Entry,
    Freshness,
    Kind,
    Status,
    entry_to_frontmatter,
    generate_id,
    parse_id,
)
from .paths import allocate_unique_path, file_path_of, relative_path
from .registry import REGISTRY_SCHEMA_VERSION, Registry
from .slug import MAX_SLUG_LEN, _is_allowed_char, generate_slug
from .transactions import transactional_write
from .unicode_utils import to_nfc

InferDomain = Callable[[dict[str, object]], str]
ApplyMovedEntry = Callable[[Path, Path, Entry, str], None]

STABLE_SLUG_KINDS = frozenset({Kind.CONCEPT, Kind.DOSSIER, Kind.DECISION})
_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class PostCommitError:
    """post-commit step 失败记录。

    事务已提交无法回滚——保留宽捕获 + warning + exc_info 设计的同时，
    通过 EntryStore.post_commit_errors 让调用方可感知失败（玻璃盒「失败必须能被外部看见」）。
    """

    label: str
    entry_id: str
    exception: BaseException


@dataclasses.dataclass(frozen=True, slots=True)
class _PathChangeInfo:
    """update 内 path 迁移计算的中间结果。

    old_neighbors 必须在事务前捕获——path 迁移会改变邻居集合，事后无法还原。
    """

    old_path: Path
    new_path: Path
    path_changed: bool
    old_neighbors: set[str]

_FRESHNESS_DAYS: dict[str, int] = {"stable": 180, "watch": 30, "volatile": 7}
_DEFAULT_STATUS: dict[Kind, Status] = {
    Kind.SOURCE: Status.AUTHORITATIVE,
    Kind.NOTE: Status.ACTIVE,
    Kind.DOSSIER: Status.ACTIVE,
    Kind.CONCEPT: Status.ACTIVE,
    Kind.DECISION: Status.ACTIVE,
    Kind.VIEW: Status.ACTIVE,
}
_DEFAULT_FRESHNESS: dict[Kind, Freshness] = {
    Kind.SOURCE: Freshness.WATCH,
    Kind.NOTE: Freshness.WATCH,
    Kind.DOSSIER: Freshness.WATCH,
    Kind.CONCEPT: Freshness.STABLE,
    Kind.DECISION: Freshness.STABLE,
    Kind.VIEW: Freshness.WATCH,
}


def parse_kind(kind: str) -> Kind:
    try:
        return Kind(kind)
    except ValueError as exc:
        raise ValidationError(f"不支持的 kind: {kind}") from exc


def validate_explicit_slug(slug: str) -> str:
    normalized = to_nfc(slug).strip()
    if not normalized:
        raise ValidationError("slug 不能为空")
    try:
        generated = generate_slug(normalized)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if generated != normalized:
        raise ValidationError(f"slug 非法或需规范化，请显式传合法 slug: {slug}")
    if len(normalized) > MAX_SLUG_LEN or any(not _is_allowed_char(char) for char in normalized):
        raise ValidationError(f"slug 非法: {slug}")
    return normalized


def compute_review_due(reviewed_at: _dt.date, freshness: str) -> _dt.date:
    days = _FRESHNESS_DAYS.get(freshness)
    if days is None:
        raise ValidationError(f"不支持的 freshness: {freshness}")
    return reviewed_at + _dt.timedelta(days=days)


def enqueue_local_findings(
    touched_ids: set[str],
    registry: Registry,
    *,
    embedder: CachedEmbedder | None = None,
) -> None:
    """Run local rules for touched entries and enqueue their findings."""

    findings = check_local_rules(touched_ids, registry, embedder=embedder)
    for finding in findings:
        mq_enqueue(registry, finding)
    if findings:
        registry.commit()


class EntryStore:
    """File-backed repository for entry CRUD operations."""

    def __init__(
        self,
        data_root: Path,
        registry: Registry,
        *,
        infer_domain: InferDomain,
        embedder: CachedEmbedder | None = None,
    ) -> None:
        self._data_root = data_root
        self._registry = registry
        self._infer_domain = infer_domain
        self._embedder = embedder
        # post-commit step 失败队列；每次 update() 开头清空。
        # step 在事务提交后运行，异常无法回滚，故收集为可查询状态供调用方补偿。
        self._post_commit_errors: list[PostCommitError] = []

    @property
    def post_commit_errors(self) -> list[PostCommitError]:
        """本次 update() 的 post-commit step 失败记录（按发生顺序，只读副本）。

        每次 update() 开头清空。失败不向上抛出（事务已提交），调用方应通过此属性
        感知可观测副作用并自行决定补偿动作。
        """
        return list(self._post_commit_errors)

    def ingest(
        self,
        kind: str,
        payload: dict[str, object],
        conflict_policy: str = "strict",
    ) -> Entry:
        kind_enum = parse_kind(kind)
        normalized_payload = _normalize_payload_map(payload)
        frontmatter = self.prepare_ingest_frontmatter(kind_enum, normalized_payload)
        body = _extract_body(normalized_payload)
        validate_ingest_payload(kind_enum, frontmatter, body)
        check_conflicts(
            self._registry,
            kind_enum,
            frontmatter,
            conflict_policy=conflict_policy,
            ignore_ids=set(),
        )
        entry = _fm_to_entry(frontmatter)
        ensure_reference_targets(self._registry, entry)
        target_path = allocate_unique_path(self._data_root, self._registry, entry)
        if target_path.name != f"{entry.slug}.md":
            entry.slug = target_path.stem
            frontmatter["slug"] = entry.slug
        body = format_body(body)
        revalidate_body_length(kind_enum, body)
        content = _render_markdown(frontmatter, body)
        with transactional_write(target_path, content, self._registry.conn):
            self._registry.upsert_entry(entry, target_path, body)
        touched_set = {entry.id} | set(self._registry.neighbors(entry.id, direction="both"))
        _recompute_ids(self._registry, touched_set)
        enqueue_local_findings(touched_set, self._registry, embedder=self._embedder)
        entry.body = body
        return self.enrich_runtime_meta(entry)

    def update(
        self,
        id: str,
        changes: dict[str, object],
        *,
        apply_moved_entry: ApplyMovedEntry | None = None,
        status_context: dict[str, object] | None = None,
    ) -> Entry:
        self._post_commit_errors.clear()
        current = self.load(id)
        normalized_changes = _normalize_payload_map(changes)
        validate_update_fields(current, normalized_changes, stable_slug_kinds=STABLE_SLUG_KINDS)
        self._guard_domain_migration(current, normalized_changes)

        body = cast(str, normalized_changes.pop("body", current.body or ""))
        body_changed = "body" in changes
        frontmatter = self._build_update_frontmatter(current, normalized_changes)
        self._validate_update_payload(current, id, normalized_changes, frontmatter)

        updated = _fm_to_entry(frontmatter, file_path=current.file_path, body=body)
        path_info = self._resolve_path_change(current, updated, normalized_changes, id)
        if body_changed and path_info.path_changed:
            raise BodyBatchNotSupported()
        ensure_reference_targets(self._registry, updated)

        if path_info.path_changed:
            self._apply_path_migration(apply_moved_entry, path_info, updated, body)
        else:
            self._apply_body_only_change(current, updated, path_info, body, body_changed, id)

        self._run_post_commit_steps(id, normalized_changes, path_info.old_neighbors)
        self._record_status_transition(current, normalized_changes, id, status_context)
        return self.enrich_runtime_meta(updated)

    def _guard_domain_migration(
        self,
        current: Entry,
        normalized_changes: dict[str, object],
    ) -> None:
        """拒绝 concept/dossier 在 update 内迁移 domain（须走 domains_migrate）。"""
        if (
            current.kind in {Kind.CONCEPT, Kind.DOSSIER}
            and "domain" in normalized_changes
            and normalized_changes["domain"] != current.domain
        ):
            raise ValidationError("concept/dossier 改 domain 请走 domains_migrate()")

    def _build_update_frontmatter(
        self,
        current: Entry,
        normalized_changes: dict[str, object],
    ) -> dict[str, object]:
        """合并 normalized_changes 到 current frontmatter，并派生 title→slug。"""
        frontmatter = entry_to_frontmatter(current)
        frontmatter.update(normalized_changes)
        frontmatter["updated_at"] = _dt.date.today()
        if "title" in normalized_changes and current.kind not in STABLE_SLUG_KINDS:
            frontmatter["slug"] = generate_slug(cast(str, frontmatter["title"]))
        return frontmatter

    def _validate_update_payload(
        self,
        current: Entry,
        id: str,
        normalized_changes: dict[str, object],
        frontmatter: dict[str, object],
    ) -> None:
        """对更新后的 frontmatter 跑 schema / source_url / search_terms / conflict 校验。"""
        validate_schema(current.kind, frontmatter)
        if "source_url" in normalized_changes:
            _validate_source_url(cast(str, normalized_changes["source_url"]))
        if "search_terms" in normalized_changes or "title" in normalized_changes:
            validate_search_terms(
                cast(list[str], frontmatter["search_terms"]),
                cast(str, frontmatter["title"]),
            )
        if "title" in normalized_changes or "aliases" in normalized_changes:
            check_conflicts(
                self._registry,
                current.kind,
                frontmatter,
                conflict_policy="strict",
                ignore_ids={id},
            )

    def _resolve_path_change(
        self,
        current: Entry,
        updated: Entry,
        normalized_changes: dict[str, object],
        id: str,
    ) -> _PathChangeInfo:
        """计算 old/new path、是否迁移，并在事务前快照 old_neighbors。

        old_neighbors 必须先于 path 迁移捕获：迁移会改变邻居集合，事后无法还原。
        """
        old_neighbors = set(self._registry.neighbors(id, direction="both"))
        old_path = file_path_of(current)
        new_path = old_path
        path_changed = False
        if current.kind not in STABLE_SLUG_KINDS and "title" in normalized_changes:
            new_path = allocate_unique_path(self._data_root, self._registry, updated, current_id=id)
            path_changed = new_path != old_path
            if path_changed:
                updated.slug = new_path.stem
        return _PathChangeInfo(
            old_path=old_path,
            new_path=new_path,
            path_changed=path_changed,
            old_neighbors=old_neighbors,
        )

    def _apply_path_migration(
        self,
        apply_moved_entry: ApplyMovedEntry | None,
        path_info: _PathChangeInfo,
        updated: Entry,
        body: str,
    ) -> None:
        """委托 apply_moved_entry 完成文件迁移 + registry 更新（事务边界由执行器负责）。"""
        if apply_moved_entry is None:
            raise StorageError("update() 缺少路径迁移执行器")
        apply_moved_entry(path_info.old_path, path_info.new_path, updated, body)

    def _apply_body_only_change(
        self,
        current: Entry,
        updated: Entry,
        path_info: _PathChangeInfo,
        body: str,
        body_changed: bool,
        id: str,
    ) -> None:
        """无路径迁移分支：可选 body 格式化 + transactional_write 落盘。

        body_changed 时 format_body 可能改写 body，并写入 updated.body 供后续 enrich 读取。
        snapshot_entry_id 仅在 body_changed 时传入，触发旧 body 快照便于回滚恢复。
        """
        if body_changed:
            body = format_body(body)
            revalidate_body_length(current.kind, body)
            updated.body = body
        content = _render_markdown(entry_to_frontmatter(updated), body)
        with transactional_write(
            path_info.old_path,
            content,
            self._registry.conn,
            snapshot_entry_id=id if body_changed else None,
        ):
            self._registry.upsert_entry(updated, path_info.old_path, body)

    def _run_post_commit_steps(
        self,
        id: str,
        normalized_changes: dict[str, object],
        old_neighbors: set[str],
    ) -> None:
        """内部协调器，批量调度 post-commit step。

        与公开 ``run_post_commit_step``（单步执行器）配对使用。

        跑两个 post-commit step（metrics recompute + local findings）。
        事务已提交，异常无法回滚——任何失败都记入 self._post_commit_errors 供调用方查询，
        不向上冒泡。touched_set 的范围由 normalized_changes 是否触发 materialized 变更决定。
        """
        new_neighbors = set(self._registry.neighbors(id, direction="both"))
        if is_materialized_change(normalized_changes):
            touched_set = {id} | old_neighbors | new_neighbors
        else:
            touched_set = {id}
            for entry_id in list(touched_set):
                touched_set |= set(self._registry.neighbors(entry_id, direction="both"))
        self.run_post_commit_step(
            "metrics_recompute_failed",
            entry_id=id,
            step=lambda: _recompute_ids(self._registry, touched_set),
        )
        self.run_post_commit_step(
            "local_findings_failed",
            entry_id=id,
            step=lambda: enqueue_local_findings(
                touched_set,
                self._registry,
                embedder=self._embedder,
            ),
        )

    def run_post_commit_step(
        self,
        label: str,
        *,
        entry_id: str,
        step: Callable[[], None],
    ) -> None:
        """执行单个 post-commit step，失败入 self._post_commit_errors 队列并记 warning。

        公开方法：EntryStore 内部 step 与 facade 层（如 core.EgoKnowledge 的 dense_enqueue）
        共用同一失败登记通道。step 在事务提交后运行，异常无法回滚——保留宽捕获 + warning
        + exc_info 的设计，同时通过 ``post_commit_errors`` property 让调用方可感知失败
        （玻璃盒「失败必须能被外部看见」）。日志 message 带 label，可直接 grep 命中
        （w2：原共用 message 无法区分步骤）。
        """
        try:
            step()
        except Exception as exc:
            self._post_commit_errors.append(
                PostCommitError(label=label, entry_id=entry_id, exception=exc)
            )
            _LOGGER.warning(
                f"{label} 失败",
                extra={"event": label, "entry_id": entry_id},
                exc_info=True,
            )

    def _record_status_transition(
        self,
        current: Entry,
        normalized_changes: dict[str, object],
        id: str,
        status_context: dict[str, object] | None,
    ) -> None:
        """status 字段实际变更时（值差异）写 status transition 日志。"""
        status_change = normalized_changes.get("status")
        if "status" in normalized_changes and isinstance(status_change, str):
            if status_change != current.status.value:
                _log_status_transition(
                    self._data_root,
                    id,
                    status_change,
                    status_before=current.status.value,
                    context=status_context,
                )

    def get(self, entry_id: str) -> Entry:
        entry = self.enrich_runtime_meta(self.load(entry_id), include_body=True)
        _record_access(self._registry, entry_id, op="get")
        return entry

    def touch(self, entry_id: str) -> Entry:
        """Lightweight updated_at touch — sync file + DB + FTS, no full validation.

        Updates the Markdown frontmatter, entries table, and FTS index with
        the current date as ``updated_at``.  Skips schema / search_terms /
        conflict validation because only the timestamp changes.
        """
        current = self.load(entry_id)
        today = _dt.date.today()
        frontmatter = entry_to_frontmatter(current)
        frontmatter["updated_at"] = today
        body = current.body or ""
        content = _render_markdown(frontmatter, body)
        updated = _fm_to_entry(frontmatter, file_path=current.file_path, body=body)
        old_path = file_path_of(current)
        with transactional_write(old_path, content, self._registry.conn):
            self._registry.upsert_entry(updated, old_path, body)
        return self.enrich_runtime_meta(updated)

    def load(self, entry_id: str) -> Entry:
        return cast(Entry, self._registry.get_entry(entry_id))

    def enrich_runtime_meta(self, entry: Entry, *, include_body: bool = False) -> Entry:
        meta = self._registry.get_runtime_meta(entry.id)
        file_path = Path(cast(str, meta["file_path"]))
        entry.file_path = relative_path(self._data_root, file_path)
        metrics = dict(cast(dict[str, object], meta.get("metrics", {})))
        metrics.pop("updated_at", None)
        if not metrics:
            metrics = _zero_metrics()
        entry.metrics = metrics
        if include_body:
            entry.body = cast(str, meta.get("body", entry.body or ""))
        return entry

    def prepare_ingest_frontmatter(
        self,
        kind: Kind,
        payload: dict[str, object],
    ) -> dict[str, object]:
        allowed_fields = {field.name for field in dataclasses.fields(KIND_TO_CLASS[kind])}
        extra_fields = sorted(set(payload) - allowed_fields - {"body", "slug_override"})
        if extra_fields:
            raise ValidationError(f"ingest payload 含非法字段: {', '.join(extra_fields)}")

        title = payload.get("title")
        if not isinstance(title, str) or not title:
            raise ValidationError("ingest payload 缺少非空 title")

        frontmatter = dict(payload)
        frontmatter["kind"] = kind.value
        if "id" in frontmatter:
            raw_id = frontmatter["id"]
            if not isinstance(raw_id, str):
                raise ValidationError("id 必须是字符串")
            try:
                parsed_kind, _ = parse_id(raw_id)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
            if parsed_kind != kind:
                raise ValidationError(f"id 类型与 kind 不一致: {raw_id}")
            if self._registry.has_entry(raw_id):
                raise ConflictError(f"条目已存在: {raw_id}")
        else:
            frontmatter["id"] = generate_id(kind)

        explicit_slug = frontmatter.pop("slug_override", frontmatter.get("slug", None))
        if explicit_slug is None:
            frontmatter["slug"] = generate_slug(title)
        elif isinstance(explicit_slug, str):
            frontmatter["slug"] = validate_explicit_slug(explicit_slug)
        else:
            raise ValidationError("slug_override/slug 必须是字符串")

        today = _dt.date.today()
        frontmatter.setdefault("status", _DEFAULT_STATUS[kind].value)
        frontmatter.setdefault("freshness", _DEFAULT_FRESHNESS[kind].value)
        frontmatter.setdefault("schema_version", REGISTRY_SCHEMA_VERSION)
        frontmatter.setdefault("created_at", today)
        frontmatter.setdefault("updated_at", today)
        frontmatter.setdefault("aliases", [])
        frontmatter.setdefault("tags", [])
        frontmatter.setdefault("search_terms", [])
        frontmatter.setdefault("relations", [])

        if kind in {Kind.CONCEPT, Kind.DOSSIER}:
            frontmatter["domain"] = self._infer_domain(frontmatter)
        elif "domain" in frontmatter and frontmatter["domain"] in {"", None}:
            frontmatter["domain"] = None

        if kind == Kind.SOURCE:
            frontmatter.setdefault("captured_at", today)
        elif kind == Kind.NOTE:
            frontmatter.setdefault("extracted_at", today)
            frontmatter.setdefault("promotion_targets", [])
        elif kind == Kind.DOSSIER:
            freshness_value = cast(str, frontmatter["freshness"])
            reviewed_at = frontmatter.setdefault("reviewed_at", today)
            if isinstance(reviewed_at, str):
                reviewed_at = _dt.date.fromisoformat(reviewed_at)
                frontmatter["reviewed_at"] = reviewed_at
            frontmatter.setdefault(
                "review_due_at",
                compute_review_due(cast(_dt.date, reviewed_at), freshness_value),
            )
        elif kind == Kind.CONCEPT:
            frontmatter.setdefault("evidence_status", "weak")
        elif kind == Kind.DECISION:
            frontmatter.setdefault("decided_at", today)
            frontmatter.setdefault("decision_status", "active")
        elif kind == Kind.VIEW:
            frontmatter.setdefault("generated_at", today)

        return frontmatter


def _utc_now_text() -> str:
    from ._frontmatter_coercion import _utc_now_text as _fn

    return _fn()


def _log_promote_body_exemption(entry_id: str, body: str, data_root: Path) -> None:
    """记录 promote 路径的 body floor 豁免日志（追加写入 JSONL）。"""
    log_dir = data_root / "logs" / "refresh"
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "entry_id": entry_id,
        "body_length": len(body.strip()),
        "logged_at": _utc_now_text(),
        "reason": "promote_body_floor_exempt",
    }
    log_file = log_dir / "promote-body-exemption.jsonl"
    with log_file.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
