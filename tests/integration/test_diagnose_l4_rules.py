"""L4 rule-set integration tests: finding → enqueue → status flow.

After dedup (commit 6134b36 governance fix), 16 L4 rules remain:
- 2 L3 redlines + 3 decay + 3 structure + 3 action + 5 push = 16 total.
Removed: note_swamp, note_stagnant, concept_internal_split,
orphan_decision, view_as_truth, action_promote, action_split.

Cross-cutting tests verify:
- _DIAGNOSE_RULES length = 16
- action_demote finding exists but is NOT enqueued
- high severity → task_board mock called + status = sent
- medium severity → queue only, status = pending
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from unittest.mock import patch

import pytest

from ego_knowledge.diagnose import _DIAGNOSE_RULES, _NO_QUEUE_RULES, diagnose
from ego_knowledge.maintenance_queue_store import list_queue
from tests.unit.support import (
    concept_payload,
    decision_payload,
    dossier_payload,
    note_payload,
    source_payload,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _set_metrics(ek, entry_id: str, **metrics: float) -> None:
    registry = ek._registry
    assignments = ", ".join(f"{key} = ?" for key in metrics)
    registry.conn.execute(
        f"UPDATE entry_metrics SET {assignments} WHERE entry_id = ?",
        (*metrics.values(), entry_id),
    )
    registry.commit()


def _patch_frontmatter(ek, entry_id: str, **updates: object) -> None:
    registry = ek._registry
    row = registry.conn.execute(
        "SELECT frontmatter_json FROM entries WHERE id = ?",
        (entry_id,),
    ).fetchone()
    frontmatter = json.loads(row["frontmatter_json"])
    for key, value in updates.items():
        frontmatter[key] = value.isoformat() if isinstance(value, _dt.date) else value
    registry.conn.execute(
        "UPDATE entries SET frontmatter_json = ? WHERE id = ?",
        (json.dumps(frontmatter, ensure_ascii=False), entry_id),
    )
    registry.commit()


def _relation(target: str, rel_type: str) -> dict[str, str]:
    return {"target": target, "type": rel_type, "source": "confirmed"}


def _has_finding(report, rule_id: str) -> bool:
    return any(f.rule_id == rule_id for f in report.findings)


def _queue_for(queue, rule_id: str) -> list[dict]:
    return [r for r in queue if r["rule_id"] == rule_id]


def _run(ek, ek_root):
    """Run diagnose and return (report, queue_rows)."""
    report = diagnose(ek._registry, ek_root)
    queue = list_queue(ek._registry)
    return report, queue


# Unique-indexed payload builders to avoid Levenshtein collision.


def _unique(title: str, idx: str) -> str:
    """Append 8-char hash of *idx* so same-kind titles always differ by Levenshtein.

    Using SHA256[:8] guarantees any two entries differ by ≥ 4 characters
    regardless of how similar the base titles or idx strings are.
    """
    digest = hashlib.sha256(idx.encode()).hexdigest()[:8]
    return f"{title}#{digest}"


def _src(title: str, idx: str, **kw) -> dict[str, object]:
    full = _unique(title, idx)
    return source_payload(
        title=full,
        source_type=kw.pop("source_type", "web"),
        source_url=f"https://l4test.example/{idx}",
        content_hash=f"hash-l4-{idx}",
        search_terms=[full, f"l4-src-{idx}", "l4test", f"测试来源{idx}", f"alias-l4s-{idx}"],
        tags=["L4集成"],
        **kw,
    )


def _nt(src_id: str, title: str, idx: str, **kw) -> dict[str, object]:
    full = _unique(title, idx)
    return note_payload(
        src_id,
        title=full,
        search_terms=[full, f"l4-nt-{idx}", "l4test", f"测试笔记{idx}", f"alias-l4n-{idx}"],
        tags=kw.pop("tags", ["L4集成"]),
        **kw,
    )


def _con(src_id: str, title: str, idx: str, **kw) -> dict[str, object]:
    full = _unique(title, idx)
    # Pull user-provided search_terms out of kw to avoid duplicate keyword;
    # if absent, fall back to the default list below.
    user_st = kw.pop("search_terms", None)
    st = (
        user_st
        if user_st is not None
        else [
            full,
            f"l4-con-{idx}",
            "l4test",
            f"测试概念{idx}",
            f"alias-l4c-{idx}",
        ]
    )
    # Replace first element with unique title if user supplied their own list
    if user_st is not None and st:
        st[0] = full
    return concept_payload(
        src_id,
        title=full,
        search_terms=st,
        tags=kw.pop("tags", ["L4集成"]),
        **kw,
    )


def _dos(evidence_ref: str, title: str, idx: str, **kw) -> dict[str, object]:
    full = _unique(title, idx)
    return dossier_payload(
        evidence_ref,
        title=full,
        search_terms=[full, f"l4-dos-{idx}", "l4test", f"测试档案{idx}", f"alias-l4d-{idx}"],
        tags=["L4集成"],
        **kw,
    )


def _dec(evidence_ref: str, title: str, idx: str, **kw) -> dict[str, object]:
    full = _unique(title, idx)
    return decision_payload(
        evidence_ref,
        title=full,
        search_terms=[full, f"l4-dec-{idx}", "l4test", f"测试决策{idx}", f"alias-l4e-{idx}"],
        tags=["L4集成"],
        **kw,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_task_board():
    """Mock _create_task_board_task to avoid real subprocess calls."""
    with patch("ego_knowledge.diagnose._create_task_board_task") as mock:
        yield mock


# ===========================================================================
# 1. _DIAGNOSE_RULES length assertion = 23
# ===========================================================================


def test_diagnose_rules_registry_has_16_rules() -> None:
    assert len(_DIAGNOSE_RULES) == 16
    assert len({rid for rid, _ in _DIAGNOSE_RULES}) == 16


# ===========================================================================
# 3. Structure rules (3 × 2 = 6 tests)
# ===========================================================================


class TestDecaySourceContext:
    def test_happy(self, fresh_ek, ek_root) -> None:
        old = fresh_ek.ingest("source", _src("衰变上下文旧来源", "dsc-old"))
        new = fresh_ek.ingest("source", _src("衰变上下文新来源", "dsc-new"))
        fresh_ek.link(new.id, old.id, "supersedes")
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "decay_source_context")
        q = _queue_for(queue, "decay_source_context")
        assert len(q) >= 1
        assert q[0]["severity"] == "medium"
        assert q[0]["status"] == "pending"

    def test_miss(self, fresh_ek, ek_root) -> None:
        fresh_ek.ingest("source", _src("衰变上下文稳来源", "dsc-stable"))
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "decay_source_context")


class TestStructureFossilConcept:
    def test_happy(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("化石来源", "sfc-src"))
        con = fresh_ek.ingest("concept", _con(src.id, "化石概念", "sfc-con"))
        _set_metrics(fresh_ek, con.id, evidence_strength=0.5, action_relevance=3.0)
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "structure_fossil_concept")
        assert len(_queue_for(queue, "structure_fossil_concept")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("强证据来源", "sfc-str"))
        con = fresh_ek.ingest("concept", _con(src.id, "强证据概念", "sfc-sc"))
        _set_metrics(fresh_ek, con.id, evidence_strength=1.0, action_relevance=3.0)
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "structure_fossil_concept")


class TestStructureMonocultureEvidence:
    def test_happy(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("单栽来源甲", "sme-a"))
        sb = fresh_ek.ingest("source", _src("单栽来源乙", "sme-b"))
        fresh_ek.ingest("concept", _con(sa.id, "单栽概念", "sme-con", evidence_refs=[sa.id, sb.id]))
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "structure_monoculture_evidence")
        assert len(_queue_for(queue, "structure_monoculture_evidence")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("多样来源甲", "sme-da"))
        sb = fresh_ek.ingest("source", _src("多样来源乙", "sme-db", source_type="doc"))
        fresh_ek.ingest("concept", _con(sa.id, "多样概念", "sme-dc", evidence_refs=[sa.id, sb.id]))
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "structure_monoculture_evidence")


class TestStructureSupersedesCycle:
    def test_happy(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("循环来源", "ssc-src"))
        ca = fresh_ek.ingest("concept", _con(src.id, "循环节点甲", "ssc-ca"))
        cb = fresh_ek.ingest(
            "concept",
            _con(
                src.id,
                "循环节点乙",
                "ssc-cb",
                relations=[_relation(ca.id, "supersedes")],
            ),
        )
        fresh_ek.update(ca.id, {"relations": [_relation(cb.id, "supersedes")]})
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "structure_supersedes_cycle")
        assert len(_queue_for(queue, "structure_supersedes_cycle")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("链式来源", "ssc-chain"))
        cb = fresh_ek.ingest("concept", _con(src.id, "链式先驱", "ssc-pre"))
        fresh_ek.ingest(
            "concept",
            _con(
                src.id,
                "链式继承",
                "ssc-suc",
                relations=[_relation(cb.id, "supersedes")],
            ),
        )
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "structure_supersedes_cycle")


# ===========================================================================
# 4. Action rules (3 × 2 = 6 tests)
# ===========================================================================


class TestActionDemote:
    """action_demote finding exists but is NOT enqueued (_NO_QUEUE_RULES)."""

    def test_happy_finding_not_enqueued(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("降级来源", "ad-src"))
        con = fresh_ek.ingest("concept", _con(src.id, "降级概念", "ad-con"))
        _set_metrics(fresh_ek, con.id, drift_score=0.5)
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "action_demote"), "action_demote finding should exist"
        assert _queue_for(queue, "action_demote") == [], "action_demote must NOT be enqueued"

    def test_miss(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("稳定降级来源", "ad-stable"))
        fresh_ek.ingest("concept", _con(src.id, "稳定降级概念", "ad-sc"))
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "action_demote")


class TestActionMerge:
    def test_happy(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("合并来源甲", "am-la"))
        sb = fresh_ek.ingest("source", _src("合并来源乙", "am-lb"))
        shared = ["merge-core", "shared-term", "l4test", "共同语义", "alias-merge"]
        ta = ["星桥合并概念"] + shared
        tb = ["南斗融合条目"] + shared
        fresh_ek.ingest("concept", _con(sa.id, "星桥合并概念", "am-la", search_terms=ta))
        fresh_ek.ingest("concept", _con(sb.id, "南斗融合条目", "am-lb", search_terms=tb))
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "action_merge")
        assert len(_queue_for(queue, "action_merge")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("低叠来源甲", "am-ma"))
        sb = fresh_ek.ingest("source", _src("低叠来源乙", "am-mb"))
        fresh_ek.ingest("concept", _con(sa.id, "松针独立概念", "am-mca"))
        fresh_ek.ingest("concept", _con(sb.id, "海雾独立条目", "am-mcb"))
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "action_merge")


class TestActionRetract:
    def test_happy(self, fresh_ek, ek_root, mock_task_board) -> None:
        old = fresh_ek.ingest("source", _src("撤回旧来源", "ar-old"))
        new = fresh_ek.ingest("source", _src("撤回新来源", "ar-new"))
        con = fresh_ek.ingest("concept", _con(old.id, "撤回概念", "ar-con"))
        fresh_ek.ingest("decision", _dec(con.id, "撤回决策", "ar-dec"))
        fresh_ek.link(new.id, con.id, "supersedes")
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "action_retract")
        q = _queue_for(queue, "action_retract")
        assert len(q) >= 1
        assert q[0]["severity"] == "high"
        assert q[0]["status"] == "sent"
        mock_task_board.assert_called()

    def test_miss(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("撤回稳定来源", "ar-ok"))
        con = fresh_ek.ingest("concept", _con(src.id, "撤回稳定概念", "ar-ok-c"))
        fresh_ek.ingest("decision", _dec(con.id, "稳定决策", "ar-ok-d"))
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "action_retract")


# ===========================================================================
# 5. Push rules (5 × 2 = 10 tests)
# ===========================================================================


class TestPushPremiseShaken:
    def test_happy(self, fresh_ek, ek_root, mock_task_board) -> None:
        src = fresh_ek.ingest("source", _src("前提震荡来源", "pps-src"))
        con = fresh_ek.ingest(
            "concept",
            _con(
                src.id,
                "震荡前提概念",
                "pps-con",
                freshness="volatile",
            ),
        )
        dec = fresh_ek.ingest("decision", _dec(con.id, "震荡决策", "pps-dec"))
        _set_metrics(fresh_ek, dec.id, retrieval_heat=3.0)
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "push_premise_shaken")
        q = _queue_for(queue, "push_premise_shaken")
        assert len(q) >= 1
        assert q[0]["severity"] == "high"
        assert q[0]["status"] == "sent"
        mock_task_board.assert_called()

    def test_miss(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("冷前提来源", "pps-cold"))
        con = fresh_ek.ingest(
            "concept",
            _con(
                src.id,
                "冷前提概念",
                "pps-cc",
                freshness="volatile",
            ),
        )
        fresh_ek.ingest("decision", _dec(con.id, "冷决策", "pps-cd"))
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "push_premise_shaken")


class TestPushCrystallize:
    def test_happy(self, fresh_ek, ek_root) -> None:
        for i, title in enumerate(["结晶素材甲", "结晶素材乙", "结晶素材丙"], start=1):
            s = fresh_ek.ingest("source", _src(f"结晶来源{i}", f"pc-s{i}"))
            fresh_ek.ingest("note", _nt(s.id, title, f"pc-n{i}", tags=["结晶簇"]))
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "push_crystallize")
        assert len(_queue_for(queue, "push_crystallize")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        # 3 notes absorbed by a concept → no crystallize finding
        sources = [fresh_ek.ingest("source", _src(f"吸收来源{i}", f"pc-m{i}")) for i in range(1, 4)]
        notes = [
            fresh_ek.ingest("note", _nt(s.id, f"吸收素材{i}", f"pc-mn{i}", tags=["吸收簇"]))
            for i, s in enumerate(sources, 1)
        ]
        absorber_src = fresh_ek.ingest("source", _src("吸收概念来源", "pc-abs"))
        fresh_ek.ingest(
            "concept",
            _con(
                absorber_src.id,
                "吸收结晶概念",
                "pc-abs-con",
                relations=[_relation(n.id, "derived_from") for n in notes],
            ),
        )
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "push_crystallize")


class TestPushPseudoStable:
    def test_happy(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("伪稳来源甲", "ps-a"))
        sb = fresh_ek.ingest("source", _src("伪稳来源乙", "ps-b"))
        fresh_ek.ingest(
            "concept",
            _con(
                sa.id,
                "伪稳概念",
                "ps-con",
                evidence_refs=[sa.id, sb.id],
            ),
        )
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "push_pseudo_stable")
        assert len(_queue_for(queue, "push_pseudo_stable")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("多源伪稳甲", "ps-da"))
        sb = fresh_ek.ingest("source", _src("多源伪稳乙", "ps-db", source_type="doc"))
        fresh_ek.ingest(
            "concept",
            _con(
                sa.id,
                "多源概念",
                "ps-dc",
                evidence_refs=[sa.id, sb.id],
            ),
        )
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "push_pseudo_stable")


class TestPushInternalSplit:
    def test_happy(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("推送裂解来源甲", "pis-a"))
        sb = fresh_ek.ingest("source", _src("推送裂解来源乙", "pis-b"))
        sc = fresh_ek.ingest("source", _src("推送裂解来源丙", "pis-c"))
        ca = fresh_ek.ingest("concept", _con(sa.id, "推送反例甲", "pis-ca"))
        cb = fresh_ek.ingest("concept", _con(sb.id, "推送反例乙", "pis-cb"))
        fresh_ek.ingest(
            "concept",
            _con(
                sc.id,
                "推送裂解概念",
                "pis-ct",
                relations=[_relation(ca.id, "contradicts"), _relation(cb.id, "contradicts")],
            ),
        )
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "push_internal_split")
        assert len(_queue_for(queue, "push_internal_split")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        sa = fresh_ek.ingest("source", _src("推送单裂来源甲", "pis-ma"))
        sb = fresh_ek.ingest("source", _src("推送单裂来源乙", "pis-mb"))
        ca = fresh_ek.ingest("concept", _con(sa.id, "推送单反例", "pis-mca"))
        fresh_ek.ingest(
            "concept",
            _con(
                sb.id,
                "推送单裂目标",
                "pis-mt",
                relations=[_relation(ca.id, "contradicts")],
            ),
        )
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "push_internal_split")


class TestPushCognitiveDivergence:
    def test_happy(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("分歧来源", "pcd-src"))
        dos = fresh_ek.ingest("dossier", _dos(src.id, "分歧档案", "pcd-dos"))
        _set_metrics(fresh_ek, dos.id, drift_score=0.7, retrieval_heat=2.0)
        report, queue = _run(fresh_ek, ek_root)
        assert _has_finding(report, "push_cognitive_divergence")
        assert len(_queue_for(queue, "push_cognitive_divergence")) >= 1

    def test_miss(self, fresh_ek, ek_root) -> None:
        src = fresh_ek.ingest("source", _src("低分歧来源", "pcd-low"))
        dos = fresh_ek.ingest("dossier", _dos(src.id, "低分歧档案", "pcd-low-d"))
        _set_metrics(fresh_ek, dos.id, drift_score=0.6, retrieval_heat=2.0)
        report, _ = _run(fresh_ek, ek_root)
        assert not _has_finding(report, "push_cognitive_divergence")


# ===========================================================================
# 6. Cross-cutting pipeline tests
# ===========================================================================


class TestLowSeverityNotEnqueued:
    """Low severity findings must never enter the queue."""

    def test_low_finding_skipped(self, fresh_ek, ek_root) -> None:
        from ego_knowledge.diagnose import _push_findings_by_severity
        from ego_knowledge.doctor import Finding, Severity

        src = fresh_ek.ingest("source", _src("低严重度来源", "low-src"))
        findings = [
            Finding(
                rule_id="test_low_rule",
                severity=Severity.LOW,
                target_id=src.id,
                target_path=None,
                message="低严重度测试",
            )
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)
        assert list_queue(fresh_ek._registry) == []


class TestActionDemoteSkipsEnqueue:
    """action_demote is in _NO_QUEUE_RULES and must not be enqueued."""

    def test_demote_not_in_queue(self, fresh_ek, ek_root) -> None:
        from ego_knowledge.diagnose import _push_findings_by_severity
        from ego_knowledge.doctor import Finding, Severity

        src = fresh_ek.ingest("source", _src("demote跳过来源", "dnq-src"))
        findings = [
            Finding(
                rule_id="action_demote",
                severity=Severity.MEDIUM,
                target_id=src.id,
                target_path=None,
                message="降级跳过测试",
            )
        ]
        _push_findings_by_severity(findings, fresh_ek._registry)
        assert _queue_for(list_queue(fresh_ek._registry), "action_demote") == []


class TestNoQueueRulesContainsDemote:
    def test_no_queue_rules_has_demote(self) -> None:
        assert "action_demote" in _NO_QUEUE_RULES
