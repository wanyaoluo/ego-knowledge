from __future__ import annotations

import json
import multiprocessing
import unicodedata
from pathlib import Path

import pytest

from ego_knowledge.core import EgoKnowledge
from ego_knowledge.errors import ConflictError, StorageError
from ego_knowledge.frontmatter import read_file
from tests.unit.support import (
    absolute_entry_path,
    concept_payload,
    note_payload,
    source_payload,
)


def test_source_to_note_to_concept_promotion(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="Phase6 来源"))
    note = fresh_ek.ingest("note", note_payload(source.id, title="Phase6 笔记"))

    concept = fresh_ek.promote(note.id, target_kind="concept")
    report = fresh_ek.doctor()

    assert concept.kind.value == "concept"
    assert concept.evidence_refs == [source.id]
    assert fresh_ek.get(note.id).status.value == "legacy"
    assert not [finding for finding in report.findings if finding.severity == "high"]


def test_chinese_terminology_conflict(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="冲突来源"))
    fresh_ek.ingest("concept", concept_payload(source.id, title="单一真源"))

    with pytest.raises(ConflictError):
        fresh_ek.ingest("concept", concept_payload(source.id, title="唯一真源"))


def test_mixed_cn_en_query_uses_returned_entry(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="检索来源"))
    entry = fresh_ek.ingest(
        "concept",
        concept_payload(
            source.id,
            title="RAG 检索增强生成",
            aliases=["RAG", "检索增强"],
            search_terms=[
                "RAG 检索增强生成",
                "retrieval augmented",
                "检索增强",
                "向量检索",
                "knowledge augmentation",
            ],
            body="RAG 与向量检索结合可以提升召回质量。" + " " + "x" * 40,
        ),
    )

    results = fresh_ek.search("RAG 向量", limit=5)

    assert results
    assert results[0].id == entry.id


