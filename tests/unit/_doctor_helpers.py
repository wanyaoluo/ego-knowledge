"""doctor 测试系列共享辅助。

抽离自 `test_doctor_checks.py`，供 `test_doctor_checks` / `test_doctor_unicode`
/ `test_doctor_broken_relations` 共用，避免复制粘贴导致的行为漂移。

 - ``write_entry_with_body``：直接在 data_root 下落盘一个带指定 body 的条目
   markdown 文件（frontmatter 用半角结构标点），供 fullwidth 扫描类测试复用。
 - ``insert_broken_relation``：临时关闭外键约束注入 target 缺失的断裂关系，
   模拟 spec 描述的「40 条断裂关系」历史债务。
"""

from __future__ import annotations

from pathlib import Path

from ego_knowledge.core import EgoKnowledge


def write_entry_with_body(
    ek_root: Path, entry_id: str, body: str, kind: str = "source"
) -> Path:
    """在 data_root 下写入一个带指定 body 的条目文件。"""
    kind_dir = ek_root / f"{kind}s"
    kind_dir.mkdir(parents=True, exist_ok=True)
    path = kind_dir / f"{entry_id}.md"
    path.write_text(
        (
            "---\n"
            f"id: {entry_id}\n"
            f"kind: {kind}\n"
            f"title: {entry_id}\n"
            f"slug: {entry_id}\n"
            "status: authoritative\n"
            "freshness: watch\n"
            "schema_version: '1.0'\n"
            "created_at: 2026-04-16\n"
            "updated_at: 2026-04-17\n"
            "---\n"
            f"{body}\n"
        ),
        encoding="utf-8",
    )
    return path


def insert_broken_relation(
    ek: EgoKnowledge,
    source_id: str,
    missing_target_id: str,
    rel_type: str,
    origin: str,
) -> None:
    """模拟存量数据中 relations.target_id 不存在的断裂关系。

    registry 默认开启外键约束，所以这里临时关闭以注入断裂数据，
    再恢复启用，模拟 spec 描述的"40 条断裂关系"历史债务。
    """
    conn = ek._registry.conn
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO relations(source_id, target_id, type, origin) VALUES (?, ?, ?, ?)",
        (source_id, missing_target_id, rel_type, origin),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    ek._registry.commit()
