"""Validation helpers for EgoKnowledge payloads and graph references."""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any, Protocol, cast

import jsonschema  # type: ignore[import-untyped]
import Levenshtein
import numpy as np
import yaml  # type: ignore[import-untyped]

from .errors import (
    BodyBatchNotSupported,
    BodyFrontmatterMismatch,
    BodyInvalidUTF8,
    BodyLengthAboveMax,
    BodyLengthBelowMin,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from .models import (
    ConceptEntry,
    DecisionEntry,
    DossierEntry,
    Entry,
    Kind,
    NoteEntry,
    entry_to_frontmatter,
)
from .registry import Registry
from .unicode_utils import is_cjk_char, to_nfc

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"
_ALLOWED_CONFLICT_POLICIES = frozenset({"strict", "merge_suggest", "allow"})
_MATERIALIZED_FIELDS = frozenset(
    {"body", "relations", "evidence_refs", "source_refs", "promotion_targets", "superseded_by"}
)
MAX_SEARCH_TERM_LEN = 40
MIN_BODY_LEN = 50
MIN_UPDATE_BODY_BYTES = 1
MAX_UPDATE_BODY_BYTES = 40960
_FRONTMATTER_BOUNDARY = "---\n"
_BATCH_BODY_KEYS = frozenset({"entries", "entry_ids", "ids", "batch", "bodies"})


type Vector = list[float]


class CachedEmbedder(Protocol):
    def embed_cached(
        self,
        entry_id: str,
        embedding_content_hash: str,
        text: str,
    ) -> Vector:
        """Return one embedding from a local cache or an external embedder."""


def _require_str(payload: dict[str, Any], key: str) -> str:
    """Return a required non-empty string field from an operation payload."""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValidationError(f"payload 缺少非空字段: {key}")
    return value


def validate_update_fields(
    entry: Entry,
    changes: dict[str, object],
    *,
    stable_slug_kinds: frozenset[Kind],
) -> None:
    allowed_fields = {field.name for field in dataclasses.fields(type(entry))}
    if "body" in changes:
        _reject_batch_body_changes(changes)
        changes["body"] = _normalize_update_body(entry, changes)
    for field_name in sorted(changes):
        if field_name in {"id", "kind", "file_path", "metrics"}:
            raise ValidationError(f"字段 {field_name} 不允许通过 update() 修改")
        if field_name == "body":
            continue
        if field_name == "slug" and entry.kind in stable_slug_kinds:
            raise ValidationError("concept/dossier/decision 改 slug 请走 rename()")
        if field_name not in allowed_fields:
            raise ValidationError(f"字段 {field_name} 不属于 {entry.kind.value}，无法 update()")


def _reject_batch_body_changes(changes: dict[str, object]) -> None:
    if _BATCH_BODY_KEYS & set(changes):
        raise BodyBatchNotSupported()
    if isinstance(changes.get("body"), list):
        raise BodyBatchNotSupported()


def _normalize_update_body(entry: Entry, changes: dict[str, object]) -> str:
    raw_body = changes.get("body")
    if isinstance(raw_body, bytes):
        try:
            body = raw_body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BodyInvalidUTF8() from exc
    elif isinstance(raw_body, str):
        body = raw_body
    else:
        raise BodyInvalidUTF8()

    body_nfc = unicodedata.normalize("NFC", body)
    body_nfc = _strip_consistent_body_frontmatter(entry, changes, body_nfc)
    body_bytes = len(body_nfc.encode("utf-8"))
    if body_bytes < MIN_UPDATE_BODY_BYTES:
        raise BodyLengthBelowMin(body_bytes, MIN_UPDATE_BODY_BYTES)
    if body_bytes > MAX_UPDATE_BODY_BYTES:
        raise BodyLengthAboveMax(body_bytes, MAX_UPDATE_BODY_BYTES)
    return body_nfc


def _strip_consistent_body_frontmatter(
    entry: Entry,
    changes: dict[str, object],
    body: str,
) -> str:
    if not body.startswith(_FRONTMATTER_BOUNDARY):
        return body
    parts = body.split(_FRONTMATTER_BOUNDARY, 2)
    if len(parts) != 3:
        raise BodyFrontmatterMismatch("body frontmatter marker 不完整")
    _, raw_frontmatter, body_content = parts
    try:
        parsed = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as exc:
        raise BodyFrontmatterMismatch("body frontmatter 不是合法 YAML") from exc
    if not isinstance(parsed, dict):
        raise BodyFrontmatterMismatch("body frontmatter 必须是对象")

    expected = entry_to_frontmatter(entry)
    for key, value in changes.items():
        if key == "body":
            continue
        expected[key] = value
    normalized_expected = _schema_payload(expected)
    normalized_parsed = _schema_payload({str(key): value for key, value in parsed.items()})
    if not isinstance(normalized_expected, dict) or not isinstance(normalized_parsed, dict):
        raise BodyFrontmatterMismatch()
    for key, value in normalized_parsed.items():
        if normalized_expected.get(key) != value:
            raise BodyFrontmatterMismatch(f"字段 {key} 与目标 frontmatter 不一致")
    return body_content.lstrip("\n")


def validate_schema(kind: Kind, frontmatter: dict[str, object]) -> None:
    validator = jsonschema.Draft202012Validator(
        _schema_for_kind(kind),
        format_checker=jsonschema.FormatChecker(),
    )
    try:
        validator.validate(_schema_payload(frontmatter))
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.path)
        where = f" ({location})" if location else ""
        raise ValidationError(f"schema 校验失败{where}: {exc.message}") from exc


