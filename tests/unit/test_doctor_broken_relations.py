"""Phase 1 / 1.2 — doctor integrity 断裂关系输出增强测试。

对应被测模块：``ego_knowledge.doctor._checks.integrity._check_broken_relations``。

从 ``test_doctor_checks.py`` 拆出，聚焦 spec 决策 3 引入的输出契约：
 - relations.target_id 不存在 → 报 HIGH finding
 - 每条 finding message 含 source/target/type/origin 四字段（结构化 key=value）
 - Finding.target_id 指向 source（存在的发起方），便于 2.3 追溯
 - 三 origin 取值（ai_suggested / ai_confirmed / confirmed）全覆盖，支撑按来源分级
 - 无断裂关系 → 空列表
"""

from __future__ import annotations

from pathlib import Path

from ego_knowledge.doctor import Severity, _check_broken_relations

from ._doctor_helpers import insert_broken_relation
from .support import concept_payload, source_payload


def test_broken_relations_detects_missing_target(fresh_ek, ek_root: Path) -> None:
    """relations.target_id 不存在于 entries → 应报告 broken_relations。"""
    source = fresh_ek.ingest("source", source_payload(title="断裂关系发起方"))

    insert_broken_relation(
        fresh_ek, source.id, "ek_MISSING_TARGET_001", "evidence_refs", "ai_suggested"
    )

    findings = _check_broken_relations(fresh_ek._registry, ek_root)

    broken = [f for f in findings if f.rule_id == "broken_relations"]
    assert len(broken) == 1
    assert broken[0].severity == Severity.HIGH


def test_broken_relations_includes_source_target_origin_in_message(
    fresh_ek, ek_root: Path
) -> None:
    """每条 finding 的 message 必须含 source/target/type/origin 四字段。

    覆盖 origin 三种取值：ai_suggested / ai_confirmed / confirmed，
    支撑任务 2.3 按来源分级处理。
    """
    source_a = fresh_ek.ingest("source", source_payload(title="发起方甲"))
    source_b = fresh_ek.ingest("source", source_payload(title="发起方乙"))
    source_c = fresh_ek.ingest("source", source_payload(title="发起方丙"))

    insert_broken_relation(
        fresh_ek, source_a.id, "ek_MISSING_A", "evidence_refs", "ai_suggested"
    )
    insert_broken_relation(
        fresh_ek, source_b.id, "ek_MISSING_B", "source_refs", "ai_confirmed"
    )
    insert_broken_relation(
        fresh_ek, source_c.id, "ek_MISSING_C", "superseded_by", "confirmed"
    )

    findings = _check_broken_relations(fresh_ek._registry, ek_root)

    assert len(findings) == 3

    # 解析 message，验证 source/target/origin 三字段在所有 finding 中都存在
    parsed: list[dict[str, str]] = []
    for finding in findings:
        assert finding.rule_id == "broken_relations"
        parts = dict(
            token.split("=", 1) for token in finding.message.split() if "=" in token
        )
        assert "source" in parts, f"message 缺 source: {finding.message}"
        assert "target" in parts, f"message 缺 target: {finding.message}"
        assert "type" in parts, f"message 缺 type: {finding.message}"
        assert "origin" in parts, f"message 缺 origin: {finding.message}"
        parsed.append(parts)

    origins = {p["origin"] for p in parsed}
    assert origins == {"ai_suggested", "ai_confirmed", "confirmed"}

    targets = {p["target"] for p in parsed}
    assert targets == {"ek_MISSING_A", "ek_MISSING_B", "ek_MISSING_C"}

    sources = {p["source"] for p in parsed}
    assert sources == {source_a.id, source_b.id, source_c.id}


def test_broken_relations_target_id_carries_source_entry(fresh_ek, ek_root: Path) -> None:
    """Finding.target_id 指向 source（存在的发起方），便于追溯。

    target entry 缺失，无法作为 target_id；改用 source 让 finding 仍能
    定位到一个真实条目，下游 2.3 也能据此过滤/分组。
    """
    source = fresh_ek.ingest("source", source_payload(title="可追溯发起方"))

    insert_broken_relation(
        fresh_ek, source.id, "ek_GHOST", "evidence_refs", "ai_suggested"
    )

    findings = _check_broken_relations(fresh_ek._registry, ek_root)

    assert len(findings) == 1
    assert findings[0].target_id == source.id


def test_broken_relations_no_findings_when_clean(fresh_ek, ek_root: Path) -> None:
    """无断裂关系 → 返回空列表。"""
    source_a = fresh_ek.ingest("source", source_payload(title="干净来源甲"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source_a.id, title="干净概念"),
    )

    findings = _check_broken_relations(fresh_ek._registry, ek_root)

    assert findings == []


def test_broken_relations_empty_db_no_findings(fresh_ek, ek_root: Path) -> None:
    """空数据库无条目无关系 → 不报 finding。"""
    findings = _check_broken_relations(fresh_ek._registry, ek_root)

    assert findings == []
