"""Frontmatter value normalization, validation, and type coercion.

Pure functions with zero internal dependencies beyond models/errors/unicode_utils.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from .errors import ValidationError
from .models import (
    Confidence,
    DecisionStatus,
    EvidenceStatus,
    Freshness,
    Kind,
    Relation,
    RelationSource,
    RelationType,
    Status,
)
from .tokenizer import tokenize
from .unicode_utils import to_nfc

type JsonMap = dict[str, object]

# --- Value sets for validation ---

_CONFIDENCE_VALUES = frozenset({"low", "medium", "high"})
_EVIDENCE_STATUS_VALUES = frozenset({"solid", "partial", "weak"})
_DECISION_STATUS_VALUES = frozenset({"active", "superseded"})


# --- Enum normalization ---


def _normalize_kind(value: str) -> Kind:
    try:
        return Kind(value)
    except ValueError as exc:
        raise ValidationError(f"不支持的 kind: {value}") from exc


def _normalize_status(value: str) -> Status:
    try:
        return Status(value)
    except ValueError as exc:
        raise ValidationError(f"不支持的 status: {value}") from exc


def _normalize_freshness(value: str) -> Freshness:
    try:
        return Freshness(value)
    except ValueError as exc:
        raise ValidationError(f"不支持的 freshness: {value}") from exc


# --- String coercion ---


def _require_str(frontmatter_map: JsonMap, key: str) -> str:
    value = frontmatter_map.get(key)
    if not isinstance(value, str) or value == "":
        raise ValidationError(f"frontmatter 字段 {key} 缺失或不是非空字符串")
    return to_nfc(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("frontmatter 可选字符串字段类型错误")
    normalized = to_nfc(value)
    return normalized if normalized else None


# --- Date coercion ---


def _require_date(frontmatter_map: JsonMap, key: str) -> date:
    value = frontmatter_map.get(key)
    parsed = _optional_date(value)
    if parsed is None:
        raise ValidationError(f"frontmatter 字段 {key} 缺失或不是日期")
    return parsed


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValidationError(f"非法日期值: {value}") from exc
    raise ValidationError("frontmatter 日期字段类型错误")


# --- List coercion ---


def _optional_string_list(frontmatter_map: JsonMap, key: str) -> list[str]:
    value = frontmatter_map.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError(f"frontmatter 字段 {key} 不是字符串列表")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValidationError(f"frontmatter 字段 {key} 含非字符串元素")
        items.append(to_nfc(item))
    return items


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("FTS 字段要求字符串列表")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValidationError("FTS 字段列表中存在非字符串元素")
        result.append(to_nfc(item))
    return result


def _string_value(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError("FTS 字段要求字符串")
    return to_nfc(value)


# --- Relation parsing ---


def _parse_relations(value: object) -> list[Relation]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValidationError("frontmatter 字段 relations 不是列表")
    relations: list[Relation] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError("relations 项必须是字典")
        target = item.get("target")
        rel_type = item.get("type")
        rel_source = item.get("source", RelationSource.CONFIRMED.value)
        if not isinstance(target, str) or not isinstance(rel_type, str):
            raise ValidationError("relations.target/type 必须是字符串")
        if not isinstance(rel_source, str):
            raise ValidationError("relations.source 必须是字符串")
        try:
            relation = Relation(
                target=to_nfc(target),
                type=RelationType(rel_type),
                source=RelationSource(rel_source),
            )
        except ValueError as exc:
            raise ValidationError(f"非法 relation 定义: {item}") from exc
        relations.append(relation)
    return relations


# --- Enum validation ---


def _optional_confidence(value: object) -> Confidence | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("confidence 必须是字符串")
    normalized = to_nfc(value)
    if normalized not in _CONFIDENCE_VALUES:
        raise ValidationError(f"非法 confidence: {normalized}")
    return cast(Confidence, normalized)


def _optional_evidence_status(value: object) -> EvidenceStatus:
    if value is None:
        return "weak"
    if not isinstance(value, str):
        raise ValidationError("evidence_status 必须是字符串")
    normalized = to_nfc(value)
    if normalized not in _EVIDENCE_STATUS_VALUES:
        raise ValidationError(f"非法 evidence_status: {normalized}")
    return cast(EvidenceStatus, normalized)


def _optional_decision_status(value: object) -> DecisionStatus:
    if value is None:
        return "active"
    if not isinstance(value, str):
        raise ValidationError("decision_status 必须是字符串")
    normalized = to_nfc(value)
    if normalized not in _DECISION_STATUS_VALUES:
        raise ValidationError(f"非法 decision_status: {normalized}")
    return cast(DecisionStatus, normalized)


# --- Date serialization ---


def _date_to_text(value: date) -> str:
    return value.isoformat()


def _date_to_text_or_none(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _parse_datetime_text(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"非法 datetime 值: {value}") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _utc_now_text() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


# --- Tokenization helpers ---


def _tokenized_field(text: str, custom_dict_dir: Path, fallback_log_path: Path) -> str:
    return " ".join(
        tokenize(
            text,
            custom_dict_dir=custom_dict_dir,
            fallback_log_path=fallback_log_path,
        )
    )


def _extract_ascii_text(text: str) -> str:
    ascii_chars = [char if char.isascii() else " " for char in text]
    return "".join(ascii_chars)


# --- Metrics conversion ---

type Metrics = object


def _metrics_to_map(metrics: Metrics) -> JsonMap:
    if isinstance(metrics, dict):
        return cast(JsonMap, metrics)
    result: JsonMap = {}
    for key in (
        "evidence_strength",
        "drift_score",
        "compression_ratio",
        "action_relevance",
        "retrieval_heat",
    ):
        result[key] = getattr(metrics, key, 0)
    return result


def _as_float(value: object) -> float:
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return float(value)
    raise ValidationError(f"metrics 数值不能转成 float: {value!r}")


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    raise ValidationError(f"metrics 数值不能转成 int: {value!r}")