_ALLOWED_SOURCE_URL_PREFIXES = ("http://", "https://", "knowledge://")


def _validate_source_url(source_url: str) -> None:
    """白名单前缀硬闸：仅接受 http/https 网址或知识库内部路径。

    空字符串由 schema minLength 约束处理，此处不做额外校验。
    """
    if not source_url:
        return
    if not any(source_url.startswith(prefix) for prefix in _ALLOWED_SOURCE_URL_PREFIXES):
        raise ValidationError(
            f"source_url '{source_url}' 不在白名单内，仅接受 http/https 或 knowledge:// 前缀"
        )


def validate_search_terms(terms: list[str], title: str) -> None:
    if len(terms) < 5:
        raise ValidationError(f"search_terms 不足 5 个（当前 {len(terms)} 个）")
    title_nfc = to_nfc(title)
    has_cn = any(any(is_cjk_char(char) for char in term) for term in terms)
    has_en = any(
        term == "" or any(char.isascii() and char.isalpha() for char in term) for term in terms
    )
    has_alias_like = any(
        term and term != title_nfc and term not in title_nfc and title_nfc not in term
        for term in terms
    )
    problems: list[str] = []
    if not has_cn:
        problems.append("缺少中文主术语")
    if not has_en:
        problems.append('缺少英文术语或缩写（若概念无对应英文请加一条空字符串 "" 占位）')
    if not has_alias_like:
        problems.append("缺少常见别称或误写（空字符串不算）")
    if problems:
        raise ValidationError("search_terms 三桶未覆盖: " + "; ".join(problems))

    # ── F6.1 单项长度上限 ──
    oversize = [term for term in terms if len(term) > MAX_SEARCH_TERM_LEN]
    if oversize:
        samples = ", ".join(repr(term[:50]) for term in oversize[:3])
        raise ValidationError(f"search_terms 单项超长（>{MAX_SEARCH_TERM_LEN} 字符）: {samples}")

    # ── F6.2 空白拦截（仅允许英文桶单条空字符串占位）──
    empty_count = sum(1 for term in terms if term == "")
    whitespace_only = [term for term in terms if term != "" and not term.strip()]
    if whitespace_only:
        raise ValidationError(
            f"search_terms 含纯空白项（应使用 '' 显式占位或删除）: {whitespace_only}"
        )
    if empty_count > 1:
        raise ValidationError(
            f"search_terms 含多个空字符串占位（最多 1 个，当前 {empty_count} 个）"
        )

    # ── F6.3 NFC 去重 ──
    seen_nfc: set[str] = set()
    duplicates: list[str] = []
    for term in terms:
        if term == "":
            continue
        normalized = to_nfc(term)
        if normalized in seen_nfc:
            duplicates.append(term)
        else:
            seen_nfc.add(normalized)
    if duplicates:
        raise ValidationError(f"search_terms 含 NFC 等价重复项: {duplicates}")


