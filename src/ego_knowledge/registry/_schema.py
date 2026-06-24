"""SchemaOps: DDL, schema versioning, and migration guards."""

from __future__ import annotations

import sqlite3
from typing import cast

from ..errors import StorageError
from ._ddl import SCHEMA_SQL
from ._typing import RegistryMixinBase

REGISTRY_SCHEMA_VERSION = "2.3"


class SchemaOps(RegistryMixinBase):
    """DDL, schema versioning, and migration guards."""

    def init_schema(self) -> None:
        """Create every Phase 2 tables and validate required SQLite features."""

        try:
            catalog_existed = self._catalog_exists()
            if catalog_existed:
                current = self._read_schema_version()
                if current is not None:
                    from packaging.version import Version

                    cur_v = Version(current)
                    target_v = Version(REGISTRY_SCHEMA_VERSION)
                    if cur_v < target_v:
                        raise RuntimeError(
                            f"catalog schema_version={current} 低于目标 "
                            f"{REGISTRY_SCHEMA_VERSION}，历史一次性迁移脚本已退役；"
                            "请从备份恢复到匹配版本或重建数据根后再升级。"
                        )
                    if cur_v > target_v:
                        raise RuntimeError(
                            f"catalog schema_version={current} 高于代码目标 "
                            f"{REGISTRY_SCHEMA_VERSION}，请升级代码或检查 catalog。"
                        )
                    self._ensure_external_watch_404_columns()
                    self._ensure_maintenance_queue_approval_columns()
                    return

            self._ensure_fts5_trigram()
            self.conn.executescript(SCHEMA_SQL)
            existing = self._read_schema_version() if catalog_existed else None
            schema_version = existing or REGISTRY_SCHEMA_VERSION
            self.conn.execute(
                """
                INSERT INTO registry_meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("schema_version", schema_version),
            )
            self.conn.execute(
                """
                INSERT INTO registry_meta(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                ("maintenance_queue_version", "1"),
            )
            self.rebuild_custom_dictionary()
            self.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"初始化注册表 schema 失败 {self.path}: {exc}") from exc

    def _ensure_external_watch_404_columns(self) -> None:
        """Ensure external_watch has 404 counter columns (idempotent)."""
        try:
            columns = {
                row[1] for row in self.conn.execute("PRAGMA table_info(external_watch)").fetchall()
            }
            changed = False
            if "consecutive_404_count" not in columns:
                self.conn.execute(
                    "ALTER TABLE external_watch "
                    "ADD COLUMN consecutive_404_count INTEGER NOT NULL DEFAULT 0"
                )
                changed = True
            if "last_404_at" not in columns:
                self.conn.execute("ALTER TABLE external_watch ADD COLUMN last_404_at TEXT")
                changed = True
            if changed:
                self.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"迁移 external_watch 404 字段失败: {exc}") from exc

    def _ensure_maintenance_queue_approval_columns(self) -> None:
        """Ensure Phase 8.2 approval metadata columns exist (idempotent)."""
        try:
            columns = {
                row[1]
                for row in self.conn.execute("PRAGMA table_info(maintenance_queue)").fetchall()
            }
            changed = False
            if "origin" not in columns:
                self.conn.execute(
                    "ALTER TABLE maintenance_queue ADD COLUMN origin TEXT NOT NULL DEFAULT 'human'"
                )
                changed = True
            if "proposed_op" not in columns:
                self.conn.execute("ALTER TABLE maintenance_queue ADD COLUMN proposed_op TEXT")
                changed = True
            if "proposed_payload_json" not in columns:
                self.conn.execute(
                    "ALTER TABLE maintenance_queue ADD COLUMN proposed_payload_json TEXT"
                )
                changed = True
            if "agent_id" not in columns:
                self.conn.execute("ALTER TABLE maintenance_queue ADD COLUMN agent_id TEXT")
                changed = True
            if changed:
                self.commit()
        except sqlite3.Error as exc:
            raise StorageError(f"迁移 maintenance_queue 审批字段失败: {exc}") from exc

    def _catalog_exists(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='registry_meta'"
        ).fetchone()
        return row is not None

    def _read_schema_version(self) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM registry_meta WHERE key = 'schema_version'"
        ).fetchone()
        return cast(str, row["value"]) if row is not None else None

    def list_tables(self) -> list[str]:
        """Return user-defined tables in the main SQLite schema."""
        rows = self.conn.execute(
            """
            SELECT name
              FROM sqlite_master
             WHERE type = 'table'
               AND name NOT LIKE 'sqlite_%'
             ORDER BY name
            """
        ).fetchall()
        return [cast(str, row["name"]) for row in rows]

    def _ensure_fts5_trigram(self) -> None:
        try:
            self.conn.execute(
                """
                CREATE VIRTUAL TABLE temp.__fts5_trigram_probe
                USING fts5(content, tokenize='trigram')
                """
            )
            self.conn.execute("DROP TABLE temp.__fts5_trigram_probe")
        except sqlite3.Error as exc:
            raise StorageError("当前 SQLite 缺少 FTS5 trigram tokenizer 支持") from exc
