"""Knowledge decay L4 diagnose rules.

Removed rules (merged / superseded):
- note_stagnant             → deleted (identical to structure_note_swamp which was also removed)
- concept_internal_split    → push_internal_split (identical logic, better push context)
"""

from __future__ import annotations

from ..doctor import Finding, Severity
from ..models import ConceptEntry, DossierEntry, Entry, Kind
from ..registry import Registry
from ._helpers import collect_source_refs, metric_value, target_path

# ADR 0003 §六种知识衰变模式：source 被新来源反驳/下游 supersedes。
_SOURCE_SUPERSEDES_MIN = 1
# 临时拍板：Phase 5 baseline 校准前，半数上游 source 漂移即视为 dossier 过期。
_DOSSIER_DRIFT_SCORE_MIN = 0.5
# 临时拍板：引用源晚于档案更新时间即视为相邻 source 更新。
_DOSSIER_NEWER_SOURCE_DAYS = 0
# 业界标准：单一证据源不可视为稳健 triangulation，至少 2 个独立来源。
_CONCEPT_SOURCE_DIVERSITY_MIN = 2


def rule_decay_source_context(registry: Registry) -> list[Finding]:
    """source 上下文被 supersedes 信号冲击。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.SOURCE.value):
        supersedes_count = registry.count_in_relations(entry.id, "supersedes")
        supersedes_count += registry.count_out_relations(entry.id, "superseded_by")
        if supersedes_count < _SOURCE_SUPERSEDES_MIN:
            continue
        findings.append(
            Finding(
                rule_id="decay_source_context",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=target_path(entry),
                message=f"source 上游语境已变化: supersedes_count={supersedes_count}",
            )
        )
    return findings


def rule_decay_dossier_outdated(registry: Registry) -> list[Finding]:
    """dossier 相邻 source 更新或漂移后未重写。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.DOSSIER.value):
        dossier = entry if isinstance(entry, DossierEntry) else None
        if dossier is None:
            continue
        drift_score = metric_value(dossier, "drift_score")
        newer_sources = _newer_source_ids(dossier, registry)
        if drift_score < _DOSSIER_DRIFT_SCORE_MIN and not newer_sources:
            continue
        findings.append(
            Finding(
                rule_id="decay_dossier_outdated",
                severity=Severity.MEDIUM,
                target_id=dossier.id,
                target_path=target_path(dossier),
                message=(
                    "dossier 上游已变化未同步: "
                    f"drift_score={drift_score:g}, newer_sources={','.join(newer_sources[:3])}"
                ),
            )
        )
    return findings


def rule_decay_concept_evidence_singular(registry: Registry) -> list[Finding]:
    """concept 证据来源过于单一。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.CONCEPT.value):
        concept = entry if isinstance(entry, ConceptEntry) else None
        if concept is None or not concept.evidence_refs:
            continue
        sources = collect_source_refs(concept.id, registry)
        diversity = len({source.source_type for source in sources})
        if diversity >= _CONCEPT_SOURCE_DIVERSITY_MIN:
            continue
        findings.append(
            Finding(
                rule_id="decay_concept_evidence_singular",
                severity=Severity.MEDIUM,
                target_id=concept.id,
                target_path=target_path(concept),
                message=(
                    "concept 证据来源单一: "
                    f"source_count={len(sources)}, source_diversity={diversity}"
                ),
            )
        )
    return findings


def _newer_source_ids(entry: Entry, registry: Registry) -> list[str]:
    ids: list[str] = []
    for source in collect_source_refs(entry.id, registry):
        captured_at = source.captured_at or source.updated_at
        if (captured_at - entry.updated_at).days > _DOSSIER_NEWER_SOURCE_DAYS:
            ids.append(source.id)
    return sorted(ids)