def check_conflicts(
    registry: Registry,
    kind: Kind,
    payload: dict[str, object],
    *,
    conflict_policy: str,
    ignore_ids: set[str],
) -> None:
    if conflict_policy not in _ALLOWED_CONFLICT_POLICIES:
        raise ValidationError(f"不支持的 conflict_policy: {conflict_policy}")
    if conflict_policy == "allow":
        return

    title = payload.get("title")
    if not isinstance(title, str):
        raise ValidationError("title 缺失，无法做冲突检测")

    candidate_map = _collect_conflict_candidate_map(
        registry,
        kind,
        payload,
        ignore_ids=ignore_ids,
    )

    if candidate_map and conflict_policy == "strict":
        raise ConflictError(
            f"冲突：检测到候选重复条目 {sorted(candidate_map)}",
            details={
                "candidates": [
                    {"id": candidate_id, "title": candidate_map[candidate_id]}
                    for candidate_id in sorted(candidate_map)
                ]
            },
        )


def collect_conflict_candidates(
    registry: Registry,
    kind: Kind,
    frontmatter: dict[str, object],
    *,
    ignore_ids: set[str],
) -> list[str]:
    """Return candidate duplicate entry ids for local rules."""

    return sorted(
        _collect_conflict_candidate_map(
            registry,
            kind,
            frontmatter,
            ignore_ids=ignore_ids,
        )
    )


def collect_semantic_candidates(
    registry: Registry,
    embedder: CachedEmbedder,
    entry: Entry,
    *,
    ignore_ids: set[str],
    threshold: float,
    max_candidates: int = 10,
) -> list[str]:
    """Return same-kind entries whose stored dense vectors pass cosine threshold."""

    from ._dense_index import _build_embed_text, load_all_embeddings
    from ._embedding_hash import compute_embedding_content_hash

    text = _build_embed_text(entry)
    if not text.strip():
        return []

    entry_hash = compute_embedding_content_hash(entry)
    query_vec = np.asarray(
        embedder.embed_cached(entry.id, entry_hash, text),
        dtype=np.float32,
    )
    if query_vec.ndim != 1:
        raise ValueError("query embedding 必须是一维向量")
    norm_q = float(np.linalg.norm(query_vec))
    if norm_q == 0.0:
        return []
    query_vec = query_vec / norm_q

    all_embeddings = load_all_embeddings(registry)
    candidate_ids = [
        candidate_id
        for candidate_id in _filter_by_kind(registry, sorted(all_embeddings), entry.kind)
        if candidate_id not in ignore_ids
    ]
    if not candidate_ids:
        return []

    matrix = np.asarray(
        [all_embeddings[candidate_id] for candidate_id in candidate_ids],
        dtype=np.float32,
    )
    if matrix.ndim != 2 or matrix.shape[1] != query_vec.shape[0]:
        raise ValueError("candidate embedding 与 query embedding 维度不一致")
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    sims = (matrix / norms[:, None]) @ query_vec

    hits = [
        (candidate_ids[index], float(score))
        for index, score in enumerate(sims)
        if float(score) >= threshold
    ]
    hits.sort(key=lambda item: (-item[1], item[0]))
    return [entry_id for entry_id, _score in hits[:max_candidates]]


def _collect_conflict_candidate_map(
    registry: Registry,
    kind: Kind,
    payload: dict[str, object],
    *,
    ignore_ids: set[str],
) -> dict[str, str]:
    if kind == Kind.SOURCE:
        return {}

    title = payload.get("title")
    if not isinstance(title, str):
        raise ValidationError("title 缺失，无法做冲突检测")

    candidate_map: dict[str, str] = {}
    aliases = payload.get("aliases", [])
    alias_values: list[str] = []
    if isinstance(aliases, list):
        alias_values = [alias for alias in aliases if isinstance(alias, str)]
    new_aliases = {to_nfc(alias) for alias in alias_values if alias}
    new_aliases.add(to_nfc(title))

    for existing in registry.find_by_aliases(sorted(new_aliases)):
        if existing.id in ignore_ids:
            continue
        candidate_map.setdefault(existing.id, existing.title)

    title_nfc = to_nfc(title)
    for existing_id, existing_title in registry.all_titles_for_kind(kind.value):
        if existing_id in ignore_ids:
            continue
        existing_title_nfc = to_nfc(existing_title)
        if Levenshtein.ratio(title_nfc, existing_title_nfc) >= 0.85 or (
            max(len(title_nfc), len(existing_title_nfc)) >= 4
            and Levenshtein.distance(title_nfc, existing_title_nfc) <= 2
        ):
            candidate_map.setdefault(existing_id, existing_title)

    return candidate_map


