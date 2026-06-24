from __future__ import annotations

import pytest

from ego_knowledge._validation import collect_conflict_candidates
from ego_knowledge.errors import ConflictError, ValidationError
from ego_knowledge.models import Kind

from .support import concept_payload, note_payload, source_payload


@pytest.mark.parametrize("policy", ["allow", "merge_suggest"])
def test_conflict_policies_allow_duplicate_candidates(fresh_ek, policy: str) -> None:
    source = fresh_ek.ingest("source", source_payload(title="冲突来源"))
    original = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="单一真源", aliases=["SSOT"]),
    )

    # 不同 title → 不同 slug，绕过 slug 硬门禁；但保持同 aliases 以测试 alias 冲突检测层
    duplicated = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="单一真源-复制", aliases=["SSOT"]),
        conflict_policy=policy,
    )

    assert duplicated.id != original.id
    assert duplicated.slug.startswith("单一真源-复制")


def test_check_conflicts_rejects_invalid_policy(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="不支持的 conflict_policy"):
        fresh_ek._check_conflicts(
            Kind.CONCEPT,
            {"title": "单一真源"},
            conflict_policy="skip",
            ignore_ids=set(),
        )


def test_check_conflicts_requires_string_title(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="title 缺失"):
        fresh_ek._check_conflicts(
            Kind.CONCEPT,
            {"title": 42},
            conflict_policy="strict",
            ignore_ids=set(),
        )


# ---------------------------------------------------------------------------
# Phase 1 · 策略矩阵细化测试
# ---------------------------------------------------------------------------


def test_conflict_strict_raises_conflict_error_with_candidates(fresh_ek) -> None:
    """strict 模式下同 kind 近似 title/alias 交集抛 ConflictError，带 candidates。"""
    source = fresh_ek.ingest("source", source_payload(title="冲突来源S"))
    original = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="知识抽取", aliases=["KE"]),
    )

    with pytest.raises(ConflictError, match="冲突") as exc_info:
        fresh_ek.ingest(
            "concept",
            concept_payload(source.id, title="知识抽取", aliases=["KE"]),
            conflict_policy="strict",
        )

    details = exc_info.value.details
    assert "candidates" in details
    candidates = details["candidates"]
    assert len(candidates) >= 1
    first = candidates[0] if isinstance(candidates[0], dict) else candidates[0]
    assert "id" in first
    assert "title" in first
    assert first["id"] == original.id


def test_conflict_merge_suggest_finds_candidates_without_error(fresh_ek) -> None:
    """merge_suggest 发现候选但不抛错，允许调用方继续落盘。"""
    source = fresh_ek.ingest("source", source_payload(title="合并来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="合并概念", aliases=["merge-con"]),
    )

    # 不同 title → 不同 slug，绕过 slug 硬门禁；保持同 aliases 以测试 alias 冲突检测层
    result = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="合并概念-merge", aliases=["merge-con"]),
        conflict_policy="merge_suggest",
    )

    assert result is not None
    assert result.kind == Kind.CONCEPT


def test_conflict_allow_skips_detection_entirely(fresh_ek) -> None:
    """allow 模式直接跳过冲突检测，同名别名条目可写入。"""
    source = fresh_ek.ingest("source", source_payload(title="跳过检测来源"))
    fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="跳过概念", aliases=["skip-alias"]),
    )

    # 不同 title → 不同 slug，绕过 slug 硬门禁；allow 跳过 check_conflicts 层
    result = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="跳过概念-allow", aliases=["skip-alias"]),
        conflict_policy="allow",
    )

    assert result is not None


def test_conflict_cross_kind_title_no_false_positive(fresh_ek) -> None:
    """跨 kind 同 title 不被 all_titles_for_kind 误判——title 近似检查是 kind 内的。"""
    source = fresh_ek.ingest("source", source_payload(title="跨类来源"))
    # 先建一个 note
    fresh_ek.ingest(
        "note",
        note_payload(source.id, title="跨类概念", aliases=["cross-kind-unique"]),
    )
    # 再建同 title 的 concept——不同 kind，title 不应触发 strict 冲突
    result = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="跨类概念", aliases=["cross-kind-diff"]),
        conflict_policy="strict",
    )

    assert result is not None
    assert result.kind == Kind.CONCEPT


def test_conflict_cross_kind_alias_overlap_detected(fresh_ek) -> None:
    """跨 kind alias 交集仍被全局 find_by_aliases 发现。"""
    source = fresh_ek.ingest("source", source_payload(title="跨类别名来源"))
    fresh_ek.ingest(
        "note",
        note_payload(source.id, title="跨类笔记", aliases=["shared-cross-alias"]),
    )

    with pytest.raises(ConflictError, match="冲突"):
        fresh_ek.ingest(
            "concept",
            concept_payload(source.id, title="跨类概念", aliases=["shared-cross-alias"]),
            conflict_policy="strict",
        )


def test_collect_conflict_candidates_returns_candidate_ids(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="候选来源"))
    original = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="候选概念", aliases=["candidate-alias"]),
    )
    duplicate = fresh_ek.ingest(
        "concept",
        concept_payload(source.id, title="候选概念副本", aliases=["candidate-alias"]),
        conflict_policy="allow",
    )

    candidates = collect_conflict_candidates(
        fresh_ek._registry,
        Kind.CONCEPT,
        {
            "title": duplicate.title,
            "aliases": duplicate.aliases,
        },
        ignore_ids={duplicate.id},
    )

    assert original.id in candidates
    assert duplicate.id not in candidates