def test_transactional_write_rollback_on_rename_failure(
    fresh_ek,
    ek_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = fresh_ek.ingest("source", source_payload(title="回滚来源"))
    concept = fresh_ek.ingest("concept", concept_payload(source.id, title="原始概念"))
    old_path = absolute_entry_path(ek_root, concept.file_path or "")
    target_path = old_path.with_name("新概念.md")

    def fail_rename(src: Path, dst: Path) -> None:
        del src, dst
        raise OSError("rename failed")

    monkeypatch.setattr("ego_knowledge.core.os.rename", fail_rename)

    with pytest.raises(StorageError, match="批量文件操作失败"):
        fresh_ek.rename(concept.id, "新概念")

    assert old_path.exists()
    assert not target_path.exists()
    assert fresh_ek.get(concept.id).file_path == concept.file_path


def test_build_registry_from_empty_rebuild(fresh_ek_data_only: Path) -> None:
    seeded = EgoKnowledge(fresh_ek_data_only)
    try:
        source = seeded.ingest("source", source_payload(title="重建来源"))
        concept = seeded.ingest("concept", concept_payload(source.id, title="重建概念"))
    finally:
        seeded.close()

    db_path = fresh_ek_data_only / "registry" / "catalog.sqlite"
    db_path.unlink()

    rebuilt = EgoKnowledge(fresh_ek_data_only)
    try:
        stats = rebuilt.build_registry()
        assert stats.entries_ok == 2
        assert rebuilt.get(source.id).title == "重建来源"
        assert rebuilt.get(concept.id).title == "重建概念"
    finally:
        rebuilt.close()


def test_doctor_repair_rebuilds_registry_after_commit_failure(fresh_ek, ek_root: Path) -> None:
    source = fresh_ek.ingest("source", source_payload(title="待修复来源"))
    source_path = absolute_entry_path(ek_root, source.file_path or "")
    recovery_log = ek_root / "logs" / "refresh" / "recovery.log"
    recovery_log.parent.mkdir(parents=True, exist_ok=True)
    recovery_log.write_text(
        json.dumps(
            {
                "ts": "2026-04-17T00:00:00+0800",
                "target_path": str(source_path),
                "message": "mock COMMIT failure",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    fresh_ek._registry.delete_entry_by_path(str(source_path))
    fresh_ek._registry.commit()

    report = fresh_ek.doctor(repair=True)

    assert fresh_ek.get(source.id).title == "待修复来源"
    assert not [finding for finding in report.findings if finding.severity == "high"]


def test_related_depth_2_returns_unique_neighbors(fresh_ek) -> None:
    source = fresh_ek.ingest("source", source_payload(title="图谱来源"))
    first = fresh_ek.ingest("concept", concept_payload(source.id, title="图谱起点"))
    second = fresh_ek.ingest("concept", concept_payload(source.id, title="关系桥接"))
    third = fresh_ek.ingest("concept", concept_payload(source.id, title="上下文终点"))

    fresh_ek.link(first.id, second.id, rel_type="related")
    fresh_ek.link(second.id, third.id, rel_type="related")
    fresh_ek.link(first.id, third.id, rel_type="related")

    related = fresh_ek.related(first.id, depth=2, rel_type="related")

    assert [entry.id for entry in related] == [second.id, third.id]


def test_nfc_normalization_across_writes(fresh_ek, ek_root: Path) -> None:
    decomposed = unicodedata.normalize("NFD", "Café")
    entry = fresh_ek.ingest(
        "source",
        source_payload(
            title=f"{decomposed} 知识来源",
            source_url=f"https://example.com/{decomposed}",
            search_terms=[f"{decomposed} 知识来源", "cafe source", "知识来源", "来源别名", ""],
            body=f"{decomposed} 正文",
        ),
    )
    updated = fresh_ek.update(
        entry.id,
        {
            "aliases": [f"{decomposed} alias"],
            "search_terms": [f"{decomposed} 知识来源", "", "知识来源", "来源别名", "cafe source"],
            "source_url": f"https://example.com/{decomposed}-updated",
        },
    )

    frontmatter, body = read_file(str(absolute_entry_path(ek_root, updated.file_path or "")))

    assert frontmatter["title"] == unicodedata.normalize("NFC", f"{decomposed} 知识来源")
    assert frontmatter["aliases"] == [unicodedata.normalize("NFC", f"{decomposed} alias")]
    assert frontmatter["source_url"] == unicodedata.normalize(
        "NFC",
        f"https://example.com/{decomposed}-updated",
    )
    assert body == unicodedata.normalize("NFC", f"{decomposed} 正文\n")


def test_full_lifecycle_ingest_link_search_diagnose_repair(fresh_ek, ek_root: Path) -> None:
    src1 = fresh_ek.ingest(
        "source",
        source_payload(
            title="证据A",
            source_url="https://a.example.com",
            content_hash="hash-a",
            search_terms=["证据A", "evidenceA", "来源A", "alias-a", "proof-a"],
        ),
    )
    src2 = fresh_ek.ingest(
        "source",
        source_payload(
            title="证据B",
            source_url="https://b.example.com",
            content_hash="hash-b",
            search_terms=["证据B", "evidenceB", "来源B", "alias-b", "proof-b"],
        ),
    )
    concept = fresh_ek.ingest(
        "concept",
        concept_payload(
            src1.id,
            title="单一真源",
            evidence_refs=[src1.id],
            evidence_status="partial",
            search_terms=["单一真源", "SSOT", "single source", "唯一真源", "alias-ssot"],
        ),
    )

    fresh_ek.link(src1.id, src2.id, rel_type="related")
    concept = fresh_ek.update(concept.id, {"evidence_refs": [src1.id, src2.id]})

    results = fresh_ek.search("单一真源", kinds=["concept"], limit=5)
    assert any(result.id == concept.id for result in results)

    diagnose_report = fresh_ek.diagnose()
    assert "redline_9_source_reachability" in diagnose_report.checked_rules
    assert "redline_10_view_as_evidence" in diagnose_report.checked_rules
    assert not [finding for finding in diagnose_report.findings if finding.severity == "high"]

    concept_path = absolute_entry_path(ek_root, concept.file_path or "")
    recovery_log = ek_root / "logs" / "refresh" / "recovery.log"
    recovery_log.parent.mkdir(parents=True, exist_ok=True)
    recovery_log.write_text(
        json.dumps(
            {
                "ts": "2026-04-17T00:00:00+0800",
                "target_path": str(concept_path),
                "message": "mock COMMIT failure",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    fresh_ek._registry.delete_entry_by_path(str(concept_path))
    fresh_ek._registry.commit()

    repair_report = fresh_ek.doctor(repair=True)

    assert fresh_ek.get(concept.id).title == "单一真源"
    assert not [finding for finding in repair_report.findings if finding.severity == "high"]


def _concurrent_update_worker(
    data_root: str,
    entry_id: str,
    tag: str,
    barrier: multiprocessing.Barrier,
    results: multiprocessing.Queue,
) -> None:
    barrier.wait()
    ek = EgoKnowledge(Path(data_root))
    try:
        ek.update(entry_id, {"tags": [tag]})
        results.put(("ok", tag))
    except StorageError as exc:
        results.put(("storage_error", str(exc)))
    finally:
        ek.close()