def _filter_by_kind(registry: Registry, entry_ids: list[str], kind: Kind) -> list[str]:
    if not entry_ids:
        return []
    placeholders = ",".join("?" for _ in entry_ids)
    # entry_ids comes from placeholders only; the generated marker list is not user input.
    rows = registry.conn.execute(
        f"""
        SELECT id
          FROM entries
         WHERE kind = ?
           AND id IN ({placeholders})
         ORDER BY id
        """,
        (kind.value, *entry_ids),
    ).fetchall()
    available_ids = {cast(str, row["id"]) for row in rows}
    return [entry_id for entry_id in entry_ids if entry_id in available_ids]


def ensure_reference_targets(
    registry: Registry,
    entry: Entry,
    *,
    extra_ids: set[str] | None = None,
) -> None:
    allowed = extra_ids or set()
    targets = [relation.target for relation in entry.relations]
    if isinstance(entry, NoteEntry):
        targets.extend(entry.source_refs)
    if isinstance(entry, (DossierEntry, ConceptEntry, DecisionEntry)):
        targets.extend(entry.evidence_refs)
    if isinstance(entry, DecisionEntry) and entry.superseded_by:
        targets.append(entry.superseded_by)

    missing = sorted(
        {target for target in targets if target not in allowed and not registry.has_entry(target)}
    )
    if missing:
        raise NotFoundError(f"引用目标不存在: {', '.join(missing)}")


def is_materialized_change(changes: dict[str, object]) -> bool:
    return bool(_MATERIALIZED_FIELDS & set(changes))


def _schema_payload(value: object) -> object:
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if _is_dataclass_instance(value):
        return _schema_payload(_asdict(value))
    if isinstance(value, list):
        return [_schema_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _schema_payload(item) for key, item in value.items() if item is not None}
    return value


@cache
def _schema_for_kind(kind: Kind) -> dict[str, object]:
    common = _load_schema("entry-common.schema.json")
    specific = _load_schema(f"{kind.value}.schema.json")
    relation = _load_schema("relation.schema.json")
    common_properties = cast(dict[str, object], common.get("properties", {})).copy()
    relations = cast(dict[str, object], common_properties.get("relations", {})).copy()
    relations["items"] = relation
    common_properties["relations"] = relations
    required = list(cast(list[str], common.get("required", [])))
    required.extend(cast(list[str], specific.get("required", [])))
    return {
        "$schema": common.get("$schema"),
        "type": "object",
        "required": required,
        "properties": {
            **common_properties,
            **cast(dict[str, object], specific.get("properties", {})),
        },
    }


@cache
def _load_schema(filename: str) -> dict[str, object]:
    path = _SCHEMA_DIR / filename
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def _is_dataclass_instance(value: object) -> bool:
    return dataclasses.is_dataclass(value) and not isinstance(value, type)


def _asdict(value: object) -> dict[str, object]:
    return cast(dict[str, object], dataclasses.asdict(cast(Any, value)))


def _validate_body_length(kind: Kind, body: str) -> None:
    """护栏④：非 source 类 body 去空白后至少 MIN_BODY_LEN 字符。"""
    if kind == Kind.SOURCE:
        return
    stripped_len = len(body.strip())
    if stripped_len < MIN_BODY_LEN:
        raise ValidationError(
            f"{kind.value} body 过短（当前 {stripped_len} 字符，要求 ≥{MIN_BODY_LEN}）"
        )


def revalidate_body_length(kind: Kind, body: str) -> None:
    """Re-check body length after markdown formatting changes byte/character counts."""

    _validate_body_length(kind, body)
    body_bytes = len(body.encode("utf-8"))
    if body_bytes > MAX_UPDATE_BODY_BYTES:
        raise BodyLengthAboveMax(body_bytes, MAX_UPDATE_BODY_BYTES)


def validate_ingest_payload(
    kind: Kind,
    frontmatter: dict[str, object],
    body: str,
    *,
    skip_body_floor: bool = False,
) -> None:
    """共享校验层：schema + search_terms + body floor 三合一。

    Args:
        kind: 条目类型。
        frontmatter: 已规范化的 frontmatter。
        body: Markdown body 文本。
        skip_body_floor: 为 True 时跳过 body 长度校验（promote 路径使用）。
    """
    validate_schema(kind, frontmatter)
    if kind == Kind.SOURCE:
        _validate_source_url(cast(str, frontmatter["source_url"]))
    validate_search_terms(
        cast(list[str], frontmatter["search_terms"]),
        cast(str, frontmatter["title"]),
    )
    if not skip_body_floor:
        _validate_body_length(kind, body)
