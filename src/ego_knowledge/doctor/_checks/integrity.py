"""Integrity checks: schema, relations, ids, orphan files."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from ...registry import Registry
from .._helpers import _iter_all_entry_files
from .._types import Finding, Severity


def _check_schema_validation(registry: Registry, data_root: Path) -> list[Finding]:
    del registry, data_root
    return []


_BROKEN_RELATIONS_QUERY = """
    SELECT r.source_id   AS source_id,
           r.target_id   AS target_id,
           r.type        AS rel_type,
           r.origin      AS origin,
           src.file_path AS source_path
      FROM relations AS r
      LEFT JOIN entries AS src ON src.id = r.source_id
     WHERE NOT EXISTS (
         SELECT 1 FROM entries AS e WHERE e.id = r.target_id
     )
     ORDER BY r.source_id, r.target_id, r.type
"""


def _check_broken_relations(registry: Registry, data_root: Path) -> list[Finding]:
    """检测断裂关系：relations.target_id 在 entries 中不存在。

    每条断裂关系生成一条 finding，message 以结构化键值对承载
    ``source`` / ``target`` / ``type`` / ``origin`` 四字段，供任务 2.3
    按 origin 分级处理（ai_suggested / ai_confirmed / confirmed）。

    - ``Finding.target_id`` 设为 source entry id（关系发起方，存在），
      使 finding 仍能定位到一个真实条目。
    - ``Finding.target_path`` 设为 source entry 的 file_path，方便人工
      追溯；source 自身被归档/删除时为 None。
    - 无断裂关系时返回空列表。
    """
    del data_root
    findings: list[Finding] = []
    rows = registry.conn.execute(_BROKEN_RELATIONS_QUERY).fetchall()
    for row in rows:
        source_id = cast(str, row["source_id"])
        target_id = cast(str, row["target_id"])
        rel_type = cast(str, row["rel_type"])
        origin = cast(str, row["origin"])
        source_path = row["source_path"]
        findings.append(
            Finding(
                rule_id="broken_relations",
                severity=Severity.HIGH,
                target_id=source_id,
                target_path=cast(str | None, source_path),
                message=(
                    f"broken relation: source={source_id} target={target_id} "
                    f"type={rel_type} origin={origin}"
                ),
            )
        )
    return findings


def _check_frontmatter_body_link_diff(
    registry: Registry,
    data_root: Path,
) -> list[Finding]:
    del registry, data_root
    return []


def _check_dir_size_over_200(registry: Registry, data_root: Path) -> list[Finding]:
    del registry, data_root
    return []


def _check_orphan_files(registry: Registry, data_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    known_paths = {
        cast(str, row["file_path"])
        for row in registry.conn.execute("SELECT file_path FROM entries").fetchall()
    }
    for path in _iter_all_entry_files(data_root):
        if str(path) not in known_paths:
            findings.append(
                Finding(
                    rule_id="orphan_files",
                    severity=Severity.MEDIUM,
                    target_id=None,
                    target_path=str(path),
                    message=f"文件存在但 registry 无对应记录: {path}",
                )
            )
    return findings


def _check_id_uniqueness(registry: Registry, data_root: Path) -> list[Finding]:
    del data_root
    findings: list[Finding] = []
    rows = registry.conn.execute(
        "SELECT id, COUNT(*) AS cnt FROM entries GROUP BY id HAVING cnt > 1"
    ).fetchall()
    for row in rows:
        entry_id = cast(str, row["id"])
        findings.append(
            Finding(
                rule_id="id_uniqueness",
                severity=Severity.HIGH,
                target_id=entry_id,
                target_path=None,
                message=f"id {entry_id} 在 entries 表出现多次（数据完整性）",
            )
        )
    return findings
