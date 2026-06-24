"""Graph structure pathology L4 diagnose rules.

Removed rules (merged / superseded):
- note_swamp       → decay_note_stagnant (identical logic)
- orphan_decision  → redline_9_source_reachability (L3 superset)
- view_as_truth    → redline_10_view_as_evidence (L3 superset)
"""

from __future__ import annotations

from typing import cast

from ..doctor import Finding, Severity
from ..models import Kind
from ..registry import Registry
from ._helpers import collect_source_refs, metric_value, target_path

# 临时拍板：弱证据 <1.0 + 高决策相关度 >=3 判作化石概念，Phase 5 baseline 校准。
_FOSSIL_EVIDENCE_STRENGTH_MAX = 1.0
_FOSSIL_ACTION_RELEVANCE_MIN = 3.0
# 业界标准：证据 triangulation 至少 2 个独立 source_type。
_SOURCE_DIVERSITY_MIN = 2
_MONOCULTURE_SOURCE_COUNT_MIN = 2
# 临时拍板：任何 supersedes 有向环都需要拆链处理。
_SUPERSEDES_CYCLE_MIN_LEN = 2


def rule_structure_fossil_concept(registry: Registry) -> list[Finding]:
    """弱证据 concept 被广泛依赖。"""

    findings: list[Finding] = []
    for entry in registry.all_entries_by_kind(Kind.CONCEPT.value):
        evidence_strength = metric_value(entry, "evidence_strength")
        action_relevance = metric_value(entry, "action_relevance")
        if (
            evidence_strength >= _FOSSIL_EVIDENCE_STRENGTH_MAX
            or action_relevance < _FOSSIL_ACTION_RELEVANCE_MIN
        ):
            continue
        findings.append(
            Finding(
                rule_id="structure_fossil_concept",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=target_path(entry),
                message=(
                    "化石概念: "
                    f"evidence_strength={evidence_strength:g}, "
                    f"action_relevance={action_relevance:g}"
                ),
            )
        )
    return findings


def rule_structure_monoculture_evidence(registry: Registry) -> list[Finding]:
    """核心节点由同一 source_type 家族支撑。"""

    findings: list[Finding] = []
    for entry in registry.all_entries():
        if entry.kind not in {Kind.DOSSIER, Kind.CONCEPT, Kind.DECISION}:
            continue
        sources = collect_source_refs(entry.id, registry)
        diversity = len({source.source_type for source in sources})
        if len(sources) < _MONOCULTURE_SOURCE_COUNT_MIN or diversity >= _SOURCE_DIVERSITY_MIN:
            continue
        findings.append(
            Finding(
                rule_id="structure_monoculture_evidence",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=target_path(entry),
                message=(
                    f"证据单一栽培: source_count={len(sources)}, source_diversity={diversity}"
                ),
            )
        )
    return findings


def rule_structure_supersedes_cycle(registry: Registry) -> list[Finding]:
    """supersedes 关系形成有向环。"""

    adjacency = _supersedes_adjacency(registry)
    cycle_ids = _cycle_nodes(adjacency)
    findings: list[Finding] = []
    for entry_id in sorted(cycle_ids):
        if not registry.has_entry(entry_id):
            continue
        entry = registry.get_entry(entry_id)
        findings.append(
            Finding(
                rule_id="structure_supersedes_cycle",
                severity=Severity.MEDIUM,
                target_id=entry.id,
                target_path=target_path(entry),
                message=f"supersedes 关系成环: {entry.id}",
            )
        )
    return findings


def _supersedes_adjacency(registry: Registry) -> dict[str, set[str]]:
    rows = registry.conn.execute(
        """
        SELECT source_id, target_id
          FROM relations
         WHERE type = 'supersedes'
        """
    ).fetchall()
    adjacency: dict[str, set[str]] = {}
    for row in rows:
        source_id = cast(str, row["source_id"])
        target_id = cast(str, row["target_id"])
        adjacency.setdefault(source_id, set()).add(target_id)
    return adjacency


def _cycle_nodes(adjacency: dict[str, set[str]]) -> set[str]:
    cycle_nodes: set[str] = set()

    def visit(node: str, path: list[str], visiting: set[str]) -> None:
        if node in visiting:
            index = path.index(node)
            cycle = path[index:]
            if len(cycle) >= _SUPERSEDES_CYCLE_MIN_LEN:
                cycle_nodes.update(cycle)
            return
        visiting.add(node)
        path.append(node)
        for neighbor in sorted(adjacency.get(node, set())):
            visit(neighbor, path, visiting)
        path.pop()
        visiting.remove(node)

    for node in sorted(adjacency):
        visit(node, [], set())
    return cycle_nodes
