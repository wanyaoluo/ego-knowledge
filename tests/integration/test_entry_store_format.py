from __future__ import annotations

from pathlib import Path

import pytest

from ego_knowledge._validation import MAX_UPDATE_BODY_BYTES
from ego_knowledge.core import EgoKnowledge
from ego_knowledge.errors import BodyLengthAboveMax, ValidationError
from ego_knowledge.frontmatter import read_file
from tests.unit.support import note_payload, overwrite_body, source_payload


def _entry_path(data_root: Path, file_path: str | None) -> Path:
    assert file_path is not None
    return data_root / file_path


def _read_body(data_root: Path, file_path: str | None) -> str:
    _, body = read_file(str(_entry_path(data_root, file_path)))
    return body


def _seed_source(ek: EgoKnowledge, title: str = "格式化来源"):
    return ek.ingest(
        "source",
        source_payload(
            title=title,
            search_terms=[title, "format", "格式化来源", "entry-store-format", "alias-format"],
        ),
    )


def _body_that_formats_below_min() -> str:
    return "短" + "\n" * 60 + "尾"


def _table_that_formats_above_max() -> str:
    header = "a" * 800
    rows = "\n".join("|x|y|" for _ in range(70))
    body = f"|{header}|b|\n|---|---|\n{rows}\n"
    assert len(body.encode("utf-8")) < MAX_UPDATE_BODY_BYTES
    return body


def test_ingest_formats_body(fresh_ek: EgoKnowledge, ek_root: Path) -> None:
    messy = "#   标题\n\n\n正文   。\n"

    entry = fresh_ek.ingest(
        "source",
        source_payload(
            title="ingest 格式化",
            body=messy,
            search_terms=["ingest 格式化", "ingest-format", "格式化", "body", "alias-ingest"],
        ),
    )

    expected = "# 标题\n\n正文 。\n"
    assert entry.body == expected
    assert _read_body(ek_root, entry.file_path) == expected


def test_update_body_changed_formats(fresh_ek: EgoKnowledge, ek_root: Path) -> None:
    entry = _seed_source(fresh_ek, "update 格式化")

    updated = fresh_ek.update(entry.id, {"body": "#   新标题\n\n\n正文   。\n"})

    expected = "# 新标题\n\n正文 。\n"
    assert updated.body == expected
    assert fresh_ek.get(entry.id).body == expected
    assert _read_body(ek_root, updated.file_path) == expected


def test_frontmatter_only_update_keeps_body_bytes(
    fresh_ek: EgoKnowledge,
    ek_root: Path,
) -> None:
    entry = _seed_source(fresh_ek, "frontmatter only 格式边界")
    legacy_body = "#   旧标题\n\n\n正文   。"
    overwrite_body(fresh_ek, ek_root, entry.file_path or "", legacy_body)

    updated = fresh_ek.update(entry.id, {"status": "archived"})

    assert updated.body == legacy_body
    assert fresh_ek.get(entry.id).body == legacy_body
    assert _read_body(ek_root, updated.file_path) == legacy_body


def test_touch_keeps_body_bytes(fresh_ek: EgoKnowledge, ek_root: Path) -> None:
    entry = _seed_source(fresh_ek, "touch 格式边界")
    legacy_body = "#   旧标题\n\n\n正文   。"
    overwrite_body(fresh_ek, ek_root, entry.file_path or "", legacy_body)

    touched = fresh_ek.touch(entry.id)

    assert touched.body == legacy_body
    assert fresh_ek.get(entry.id).body == legacy_body
    assert _read_body(ek_root, touched.file_path) == legacy_body


def test_revalidates_min_length_after_format(fresh_ek: EgoKnowledge) -> None:
    source = _seed_source(fresh_ek, "格式后最小长度来源")

    with pytest.raises(ValidationError, match="body 过短"):
        fresh_ek.ingest(
            "note",
            note_payload(
                source.id,
                title="格式后过短笔记",
                body=_body_that_formats_below_min(),
                search_terms=["格式后过短笔记", "format-min", "过短", "body", "alias-min"],
            ),
        )


def test_revalidates_max_bytes_after_format(fresh_ek: EgoKnowledge) -> None:
    entry = _seed_source(fresh_ek, "格式后最大长度来源")

    with pytest.raises(BodyLengthAboveMax) as exc_info:
        fresh_ek.update(entry.id, {"body": _table_that_formats_above_max()})

    assert exc_info.value.details["maximum"] == MAX_UPDATE_BODY_BYTES
