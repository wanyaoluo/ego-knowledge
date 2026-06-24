"""Terminology audit checks: near-duplicate terms and cross-language aliases."""

from __future__ import annotations

from itertools import combinations
from pathlib import Path

import Levenshtein

from ...registry import Registry
from ...unicode_utils import to_nfc
from .._helpers import _has_cjk_and_ascii
from .._types import Finding, Severity

_TERMINOLOGY_AUDIT_MAX_TERMS = 500


def _check_terminology_audit(registry: Registry, data_root: Path) -> list[Finding]:
    del data_root
    findings: list[Finding] = []
    all_terms = registry.all_terms_flat()

    # Short-circuit protection: cap O(n²) pairwise comparisons
    original_count = len(all_terms)
    if original_count > _TERMINOLOGY_AUDIT_MAX_TERMS:
        all_terms = all_terms[:_TERMINOLOGY_AUDIT_MAX_TERMS]
        findings.append(
            Finding(
                rule_id="_note",
                severity=Severity.LOW,
                target_id=None,
                target_path=None,
                message=(f"采样检查（{original_count} terms 中取 {_TERMINOLOGY_AUDIT_MAX_TERMS}）"),
            )
        )

    for left, right in combinations(all_terms, 2):
        term_a, entry_a, field_a = left
        term_b, entry_b, field_b = right
        if entry_a == entry_b:
            continue
        normalized_a = to_nfc(term_a).strip()
        normalized_b = to_nfc(term_b).strip()
        if len(normalized_a) < 3 or len(normalized_b) < 3:
            continue
        if Levenshtein.distance(normalized_a, normalized_b) > 2:
            continue
        findings.append(
            Finding(
                rule_id="terminology_near_duplicate",
                severity=Severity.MEDIUM,
                target_id=f"{entry_a} vs {entry_b}",
                target_path=None,
                message=(f"近似词: '{normalized_a}' ({field_a}) vs '{normalized_b}' ({field_b})"),
            )
        )

    entry_aliases = registry.entry_aliases_map()
    entry_ids = sorted(entry_aliases)
    for index, entry_a in enumerate(entry_ids):
        aliases_a = entry_aliases[entry_a]
        for entry_b in entry_ids[index + 1 :]:
            aliases_b = entry_aliases[entry_b]
            overlap = sorted(aliases_a & aliases_b)
            if not overlap:
                continue
            if not _has_cjk_and_ascii(aliases_a | aliases_b):
                continue
            findings.append(
                Finding(
                    rule_id="terminology_cross_language_alias",
                    severity=Severity.MEDIUM,
                    target_id=f"{entry_a} vs {entry_b}",
                    target_path=None,
                    message=f"跨语种别名重叠: {overlap}",
                )
            )

    return findings
