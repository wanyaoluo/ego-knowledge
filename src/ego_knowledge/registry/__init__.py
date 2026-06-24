"""SQLite registry for EgoKnowledge derived catalog.

Split into mixin modules for maintainability; ``Registry`` combines them
via multiple inheritance.  ``from ego_knowledge.registry import Registry``
remains the public import path.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..errors import StorageError
from ._audit import AuditOps
from ._fts import FtsOps
from ._read import ReadOps
from ._relations import RelationsOps
from ._ddl import SCHEMA_SQL
from ._schema import REGISTRY_SCHEMA_VERSION, SchemaOps
from ._write import WriteOps

__all__ = [
    "Registry",
    "REGISTRY_SCHEMA_VERSION",
    "SCHEMA_SQL",
]


class Registry(SchemaOps, ReadOps, WriteOps, FtsOps, RelationsOps, AuditOps):
    """High-level API over the derived SQLite catalog.

    Combines six Ops mixins:
    SchemaOps → DDL + upgrade
    ReadOps → queries / row↔entity conversion
    WriteOps → insert / upsert / stage_build
    FtsOps → FTS5 sync (called inside WriteOps._upsert_entry transaction)
    RelationsOps → _refresh_relations / graph traversal
    AuditOps → stats / review_queue / heat / metrics persistence
    """

    def __init__(self, db_path: Path) -> None:
        self.path = db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data_root = self.path.parent.parent
        try:
            self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        except sqlite3.Error as exc:
            raise StorageError(f"无法打开注册表 {self.path}: {exc}") from exc
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        try:
            self.conn.close()
        except sqlite3.Error as exc:
            raise StorageError(f"关闭注册表失败 {self.path}: {exc}") from exc

    def commit(self) -> None:
        """Commit the current transaction."""

        try:
            self.conn.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"提交注册表事务失败 {self.path}: {exc}") from exc


# --- Re-exports from extracted modules (backward compatibility) ---
# ruff: noqa: I001
from .._build import (  # noqa: E402
    RegistryStats as RegistryStats,
    build_registry as build_registry,
)
from .._frontmatter_coercion import (  # noqa: E402
    _as_float as _as_float,
    _as_int as _as_int,
    _date_to_text as _date_to_text,
    _date_to_text_or_none as _date_to_text_or_none,
    _extract_ascii_text as _extract_ascii_text,
    _metrics_to_map as _metrics_to_map,
    _normalize_kind as _normalize_kind,
    _parse_datetime_text as _parse_datetime_text,
    _string_list as _string_list,
    _string_value as _string_value,
    _tokenized_field as _tokenized_field,
    _utc_now_text as _utc_now_text,
)
from .._serde import (  # noqa: E402
    _entry_from_frontmatter as _entry_from_frontmatter,
    _materialized_relation_rows as _materialized_relation_rows,
    _serialize_frontmatter as _serialize_frontmatter,
)
