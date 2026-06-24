"""Proactive push L4 diagnose rules.

Canonical home for note-group and concept-split rules that were
previously duplicated in action / decay modules.
"""

from __future__ import annotations

from ..doctor import Finding, Severity
from ..models import ConceptEntry, DecisionEntry, DossierEntry, Kind
from ..registry import Registry
from ._helpers import (
    collect_source_refs,
    contradicts_count,
    metric_value,
    target_path,
    unabsorbed_note_groups,
)

# ADR 0003 §六类主动推送：高频 decision 依赖 volatile 前提；Phase 5 baseline 前临时拍板。
_PREMISE_SHAKEN_RETRIEVAL_HEAT_MIN = 3.0
_PREMISE_SHAKEN_DRIFT_SCORE_MIN = 0.75
# ADR 0003 §六类主动推送：note 信息密度够压缩成 concept；Phase 5 baseline 前临时拍板。
_CRYSTALLIZE_NOTE_GROUP_MIN = 3
# 业界标准：stable concept 至少 2 个独立 source_type 才不算伪稳定。
_PSEUDO_STABLE_SOURCE_DIVERSITY_MIN = 2
# ADR 0003 §六类主动推送：内部形成两团矛盾证据；Phase 5 baseline 前临时拍板。
_INTERNAL_SPLIT_CONTRADICTS_MIN = 2
# ADR 0003 §六类主动推送：认知分歧用 dossier drift + 热度近似；Phase 5 baseline 前临时拍板。
_COGNITIVE_DIVERGENCE_DRIFT_MIN = 0.7
_COGNITIVE_DIVERGENCE_HEAT_MIN = 2.0


def rule_push_premise_shaken(registry: Registry) -> list[Finding]:
    """高频 decision 依赖的前提已变成 volatile 或漂移。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.DECISION.value):
        decision = entry if isinstance(entry, DecisionEntry) else None
        if decision is None or decision.decision_status != "active":
            continue
        retrieval_heat = metric_value(decision, "retrieval_heat")
        if retrieval_heat < _PREMISE_SHAKEN_RETRIEVAL_HEAT_MIN:
            continue
        shaken_refs = _shaken_evidence_refs(decision, registry)
        drift_score = metric_value(decision, "drift_score")
        if drift_score < _PREMISE_SHAKEN_DRIFT_SCORE_MIN and not shaken_refs:
            continue
        findings.append(
            Finding(
                rule_id="push_premise_shaken",
                severity=Severity.HIGH,
                target_id=decision.id,
                target_path=target_path(decision),
                message=(
                    "前提震荡: "
                    f"retrieval_heat={retrieval_heat:g}, drift_score={drift_score:g}, "
                    f"shaken_refs={','.join(shaken_refs[:3])}"
                ),
            )
        )
    return findings


def rule_push_crystallize(registry: Registry) -> list[Finding]:
    """note 群达到结晶候选密度。"""

    findings: list[Finding] = []
    for group_key, notes in unabsorbed_note_groups(registry).items():
        if len(notes) < _CRYSTALLIZE_NOTE_GROUP_MIN:
            continue
        first = sorted(notes, key=lambda item: item.id)[0]
        findings.append(
            Finding(
                rule_id="push_crystallize",
                severity=Severity.MEDIUM,
                target_id=first.id,
                target_path=target_path(first),
                message=(
                    "该结晶了: "
                    f"group={group_key}, note_count={len(notes)}, "
                    "notes="
                    f"{','.join(note.id for note in sorted(notes, key=lambda item: item.id)[:3])}"
                ),
            )
        )
    return findings


def rule_push_pseudo_stable(registry: Registry) -> list[Finding]:
    """stable concept 只剩单一 source_type 支撑。

    Subtle difference from decay_concept_evidence_singular:
    pseudo_stable only fires for freshness=stable concepts with evidence_refs,
    while evidence_singular fires for any concept regardless of freshness.
    The threshold is the same (source diversity < 2) but the *population*
    is narrower, making this a higher-priority push signal.
    """

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.CONCEPT.value):
        concept = entry if isinstance(entry, ConceptEntry) else None
        if concept is None or concept.freshness.value != "stable" or not concept.evidence_refs:
            continue
        sources = collect_source_refs(concept.id, registry)
        diversity = len({source.source_type for source in sources})
        if diversity >= _PSEUDO_STABLE_SOURCE_DIVERSITY_MIN:
            continue
        findings.append(
            Finding(
                rule_id="push_pseudo_stable",
                severity=Severity.MEDIUM,
                target_id=concept.id,
                target_path=target_path(concept),
                message=(
                    f"伪稳定 concept: source_count={len(sources)}, source_diversity={diversity}"
                ),
            )
        )
    return findings


def rule_push_internal_split(registry: Registry) -> list[Finding]:
    """concept 内部形成矛盾证据团。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.CONCEPT.value):
        cc = contradicts_count(entry.id, registry)
        if cc < _INTERNAL_SPLIT_CONTRADICTS_MIN:
            continue
        findings.append(
            Finding(
                rule_id="push_internal_split",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=target_path(entry),
                message=f"该裂解了: contradicts_count={cc}",
            )
        )
    return findings


def rule_push_cognitive_divergence(registry: Registry) -> list[Finding]:
    """dossier 漂移且高热，提示刷新结果可能认知分歧。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.DOSSIER.value):
        dossier = entry if isinstance(entry, DossierEntry) else None
        if dossier is None:
            continue
        drift_score = metric_value(dossier, "drift_score")
        retrieval_heat = metric_value(dossier, "retrieval_heat")
        if (
            drift_score < _COGNITIVE_DIVERGENCE_DRIFT_MIN
            or retrieval_heat < _COGNITIVE_DIVERGENCE_HEAT_MIN
        ):
            continue
        findings.append(
            Finding(
                rule_id="push_cognitive_divergence",
                severity=Severity.MEDIUM,
                target_id=dossier.id,
                target_path=target_path(dossier),
                message=(
                    f"认知分歧候选: drift_score={drift_score:g}, retrieval_heat={retrieval_heat:g}"
                ),
            )
        )
    return findings


def _shaken_evidence_refs(decision: DecisionEntry, registry: Registry) -> list[str]:
    shaken: list[str] = []
    for ref_id in decision.evidence_refs:
        if not registry.has_entry(ref_id):
            continue
        ref = registry.get_entry(ref_id)
        if ref.freshness.value == "volatile" or registry.is_superseded(ref_id):
            shaken.append(ref_id)
    return sorted(shaken)
