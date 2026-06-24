"""共享数据结构：断裂关系扫描、清理计划与报告。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ego_knowledge.models import Entry

AI_RELATION_ORIGINS = frozenset({"ai_suggested", "ai_confirmed"})
CONFIRMED_RELATION_ORIGIN = "confirmed"


@dataclass(frozen=True, slots=True)
class SourceFile:
    """一个 Markdown 条目真源文件及其已解析 frontmatter。"""

    path: Path
    relative_path: str
    original_text: str
    frontmatter: dict[str, object]
    body: str
    entry: Entry


@dataclass(slots=True)
class BrokenRelation:
    """一条断裂关系，来源可同时来自 registry 与 frontmatter 扫描。"""

    source_id: str
    target: str
    type: str
    origin: str
    source_path: str | None
    storages: list[str] = field(default_factory=list)
    detectors: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ParseError:
    """扫描 Markdown 真源时遇到的解析错误。"""

    path: str
    message: str


@dataclass(frozen=True, slots=True)
class FileCleanupChange:
    """单个文件将删除或已删除的 AI 断裂关系。"""

    path: str
    removed_relations: list[BrokenRelation]


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """dry-run/apply 共用报告。"""

    data_root: Path
    scanned_files: int
    known_entries: int
    broken_relations: list[BrokenRelation]
    confirmed_adjudication: list[BrokenRelation]
    file_changes: list[FileCleanupChange]
    registry_delete_relations: list[BrokenRelation]
    parse_errors: list[ParseError]

    @property
    def broken_count(self) -> int:
        return len(self.broken_relations)

    @property
    def ai_cleanup_count(self) -> int:
        keys: set[tuple[str, str, str, str]] = set()
        for change in self.file_changes:
            for relation in change.removed_relations:
                keys.add((relation.source_id, relation.target, relation.type, relation.origin))
        for relation in self.registry_delete_relations:
            keys.add((relation.source_id, relation.target, relation.type, relation.origin))
        return len(keys)


RelationKey = tuple[str, str, str, str]
