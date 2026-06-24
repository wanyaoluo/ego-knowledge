"""Tests for L4 action diagnose rules.

Removed test cases (rules merged / superseded):
- test_action_promote_*   → push_crystallize (identical logic, better push context)
- test_action_split_*     → push_internal_split (identical logic, better push context)
"""

from __future__ import annotations

from ego_knowledge._diagnose_rules import action

from .support import concept_payload, decision_payload, source_payload


def _set_metrics(ek, entry_id: str, **metrics: float) -> None:
    registry = ek._registry
    assignments = ", ".join(f"{key} = ?" for key in metrics)
    registry.conn.execute(
        f"UPDATE entry_metrics SET {assignments} WHERE entry_id = ?",
        (*metrics.values(), entry_id),
    )
    registry.commit()


def _relation(target: str, rel_type: str) -> dict[str, str]:
    return {"target": target, "type": rel_type, "source": "confirmed"}


def _source_payload(title: str, index: str) -> dict[str, object]:
    return source_payload(
        title=title,
        source_type="web",
        source_url=f"https://example.com/action/{index}",
        content_hash=f"hash-action-{index}",
        search_terms=[
            title,
            f"action-source-{index}",
            f"origin-{index}",
            f"动作来源{index}",
            f"alias-src-{index}",
        ],
    )


def _concept_payload(
    source_id: str,
    title: str,
    index: str,
    **overrides: object,
) -> dict[str, object]:
    payload = concept_payload(
        source_id,
        title=title,
        search_terms=[
            title,
            f"action-concept-{index}",
            f"idea-{index}",
            f"动作概念{index}",
            f"alias-concept-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def _decision_payload(
    evidence_ref: str,
    title: str,
    index: str,
    **overrides: object,
) -> dict[str, object]:
    payload = decision_payload(
        evidence_ref,
        title=title,
        search_terms=[
            title,
            f"action-decision-{index}",
            f"choice-{index}",
            f"动作决策{index}",
            f"alias-decision-{index}",
        ],
    )
    payload.update(overrides)
    return payload


def test_action_demote_hit_for_drift_score(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("动作降级来源甲", "demote-hit"))
    concept = fresh_ek.ingest("concept", _concept_payload(source.id, "珊瑚降级概念", "demote-hit"))
    _set_metrics(fresh_ek, concept.id, drift_score=0.5)

    findings = action.rule_action_demote(fresh_ek._registry)

    assert any(finding.target_id == concept.id for finding in findings)


def test_action_demote_miss_for_stable_metrics(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("动作降级来源乙", "demote-miss"))
    fresh_ek.ingest("concept", _concept_payload(source.id, "榆树稳定概念", "demote-miss"))

    assert action.rule_action_demote(fresh_ek._registry) == []


def test_action_merge_hit_for_term_overlap(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", _source_payload("动作合并来源甲", "merge-left"))
    source_b = fresh_ek.ingest("source", _source_payload("动作合并来源乙", "merge-right"))
    terms_a = ["星桥合并概念", "merge-alpha", "shared-core", "共同语义", "shared-alias"]
    terms_b = ["南斗融合条目", "merge-alpha", "shared-core", "共同语义", "shared-alias"]
    left = fresh_ek.ingest(
        "concept",
        _concept_payload(source_a.id, "星桥合并概念", "merge-left", search_terms=terms_a),
    )
    fresh_ek.ingest(
        "concept",
        _concept_payload(source_b.id, "南斗融合条目", "merge-right", search_terms=terms_b),
    )

    findings = action.rule_action_merge(fresh_ek._registry)

    assert findings[0].target_id == left.id


def test_action_merge_miss_for_low_term_overlap(fresh_ek) -> None:
    source_a = fresh_ek.ingest("source", _source_payload("动作合并来源丙", "merge-low-a"))
    source_b = fresh_ek.ingest("source", _source_payload("动作合并来源丁", "merge-low-b"))
    fresh_ek.ingest("concept", _concept_payload(source_a.id, "松针合并概念", "merge-low-a"))
    fresh_ek.ingest("concept", _concept_payload(source_b.id, "海雾独立条目", "merge-low-b"))

    assert action.rule_action_merge(fresh_ek._registry) == []


def test_action_retract_hit_for_superseded_premise(fresh_ek) -> None:
    old_source = fresh_ek.ingest("source", _source_payload("动作撤回旧源", "retract-old"))
    new_source = fresh_ek.ingest("source", _source_payload("动作撤回新源", "retract-new"))
    concept = fresh_ek.ingest(
        "concept",
        _concept_payload(old_source.id, "岩层撤回概念", "retract-concept"),
    )
    decision = fresh_ek.ingest(
        "decision",
        _decision_payload(concept.id, "灯塔撤回决策", "retract-hit"),
    )
    fresh_ek.link(new_source.id, concept.id, "supersedes")

    findings = action.rule_action_retract(fresh_ek._registry)

    assert findings[0].target_id == decision.id
    assert findings[0].severity.value == "high"


def test_action_retract_miss_for_intact_premise(fresh_ek) -> None:
    source = fresh_ek.ingest("source", _source_payload("动作撤回来源乙", "retract-miss"))
    concept = fresh_ek.ingest(
        "concept",
        _concept_payload(source.id, "云杉撤回概念", "retract-miss-concept"),
    )
    fresh_ek.ingest(
        "decision",
        _decision_payload(concept.id, "石阶稳定决策", "retract-miss"),
    )

    assert action.rule_action_retract(fresh_ek._registry) == []
