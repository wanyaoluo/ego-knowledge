from __future__ import annotations

import multiprocessing
from pathlib import Path

from ego_knowledge.core import EgoKnowledge
from ego_knowledge.frontmatter import read_file
from tests.integration.test_phase6_qa import _concurrent_update_worker
from tests.unit.support import absolute_entry_path, concept_payload, source_payload


def test_multi_process_concurrent_writes_keep_entry_consistent(ek_root: Path) -> None:
    seeded = EgoKnowledge(ek_root)
    try:
        source = seeded.ingest("source", source_payload(title="并发来源"))
        concept = seeded.ingest(
            "concept",
            concept_payload(
                source.id,
                title="并发概念",
                tags=["initial"],
            ),
        )
        relative_path = concept.file_path or ""
    finally:
        seeded.close()

    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(4)
    results = ctx.Queue()
    tags = [f"tag-{index}" for index in range(4)]
    processes = [
        ctx.Process(
            target=_concurrent_update_worker,
            args=(str(ek_root), concept.id, tag, barrier, results),
        )
        for tag in tags
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0

    outcomes = [results.get(timeout=5) for _ in tags]
    assert any(status == "ok" for status, _ in outcomes)
    assert all(status in {"ok", "storage_error"} for status, _ in outcomes)

    inspected = EgoKnowledge(ek_root)
    try:
        entry = inspected.get(concept.id)
        frontmatter, _ = read_file(str(absolute_entry_path(ek_root, relative_path)))
    finally:
        inspected.close()

    assert entry.tags in [[tag] for tag in tags]
    assert frontmatter["tags"] in [[tag] for tag in tags]
    assert not list(ek_root.rglob("*.tmp"))
