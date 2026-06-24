from __future__ import annotations

import json

from ego_knowledge.doctor import Finding
from ego_knowledge.local_rules import check_local_rules

from .support import concept_payload, note_payload, source_payload

_LOCAL_RULES_TAG = "局部规则标签"
_EXISTING_RELATION_TAG = "已关联标签"


def _set_metrics(
    ek: object,
    entry_id: str,
    *,
    compression_ratio: float = 0.0,
    action_relevance: float = 0.0,
) -> None:
    registry = ek._registry  # type: ignore[attr-defined]
    registry.conn.execute(
        """
        UPDATE entry_metrics
           SET compression_ratio = ?, action_relevance = ?
         WHERE entry_id = ?
        """,
        (compression_ratio, action_relevance, entry_id),
    )
    registry.commit()


def _patch_frontmatter_field(ek: object, entry_id: str, field: str, value: object) -> None:
    registry = ek._registry  # type: ignore[attr-defined]
    row = registry.conn.execute(
        "SELECT frontmatter_json FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()
    frontmatter = json.loads(row["frontmatter_json"])
    frontmatter[field] = value
    registry.conn.execute(
        "UPDATE entries SET frontmatter_json = ? WHERE id = ?",
        (json.dumps(frontmatter, ensure_ascii=False), entry_id),
    )
    registry.commit()


def _findings_for_rule(findings: list[Finding], rule_id: str) -> list[Finding]:
    return [finding for finding in findings if finding.rule_id == rule_id]


def _relation_hint_terms(title: str, token: str) -> list[str]:
    return [title, token, f"{token}-abbr", f"{title}索引", f"{token}-alias"]


def test_duplicate_candidate_hit(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="局部重复来源"))
    original = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="局部重复概念", aliases=["local-dup"]),
    )
    duplicate = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="局部重复副本", aliases=["local-dup"]),
        conflict_policy="allow",
    )

    findings = check_local_rules({duplicate.id}, fresh_ek._registry)

    duplicate_findings = _findings_for_rule(findings, "duplicate_candidate")
    assert len(duplicate_findings) == 1
    assert original.id in duplicate_findings[0].message


def test_duplicate_candidate_miss(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="局部无重复来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="局部无重复概念"))

    findings = check_local_rules({concept.id}, fresh_ek._registry)

    assert _findings_for_rule(findings, "duplicate_candidate") == []


def test_promotion_signal_note_to_dossier(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="升格信号来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="升格信号笔记"))
    _set_metrics(fresh_ek, note.id, compression_ratio=3, action_relevance=1)

    findings = check_local_rules({note.id}, fresh_ek._registry)

    promotion_findings = _findings_for_rule(findings, "promotion_signal")
    assert len(promotion_findings) == 1
    assert "note→dossier" in promotion_findings[0].message


def test_promotion_signal_concept_to_decision_no_evidence(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="无证据信号来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="无证据信号概念"))
    _patch_frontmatter_field(fresh_ek, concept.id, "evidence_refs", [])
    _set_metrics(fresh_ek, concept.id, action_relevance=5)

    findings = check_local_rules({concept.id}, fresh_ek._registry)

    assert _findings_for_rule(findings, "promotion_signal") == []


def test_missing_relation_hint_via_tag(fresh_ek) -> None:
    source_a = fresh_ek.ingest(
        "source", source_payload(title="关系提示来源A", tags=["source-only"])
    )
    source_b = fresh_ek.ingest(
        "source", source_payload(title="关系提示来源B", tags=["source-only"])
    )
    source_c = fresh_ek.ingest(
        "source", source_payload(title="关系提示来源C", tags=["source-only"])
    )
    touched = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_a.id,
            title="关系提示主体",
            tags=[_LOCAL_RULES_TAG],
            search_terms=_relation_hint_terms("关系提示主体", "hint-touched"),
        ),
    )
    first = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_b.id,
            title="火星轨道候选",
            tags=[_LOCAL_RULES_TAG],
            search_terms=_relation_hint_terms("火星轨道候选", "hint-first"),
        ),
    )
    second = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_c.id,
            title="蓝鲸迁徙候选",
            tags=[_LOCAL_RULES_TAG],
            search_terms=_relation_hint_terms("蓝鲸迁徙候选", "hint-second"),
        ),
    )

    findings = check_local_rules({touched.id}, fresh_ek._registry)

    hint_findings = _findings_for_rule(findings, "missing_relation_hint")
    assert len(hint_findings) == 1
    assert first.id in hint_findings[0].message
    assert second.id in hint_findings[0].message


def test_missing_relation_hint_skip_existing(fresh_ek) -> None:
    source_a = fresh_ek.ingest(
        "source", source_payload(title="关系过滤来源A", tags=["source-only"])
    )
    source_b = fresh_ek.ingest(
        "source", source_payload(title="关系过滤来源B", tags=["source-only"])
    )
    source_c = fresh_ek.ingest(
        "source", source_payload(title="关系过滤来源C", tags=["source-only"])
    )
    touched = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_a.id,
            title="关系过滤主体",
            tags=[_EXISTING_RELATION_TAG],
            search_terms=_relation_hint_terms("关系过滤主体", "skip-touched"),
        ),
    )
    first = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_b.id,
            title="晨光索引节点",
            tags=[_EXISTING_RELATION_TAG],
            search_terms=_relation_hint_terms("晨光索引节点", "skip-first"),
        ),
    )
    second = fresh_ek.ingest(
        "concept",
        concept_payload(
            source_c.id,
            title="海盐归档节点",
            tags=[_EXISTING_RELATION_TAG],
            search_terms=_relation_hint_terms("海盐归档节点", "skip-second"),
        ),
    )
    fresh_ek.link(touched.id, first.id, "related")
    fresh_ek.link(touched.id, second.id, "related")

    findings = check_local_rules({touched.id}, fresh_ek._registry)

    assert _findings_for_rule(findings, "missing_relation_hint") == []


def test_check_local_rules_empty_touched(fresh_ek) -> None:
    assert check_local_rules(set(), fresh_ek._registry) == []


def test_check_local_rules_skip_deleted(fresh_ek) -> None:
    assert check_local_rules({"ek_con_00000000000000000000000000"}, fresh_ek._registry) == []
