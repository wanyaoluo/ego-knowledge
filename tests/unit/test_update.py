from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge.errors import ValidationError

from .support import absolute_entry_path, concept_payload, source_payload


def test_update_concept_fields_keeps_stable_slug(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="更新来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="旧概念"))
    old_path = concept.file_path

    updated = fresh_ek.update(
        concept.id,
        {
            "title": "新标题",
            "evidence_status": "solid",
            "tags": ["测试", "更新"],
        },
    )

    assert updated.title == "新标题"
    assert updated.slug == concept.slug
    assert updated.file_path == old_path
    assert absolute_entry_path(ek_root, updated.file_path or "").exists()


def test_update_source_title_moves_file(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="旧来源"))
    old_path = absolute_entry_path(ek_root, source.file_path or "")

    updated = fresh_ek.update(source.id, {"title": "新来源"})

    assert updated.file_path != source.file_path
    assert not old_path.exists()
    assert absolute_entry_path(ek_root, updated.file_path or "").exists()


def test_update_invalid_field_raises(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload())

    with pytest.raises(ValidationError, match="evidence_refs"):
        fresh_ek.update(source.id, {"evidence_refs": ["ek_src_fake"]})

    with pytest.raises(ValidationError, match="id"):
        fresh_ek.update(source.id, {"id": "ek_src_fake"})


# ---------------------------------------------------------------------------
# update 路径 source_url 白名单硬闸
# ---------------------------------------------------------------------------


def test_update_source_url_https_accepted(fresh_ek) -> None:
    """update 传入合法 https source_url 应成功。"""
    source = fresh_ek.ingest("source", source_payload())
    updated = fresh_ek.update(
        source.id,
        {"source_url": "https://arxiv.org/pdf/2402.0001"},
    )
    assert updated.source_url == "https://arxiv.org/pdf/2402.0001"


def test_update_source_url_knowledge_scheme_accepted(fresh_ek) -> None:
    """update 传入合法 knowledge:// source_url 应成功。"""
    source = fresh_ek.ingest("source", source_payload())
    updated = fresh_ek.update(
        source.id,
        {"source_url": "knowledge://papers/survey.pdf"},
    )
    assert updated.source_url == "knowledge://papers/survey.pdf"


def test_update_source_url_absolute_path_rejected(fresh_ek) -> None:
    """update 传入非法绝对路径 source_url 应抛 ValidationError。"""
    source = fresh_ek.ingest("source", source_payload())
    with pytest.raises(ValidationError, match="不在白名单内"):
        fresh_ek.update(source.id, {"source_url": "/home/user/bad.pdf"})


def test_update_source_url_relative_path_rejected(fresh_ek) -> None:
    """update 传入非法相对路径 source_url 应抛 ValidationError。"""
    source = fresh_ek.ingest("source", source_payload())
    with pytest.raises(ValidationError, match="不在白名单内"):
        fresh_ek.update(source.id, {"source_url": "tmp/x.md"})


def test_update_without_source_url_not_affected(fresh_ek) -> None:
    """update 不改 source_url 时不受硬闸影响。"""
    source = fresh_ek.ingest("source", source_payload())
    original_url = source.source_url
    updated = fresh_ek.update(source.id, {"title": "改标题不改 URL"})
    assert updated.source_url == original_url
