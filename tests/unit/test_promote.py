from __future__ import annotations

import datetime as dt
import json

import pytest

from ego_knowledge.errors import ValidationError
from ego_knowledge.models import RelationType, Status

from .support import concept_payload, dossier_payload, note_payload, source_payload


def _patch_frontmatter_field(ek: object, entry_id: str, field: str, value: object) -> None:
    """直接修改数据库 frontmatter_json，绕过 schema 校验。"""
    registry = ek._registry  # type: ignore[union-attr]
    row = registry.conn.execute(
        "SELECT frontmatter_json FROM entries WHERE id = ?", (entry_id,)
    ).fetchone()
    fm = json.loads(row["frontmatter_json"])
    fm[field] = value
    registry.conn.execute(
        "UPDATE entries SET frontmatter_json = ? WHERE id = ?",
        (json.dumps(fm, ensure_ascii=False), entry_id),
    )
    registry.commit()


# ---------------------------------------------------------------------------
# note → dossier
# ---------------------------------------------------------------------------


def test_promote_note_to_dossier_computes_review_due_at(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="升格来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="待升格笔记"))

    dossier = fresh_ek.promote(note.id, target_kind="dossier", freshness="watch")

    assert dossier.kind == "dossier"
    assert dossier.evidence_refs == [source.id]
    assert dossier.reviewed_at == dt.date.today()
    assert dossier.review_due_at == dt.date.today() + dt.timedelta(days=30)
    assert dossier.relations[0].type == RelationType.DERIVED_FROM
    assert fresh_ek.get(note.id).status == Status.ACTIVE


# ---------------------------------------------------------------------------
# 非法路径
# ---------------------------------------------------------------------------


def test_promote_invalid_path_source_to_concept(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="不能升格"))

    with pytest.raises(ValidationError, match="不允许的升格路径"):
        fresh_ek.promote(source.id, target_kind="concept")


# ---------------------------------------------------------------------------
# concept → decision
# ---------------------------------------------------------------------------


def test_promote_concept_to_decision_links_back(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="决策来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="决策概念"))

    decision = fresh_ek.promote(concept.id, target_kind="decision")
    updated_concept = fresh_ek.get(concept.id)

    assert decision.kind == "decision"
    assert decision.evidence_refs == [source.id]
    assert decision.decided_at == dt.date.today()
    assert decision.decision_status == "active"
    assert any(
        rel.target == decision.id and rel.type == RelationType.APPLIED_IN
        for rel in updated_concept.relations
    )
    # 旧 concept 保持 active
    assert updated_concept.status == Status.ACTIVE


def test_promote_concept_to_decision_fails_with_bad_status(fresh_ek) -> None:
    """concept.status 非 active/authoritative 时拒绝升格。"""
    source = fresh_ek.ingest("source", source_payload(title="状态来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="状态概念"))
    # 手动改为 legacy
    fresh_ek.update(concept.id, {"status": "legacy"})

    with pytest.raises(ValidationError, match="active/authoritative"):
        fresh_ek.promote(concept.id, target_kind="decision")


def test_promote_concept_to_decision_fails_without_evidence_refs(fresh_ek) -> None:
    """治理红线 9：concept 无 evidence_refs 时不能升格为 decision。"""
    source = fresh_ek.ingest("source", source_payload(title="无证据来源"))
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="无证据概念",
            evidence_status="weak",
        ),
    )
    # 直接修改 DB 将 evidence_refs 设为空（绕过 schema 校验）
    _patch_frontmatter_field(fresh_ek, concept.id, "evidence_refs", [])

    with pytest.raises(ValidationError, match="evidence_refs"):
        fresh_ek.promote(concept.id, target_kind="decision")


# ---------------------------------------------------------------------------
# dossier → concept
# ---------------------------------------------------------------------------


def test_promote_dossier_to_concept_happy_path(fresh_ek) -> None:
    """happy path：evidence_refs 保留、evidence_status 计算、dossier 变 legacy、supersedes。"""
    source = fresh_ek.ingest("source", source_payload(title="档案升格来源"))
    source2 = fresh_ek.ingest("source", source_payload(title="辅助证据来源"))
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="待升格档案",
            evidence_refs=[source.id, source2.id],
            domain="测试",
        ),
    )

    concept = fresh_ek.promote(dossier.id, target_kind="concept")
    updated_dossier = fresh_ek.get(dossier.id)

    assert concept.kind == "concept"
    assert concept.evidence_refs == [source.id, source2.id]
    assert concept.evidence_status == "solid"  # 2 unique refs → solid
    assert concept.freshness == "stable"
    # 原 dossier 变 legacy
    assert updated_dossier.status == Status.LEGACY
    # 新 concept 通过 supersedes 指向原 dossier
    assert any(
        rel.target == dossier.id and rel.type == RelationType.SUPERSEDES
        for rel in concept.relations
    )


