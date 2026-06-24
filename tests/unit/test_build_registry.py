from __future__ import annotations

import datetime as _dt
import inspect
import json
import re
from datetime import date
from pathlib import Path

import pytest

from ego_knowledge import _build as build_module
from ego_knowledge.errors import StorageError
from ego_knowledge.frontmatter import write_file
from ego_knowledge.metrics import compute_retrieval_heat
from ego_knowledge.models import (
    ConceptEntry,
    Freshness,
    Kind,
    SourceEntry,
    Status,
    entry_to_frontmatter,
)
from ego_knowledge.registry import Registry, build_registry

REGISTRY_PRIVATE_CALL_PATTERN = re.compile(r"\bregistry\._[A-Za-z0-9_]+\s*\(")
TOKENIZER_IMPORT_PATTERN = re.compile(r"from \.tokenizer import rebuild_custom_dict")


def _write_entry(path: Path, entry: SourceEntry | ConceptEntry, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_file(str(path), entry_to_frontmatter(entry), body)


def test_build_registry_does_not_call_registry_private_members() -> None:
    source = inspect.getsource(build_module)

    assert REGISTRY_PRIVATE_CALL_PATTERN.search(source) is None
    assert TOKENIZER_IMPORT_PATTERN.search(source) is None


def test_build_registry_marks_entries_with_missing_relation_targets_as_failed(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    source = SourceEntry(
        id="ek_src_01HXYZ1234ABCDEFGHJKMNPQRS",
        kind=Kind.SOURCE,
        title="孤立来源",
        slug="孤立来源",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com/source",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:source",
        search_terms=["孤立来源", "isolated source", "source", "来源样例", "alias-source"],
    )
    _write_entry(data_root / "sources" / "source.md", source, "来源正文")
    broken_path = data_root / "entries" / "broken.md"
    broken_path.parent.mkdir(parents=True, exist_ok=True)
    broken_path.write_text(
        "---\nid: ek_con_01HXYZ1234ABCDEFGHJKMNPQRT\ntitle: 缺 kind 的坏文件\n---\n正文\n",
        encoding="utf-8",
    )

    stats = build_registry(data_root)

    assert stats.entries_ok == 1
    assert stats.entries_failed == 1
    assert any(
        "frontmatter 字段 kind 缺失或不是非空字符串" in message for _, message in stats.errors
    )
    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        assert registry.all_entry_ids() == [source.id]
    finally:
        registry.close()


def test_build_registry_reports_atomic_swap_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data" / "EgoKnowledge"
    source = SourceEntry(
        id="ek_src_01HXYZ1234ABCDEFGHJKMNPQRS",
        kind=Kind.SOURCE,
        title="原子替换来源",
        slug="原子替换来源",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com/swap",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:swap",
        search_terms=["原子替换来源", "atomic swap", "source", "原子来源", "alias-source"],
    )
    _write_entry(data_root / "sources" / "swap.md", source, "来源正文")

    def fail_rename(src: Path, dst: Path) -> None:
        del src, dst
        raise OSError("swap failed")

    monkeypatch.setattr("ego_knowledge._build.os.rename", fail_rename)

    with pytest.raises(StorageError, match="原子替换注册表失败"):
        build_registry(data_root)


# ---------------------------------------------------------------------------
# 2.2  _replay_access_log_if_available 真实回放
# ---------------------------------------------------------------------------


def test_replay_access_log_restores_heat_after_rebuild(tmp_path: Path) -> None:
    """jsonl → build_registry → retrieval_heat > 0."""
    data_root = tmp_path / "data" / "EgoKnowledge"
    source = SourceEntry(
        id="ek_src_01HXYZ1234ABCDEFGHJKMNPQRS",
        kind=Kind.SOURCE,
        title="回放来源",
        slug="回放来源",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com/replay",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:replay",
        search_terms=["回放来源", "replay", "source", "来源样例", "alias-source"],
    )
    _write_entry(data_root / "sources" / "replay.md", source, "来源正文")

    # 构造 jsonl access log
    access_dir = data_root / "logs" / "access"
    access_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    jsonl_path = access_dir / f"{today}.jsonl"
    now = _dt.datetime.now(tz=_dt.UTC).replace(microsecond=0).isoformat()
    record = json.dumps(
        {"entry_id": source.id, "op": "get", "accessed_at": now},
        ensure_ascii=False,
    )
    jsonl_path.write_text(record + "\n", encoding="utf-8")

    build_registry(data_root)

    registry = Registry(data_root / "registry" / "catalog.sqlite")
    try:
        heat = compute_retrieval_heat(source.id, registry)
        assert heat > 0
    finally:
        registry.close()


def test_replay_skips_nonexistent_entry(tmp_path: Path) -> None:
    """jsonl 引用不存在的 entry → 跳过不阻断重建。"""
    data_root = tmp_path / "data" / "EgoKnowledge"
    source = SourceEntry(
        id="ek_src_01HXYZ1234ABCDEFGHJKMNPQRS",
        kind=Kind.SOURCE,
        title="跳过来源",
        slug="跳过来源",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com/skip",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:skip",
        search_terms=["跳过来源", "skip", "source", "来源样例", "alias-source"],
    )
    _write_entry(data_root / "sources" / "skip.md", source, "来源正文")

    access_dir = data_root / "logs" / "access"
    access_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    jsonl_path = access_dir / f"{today}.jsonl"
    now = _dt.datetime.now(tz=_dt.UTC).replace(microsecond=0).isoformat()
    ghost_record = json.dumps(
        {"entry_id": "ek_src_GHOST000000000000000000", "op": "get", "accessed_at": now},
        ensure_ascii=False,
    )
    jsonl_path.write_text(ghost_record + "\n", encoding="utf-8")

    stats = build_registry(data_root)
    assert stats.entries_ok == 1


def test_replay_malformed_jsonl_does_not_break_build(tmp_path: Path) -> None:
    """畸形 jsonl 行不导致整个 registry 构建失败。"""
    data_root = tmp_path / "data" / "EgoKnowledge"
    source = SourceEntry(
        id="ek_src_01HXYZ1234ABCDEFGHJKMNPQRS",
        kind=Kind.SOURCE,
        title="畸形回放来源",
        slug="畸形回放来源",
        status=Status.AUTHORITATIVE,
        freshness=Freshness.WATCH,
        schema_version="1.0",
        created_at=date(2026, 4, 16),
        updated_at=date(2026, 4, 17),
        source_type="web",
        source_url="https://example.com/malformed",
        captured_at=date(2026, 4, 16),
        content_hash="sha256:malformed",
        search_terms=["畸形回放来源", "malformed", "source", "来源样例", "alias-source"],
    )
    _write_entry(data_root / "sources" / "malformed.md", source, "来源正文")

    access_dir = data_root / "logs" / "access"
    access_dir.mkdir(parents=True, exist_ok=True)
    today = _dt.date.today().isoformat()
    jsonl_path = access_dir / f"{today}.jsonl"
    jsonl_path.write_text("NOT JSON AT ALL\n", encoding="utf-8")

    stats = build_registry(data_root)
    assert stats.entries_ok == 1
