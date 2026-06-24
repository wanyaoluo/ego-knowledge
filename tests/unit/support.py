from __future__ import annotations

import os
from pathlib import Path

from ego_knowledge.frontmatter import _fm_to_entry, read_file, write_file


def source_payload(title: str = "测试来源", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "source_type": "web",
        "source_url": f"https://example.com/{title}",
        "content_hash": f"hash-{title}",
        "search_terms": [title, "source", "src", "来源样例", "alias-source"],
        "tags": ["测试"],
    }
    payload.update(overrides)
    return payload


def note_payload(source_id: str, title: str = "测试笔记", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "source_refs": [source_id],
        "search_terms": [title, "note", "nt", "笔记样例", "alias-note"],
        "tags": ["测试"],
        "body": "x" * 50,
    }
    payload.update(overrides)
    return payload


def concept_payload(
    source_id: str,
    title: str = "测试概念",
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "evidence_refs": [source_id],
        "evidence_status": "partial",
        "search_terms": [title, "concept", "con", "概念样例", "alias-concept"],
        "tags": ["测试"],
        "body": "x" * 50,
    }
    payload.update(overrides)
    return payload


def dossier_payload(
    evidence_ref: str,
    title: str = "测试档案",
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "evidence_refs": [evidence_ref],
        "search_terms": [title, "dossier", "dos", "档案样例", "alias-dossier"],
        "tags": ["测试"],
        "body": "x" * 50,
    }
    payload.update(overrides)
    return payload


def decision_payload(
    evidence_ref: str,
    title: str = "测试决策",
    **overrides: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "evidence_refs": [evidence_ref],
        "decision_status": "active",
        "search_terms": [title, "decision", "dec", "决策样例", "alias-decision"],
        "tags": ["测试"],
        "body": "x" * 50,
    }
    payload.update(overrides)
    return payload


def view_payload(title: str = "测试视图", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "generator": "manual",
        "source_query": "kind:concept",
        "search_terms": [title, "view", "vw", "视图样例", "alias-view"],
        "tags": ["测试"],
        "body": "x" * 50,
    }
    payload.update(overrides)
    return payload


def absolute_entry_path(data_root: Path, relative_path: str) -> Path:
    return data_root / relative_path


def relative_link(source_path: Path, target_path: Path) -> str:
    return os.path.relpath(target_path, start=source_path.parent).replace(os.sep, "/")


def overwrite_body(ek: object, data_root: Path, relative_path: str, body: str) -> None:
    path = absolute_entry_path(data_root, relative_path)
    frontmatter, _ = read_file(str(path))
    write_file(str(path), frontmatter, body)
    registry = getattr(ek, "_registry")
    entry = _fm_to_entry(frontmatter, file_path=str(path), body=body)
    registry.upsert_entry(entry, path, body)
    registry.commit()