def test_promote_dossier_to_concept_fails_without_reviewed_at(fresh_ek) -> None:
    """dossier reviewed_at 为 None 时拒绝升格。"""
    source = fresh_ek.ingest("source", source_payload(title="无review来源"))
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(source.id, title="无review档案"),
    )
    # 直接修改 DB 将 reviewed_at 设为 None（绕过 schema 校验）
    _patch_frontmatter_field(fresh_ek, dossier.id, "reviewed_at", None)

    with pytest.raises(ValidationError, match="reviewed_at"):
        fresh_ek.promote(dossier.id, target_kind="concept")


def test_promote_dossier_to_concept_fails_with_stale_reviewed_at(fresh_ek) -> None:
    """dossier reviewed_at 超过 30 天时拒绝升格。"""
    source = fresh_ek.ingest("source", source_payload(title="陈旧review来源"))
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(source.id, title="陈旧review档案"),
    )
    # 将 reviewed_at 设为 31 天前
    stale_date = (dt.date.today() - dt.timedelta(days=31)).isoformat()
    fresh_ek.update(dossier.id, {"reviewed_at": stale_date})

    with pytest.raises(ValidationError, match="30 天"):
        fresh_ek.promote(dossier.id, target_kind="concept")


def test_promote_dossier_to_concept_fails_with_empty_evidence_refs(fresh_ek) -> None:
    """dossier 无 evidence_refs（指标不足）时拒绝升格。"""
    source = fresh_ek.ingest("source", source_payload(title="空证据来源"))
    dossier = fresh_ek.ingest(
        "dossier",
        dossier_payload(
            source.id,
            title="空证据档案",
        ),
    )
    # 直接修改 DB 将 evidence_refs 设为空（绕过 schema 校验）
    _patch_frontmatter_field(fresh_ek, dossier.id, "evidence_refs", [])

    with pytest.raises(ValidationError, match="evidence_refs"):
        fresh_ek.promote(dossier.id, target_kind="concept")


# ---------------------------------------------------------------------------
# Phase 2: promote skip_body_floor 豁免
# ---------------------------------------------------------------------------


def test_promote_creates_entry_with_empty_body_succeeds(fresh_ek) -> None:
    """promote 创建空 body 条目应成功（skip_body_floor=True 豁免）。"""
    source = fresh_ek.ingest("source", source_payload(title="豁免来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="豁免笔记"))

    # promote note → concept：created entry body=""，但 skip_body_floor=True
    concept = fresh_ek.promote(note.id, target_kind="concept")

    assert concept.kind == "concept"
    assert concept.body == ""


def test_promote_writes_exemption_log(fresh_ek, ek_root) -> None:
    """promote 时 _log_promote_body_exemption 应写入 JSONL 日志。"""
    import json

    source = fresh_ek.ingest("source", source_payload(title="日志来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="日志笔记"))

    fresh_ek.promote(note.id, target_kind="concept")

    log_file = ek_root / "logs" / "refresh" / "promote-body-exemption.jsonl"
    assert log_file.exists(), "promote body 豁免日志文件应存在"
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    record = json.loads(lines[-1])
    assert record["reason"] == "promote_body_floor_exempt"
    assert "entry_id" in record
