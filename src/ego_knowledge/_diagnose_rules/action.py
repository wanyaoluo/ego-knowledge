"""Maintenance action L4 diagnose rules.

Removed rules (merged / superseded):
- promote  → push_crystallize (identical logic, better push context)
- split    → push_internal_split (identical logic, better push context)
"""

from __future__ import annotations

from collections import defaultdict

from ..doctor import Finding, Severity
from ..models import DecisionEntry, Entry, Kind
from ..registry import Registry
from ._helpers import contradicts_count, metric_value, target_path

# ADR 0003 §五种维护动作：降级由证据变薄或 contradicts 累积触发；Phase 5 baseline 前临时拍板。
_DEMOTE_DRIFT_SCORE_MIN = 0.5
_DEMOTE_CONTRADICTS_MIN = 2
# 临时拍板：search_terms Jaccard 重叠达到半数即进入合并候选，避开 title 近似冲突通道。
_MERGE_TERM_OVERLAP_MIN = 0.5
# ADR 0003 §五种维护动作：撤回要求前提被推翻；drift=1 或引用前提被 supersedes 即 high。
_RETRACT_DRIFT_SCORE_MIN = 1.0


def rule_action_demote(registry: Registry) -> list[Finding]:
    """证据变薄或反证累积时提示降级 freshness。"""

    findings: list[Finding] = []
    for entry in registry.all_entries():
        if entry.kind not in {Kind.CONCEPT, Kind.DOSSIER, Kind.DECISION}:
            continue
        drift_score = metric_value(entry, "drift_score")
        contradicts = contradicts_count(entry.id, registry)
        if drift_score < _DEMOTE_DRIFT_SCORE_MIN and contradicts < _DEMOTE_CONTRADICTS_MIN:
            continue
        findings.append(
            Finding(
                rule_id="action_demote",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=target_path(entry),
                message=(
                    "建议降级 freshness: "
                    f"drift_score={drift_score:g}, contradicts_count={contradicts}"
                ),
            )
        )
    return findings


def rule_action_merge(registry: Registry) -> list[Finding]:
    """search_terms 高重叠的同类知识建议合并。"""

    findings: list[Finding] = []
    by_kind: dict[Kind, list[Entry]] = defaultdict(list)
    for entry in registry.all_entries():
        if entry.kind in {Kind.SOURCE, Kind.VIEW}:
            continue
        by_kind[entry.kind].append(entry)

    for entries in by_kind.values():
        ordered = sorted(entries, key=lambda item: item.id)
        for index, left in enumerate(ordered):
            left_terms = _terms(left)
            if not left_terms:
                continue
            for right in ordered[index + 1 :]:
                if right.id in registry.neighbors(left.id, direction="both"):
                    continue
                score = _jaccard(left_terms, _terms(right))
                if score < _MERGE_TERM_OVERLAP_MIN:
                    continue
                findings.append(
                    Finding(
                        rule_id="action_merge",
                        severity=Severity.MEDIUM,
                        target_id=left.id,
                        target_path=target_path(left),
                        message=f"建议合并候选: {right.id}, term_overlap={score:.2f}",
                    )
                )
    return findings


def rule_action_retract(registry: Registry) -> list[Finding]:
    """decision 的前提被推翻时提示撤回。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.DECISION.value):
        decision = entry if isinstance(entry, DecisionEntry) else None
        if decision is None or decision.decision_status != "active":
            continue
        drift_score = metric_value(decision, "drift_score")
        broken_refs = [
            ref_id for ref_id in decision.evidence_refs if registry.is_superseded(ref_id)
        ]
        if drift_score < _RETRACT_DRIFT_SCORE_MIN and not broken_refs:
            continue
        findings.append(
            Finding(
                rule_id="action_retract",
                severity=Severity.HIGH,
                target_id=decision.id,
                target_path=target_path(decision),
                message=(
                    "建议撤回 decision: "
                    f"drift_score={drift_score:g}, broken_refs={','.join(broken_refs[:3])}"
                ),
            )
        )
    return findings


def _terms(entry: Entry) -> set[str]:
    return {term.strip().casefold() for term in entry.search_terms if term.strip()}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)
