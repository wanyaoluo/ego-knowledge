from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge.errors import ValidationError

from .support import (
    absolute_entry_path,
    concept_payload,
    overwrite_body,
    relative_link,
    source_payload,
)


def test_rename_rewrites_relative_links(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="改名来源"))
    target = fresh_ek.ingest("concept", concept_payload(source.id, title="被引用概念"))
    referrer = fresh_ek.ingest("concept", concept_payload(source.id, title="独立索引节点"))

    referrer_path = absolute_entry_path(ek_root, referrer.file_path or "")
    target_path = absolute_entry_path(ek_root, target.file_path or "")
    overwrite_body(
        fresh_ek,
        ek_root,
        referrer.file_path or "",
        f"[目标]({relative_link(referrer_path, target_path)})\n",
    )

    renamed = fresh_ek.rename(target.id, "重命名概念")
    renamed_path = absolute_entry_path(ek_root, renamed.file_path or "")
    referrer_disk = absolute_entry_path(ek_root, referrer.file_path or "")
    body = referrer_disk.read_text(encoding="utf-8")

    assert renamed.slug == "重命名概念"
    assert renamed_path.exists()
    assert relative_link(referrer_path, renamed_path) in body


def test_rename_rejects_unstable_kind(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="不允许改名"))

    with pytest.raises(ValidationError, match="只.*rename"):
        fresh_ek.rename(source.id, "新来源")
