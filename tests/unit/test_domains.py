from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge.errors import ConflictError, StorageError, ValidationError

from .support import (
    absolute_entry_path,
    concept_payload,
    overwrite_body,
    relative_link,
    source_payload,
)


def test_domains_add_and_list_roundtrip(fresh_ek) -> None:
    fresh_ek.domains_add("知识管理")

    domains = fresh_ek.domains_list()

    assert any(domain["name"] == "知识管理" for domain in domains)
    with pytest.raises(ConflictError):
        fresh_ek.domains_add("知识管理")


def test_domains_migrate_rewrites_links(fresh_ek, ek_root: Path) -> None:
    fresh_ek.domains_add("新领域")
    source = fresh_ek.ingest("source", source_payload(title="迁移来源"))
    target = fresh_ek.ingest("concept", concept_payload(source.id, title="待迁移概念"))
    referrer = fresh_ek.ingest("concept", concept_payload(source.id, title="迁移引用者"))

    referrer_path = absolute_entry_path(ek_root, referrer.file_path or "")
    target_path = absolute_entry_path(ek_root, target.file_path or "")
    overwrite_body(
        fresh_ek,
        ek_root,
        referrer.file_path or "",
        f"[待迁移]({relative_link(referrer_path, target_path)})\n",
    )

    result = fresh_ek.domains_migrate([target.id], target_domain="新领域")
    migrated = fresh_ek.get(target.id)
    migrated_path = absolute_entry_path(ek_root, migrated.file_path or "")
    body = referrer_path.read_text(encoding="utf-8")

    assert result.target_domain == "新领域"
    assert "entries/concepts/新领域" in migrated.file_path
    assert relative_link(referrer_path, migrated_path) in body


def test_domains_migrate_rolls_back_on_failure(
    fresh_ek,
    ek_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh_ek.domains_add("回滚领域")
    source = fresh_ek.ingest("source", source_payload(title="回滚来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="回滚概念"))
    old_path = absolute_entry_path(ek_root, concept.file_path or "")

    def raise_rename(src: Path, dst: Path) -> None:
        del src, dst
        raise OSError("rename failed")

    monkeypatch.setattr("ego_knowledge.core.os.rename", raise_rename)

    with pytest.raises(StorageError, match="批量文件操作失败"):
        fresh_ek.domains_migrate([concept.id], target_domain="回滚领域")

    assert old_path.exists()
    assert fresh_ek.get(concept.id).file_path == concept.file_path


def test_domains_migrate_requires_existing_domain(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="目标 domain 不存在"):
        fresh_ek.domains_migrate(["ek_con_missing"], target_domain="不存在")
