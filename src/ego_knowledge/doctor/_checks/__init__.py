"""Check registry: aggregates all check handlers by rule_id."""

from __future__ import annotations

from .._types import CheckHandler
from .alias import _check_alias_conflicts
from .integrity import (
    _check_broken_relations,
    _check_dir_size_over_200,
    _check_frontmatter_body_link_diff,
    _check_id_uniqueness,
    _check_orphan_files,
    _check_schema_validation,
)
from .metrics import (
    _check_jieba_fallback_summary,
    _check_metrics_stale,
    _check_orphan_relation_type,
)
from .terminology import _check_terminology_audit
from .unicode import _check_fullwidth_chars, _check_nfc_residuals

_CHECK_REGISTRY: list[tuple[str, CheckHandler]] = [
    ("schema_validation", _check_schema_validation),
    ("broken_relations", _check_broken_relations),
    ("alias_conflicts", _check_alias_conflicts),
    ("frontmatter_body_link_diff", _check_frontmatter_body_link_diff),
    ("orphan_files", _check_orphan_files),
    ("dir_size_over_200", _check_dir_size_over_200),
    ("id_uniqueness", _check_id_uniqueness),
    ("terminology_near_duplicate", _check_terminology_audit),
    ("terminology_cross_language_alias", _check_terminology_audit),
    ("nfc_residual_in_frontmatter", _check_nfc_residuals),
    ("nfc_residual_in_path", _check_nfc_residuals),
    ("nfc_residual_in_markdown_link", _check_nfc_residuals),
    ("nfc_residual_in_source_url", _check_nfc_residuals),
    ("fullwidth_in_body", _check_fullwidth_chars),
    ("jieba_fallback_summary", _check_jieba_fallback_summary),
    ("metrics_stale", _check_metrics_stale),
    ("orphan_relation_type", _check_orphan_relation_type),
]

__all__: list[str] = [
    "_CHECK_REGISTRY",
    "_check_alias_conflicts",
    "_check_broken_relations",
    "_check_dir_size_over_200",
    "_check_frontmatter_body_link_diff",
    "_check_fullwidth_chars",
    "_check_id_uniqueness",
    "_check_jieba_fallback_summary",
    "_check_metrics_stale",
    "_check_nfc_residuals",
    "_check_orphan_files",
    "_check_orphan_relation_type",
    "_check_schema_validation",
    "_check_terminology_audit",
]
