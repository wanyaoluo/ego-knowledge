"""WriteOps: insert, upsert, stage_build, and entry mutation helpers."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .._frontmatter_coercion import (
    _date_to_text,
    _date_to_text_or_none,
    _utc_now_text,
)
from .._serde import _serialize_frontmatter
from ..errors import StorageError
from ..unicode_utils import to_nfc
from ._ddl import KIND_FIELD_TABLES
from ._typing import RegistryMixinBase

if TYPE_CHECKING:
    from ._fts import FtsOps


class WriteOps(RegistryMixinBase):
    """Insert / upsert / stage_build and entry mutation helpers."""

    def insert_entry(self, entry: Entry, file_path: Path, body: str) -> None:  # type: ignore[name-defined]  # noqa: F821
        self.upsert_entry(entry, file_path, body)

    def upsert_entry(self, entry: Entry, file_path: Path, body: str) -> None:  # type: ignore[name-defined]  # noqa: F821
        self._upsert_entry(entry, file_path, body, allow_missing_targets=False)

    def stage_build_entry(self, entry: Entry, file_path: Path, body: str) -> None:  # type: ignore[name-defined]  # noqa: F821
        """Stage an entry during full rebuild (allow forward relation refs)."""
        self._upsert_entry(entry, file_path, body, allow_missing_targets=True)

    def validate_build_entry_relations(self, entry: Entry) -> None:  # type: ignore[name-defined]  # noqa: F821
        """Strictly refresh staged relations after all rebuild entries are loaded."""
        self._refresh_relations(entry, allow_missing_targets=False)

    def delete_entry_by_id(self, entry_id: str) -> None:
        """Delete a single entry by ID (build rollback on validation failure)."""
        self.conn.execute("DELETE FROM entries WHERE id = ?", (entry_id,))

    def upsert_meta(self, key: str, value: str) -> None:
        """Insert or update a registry_meta key-value pair."""
        self.conn.execute(
            """
            INSERT INTO registry_meta(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

    def init_metrics_placeholders(self) -> None:
        """Ensure every entry has a zero-valued metrics row (INSERT OR IGNORE)."""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO entry_metrics(
                entry_id,
                evidence_strength,
                drift_score,
                compression_ratio,
                action_relevance,
                retrieval_heat,
                updated_at
            )
            SELECT id, 0.0, 0.0, 0, 0, 0.0, datetime('now')
            FROM entries
            """
        )

    def replay_access_log(self, rows: list[tuple[str, str, str]]) -> None:
        """Bulk insert access log entries from historical replay."""
        if rows:
            self.conn.executemany(
                """
                INSERT INTO access_log(entry_id, op, accessed_at)
                VALUES(?, ?, ?)
                """,
                rows,
            )

    def _upsert_entry(
        self,
        entry: Entry,  # type: ignore[name-defined]  # noqa: F821
        file_path: Path,
        body: str,
        *,
        allow_missing_targets: bool,
    ) -> None:
        frontmatter_json = _serialize_frontmatter(entry)
        try:
            self.conn.execute(
                """
                INSERT INTO entries(
                    id,
                    kind,
                    title,
                    slug,
                    file_path,
                    status,
                    freshness,
                    confidence,
                    schema_version,
                    domain,
                    created_at,
                    updated_at,
                    frontmatter_json,
                    body
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    title = excluded.title,
                    slug = excluded.slug,
                    file_path = excluded.file_path,
                    status = excluded.status,
                    freshness = excluded.freshness,
                    confidence = excluded.confidence,
                    schema_version = excluded.schema_version,
                    domain = excluded.domain,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    frontmatter_json = excluded.frontmatter_json,
                    body = excluded.body
                """,
                (
                    entry.id,
                    entry.kind.value,
                    entry.title,
                    entry.slug,
                    str(file_path),
                    entry.status.value,
                    entry.freshness.value,
                    entry.confidence,
                    entry.schema_version,
                    entry.domain,
                    _date_to_text(entry.created_at),
                    _date_to_text(entry.updated_at),
                    frontmatter_json,
                    body,
                ),
            )
            self.conn.execute("DELETE FROM aliases WHERE entry_id = ?", (entry.id,))
            self.conn.execute("DELETE FROM entry_tags WHERE entry_id = ?", (entry.id,))
            self.conn.execute(
                "DELETE FROM entry_search_terms WHERE entry_id = ?",
                (entry.id,),
            )
            for table_name in KIND_FIELD_TABLES:
                # Safe f-string: table_name comes from the fixed
                # KIND_FIELD_TABLES tuple; entry id remains parameterized.
                self.conn.execute(
                    f"DELETE FROM {table_name} WHERE entry_id = ?",
                    (entry.id,),
                )

            self.conn.executemany(
                """
                INSERT INTO aliases(alias_nfc, entry_id)
                VALUES(?, ?)
                """,
                [(to_nfc(alias), entry.id) for alias in entry.aliases],
            )
            self.conn.executemany(
                """
                INSERT INTO entry_tags(entry_id, tag)
                VALUES(?, ?)
                """,
                [(entry.id, tag) for tag in entry.tags],
            )
            self.conn.executemany(
                """
                INSERT INTO entry_search_terms(entry_id, term)
                VALUES(?, ?)
                """,
                [(entry.id, term) for term in entry.search_terms],
            )
            _upsert_kind_fields(self.conn, entry)
            self._refresh_relations(
                entry,
                allow_missing_targets=allow_missing_targets,
            )
            # FTS sync MUST stay inside this transaction.
            cast("FtsOps", self)._sync_fts_index(entry, body)
            self.conn.execute(
                """
                INSERT INTO entry_metrics(
                    entry_id,
                    evidence_strength,
                    drift_score,
                    compression_ratio,
                    action_relevance,
                    retrieval_heat,
                    updated_at
                )
                VALUES(?, 0, 0, 0, 0, 0, ?)
                ON CONFLICT(entry_id) DO NOTHING
                """,
                (entry.id, _utc_now_text()),
            )
        except sqlite3.Error as exc:
            raise StorageError(f"写入注册表失败 {entry.id}: {exc}") from exc

    def delete_entry_by_path(self, file_path: str) -> None:
        try:
            row = self.conn.execute(
                "SELECT id FROM entries WHERE file_path = ?",
                (file_path,),
            ).fetchone()
            if row is not None:
                cast("FtsOps", self)._delete_fts_rows(cast(str, row["id"]))
            self.conn.execute("DELETE FROM entries WHERE file_path = ?", (file_path,))
        except sqlite3.Error as exc:
            raise StorageError(f"按路径删除条目失败 {file_path}: {exc}") from exc


def _upsert_kind_fields(conn: sqlite3.Connection, entry: Entry) -> None:  # type: ignore[name-defined]  # noqa: F821
    """Write kind-specific field row for *entry* using *conn*."""
    from ..models import (
        ConceptEntry,
        DecisionEntry,
        DossierEntry,
        NoteEntry,
        SourceEntry,
    )

    if isinstance(entry, SourceEntry):
        conn.execute(
            """
            INSERT INTO source_fields(
                entry_id, source_type, source_url, captured_at, content_hash
            )
            VALUES(?, ?, ?, ?, ?)
            """,
            (
                entry.id,
                entry.source_type,
                entry.source_url,
                _date_to_text_or_none(entry.captured_at),
                entry.content_hash,
            ),
        )
        return
    if isinstance(entry, NoteEntry):
        conn.execute(
            "INSERT INTO note_fields(entry_id, extracted_at) VALUES(?, ?)",
            (entry.id, _date_to_text_or_none(entry.extracted_at)),
        )
        return
    if isinstance(entry, DossierEntry):
        conn.execute(
            """
            INSERT INTO dossier_fields(entry_id, reviewed_at, review_due_at)
            VALUES(?, ?, ?)
            """,
            (
                entry.id,
                _date_to_text_or_none(entry.reviewed_at),
                _date_to_text_or_none(entry.review_due_at),
            ),
        )
        return
    if isinstance(entry, ConceptEntry):
        conn.execute(
            "INSERT INTO concept_fields(entry_id, evidence_status) VALUES(?, ?)",
            (entry.id, entry.evidence_status),
        )
        return
    if isinstance(entry, DecisionEntry):
        conn.execute(
            """
            INSERT INTO decision_fields(
                entry_id, decided_at, decision_status, superseded_by
            )
            VALUES(?, ?, ?, ?)
            """,
            (
                entry.id,
                _date_to_text_or_none(entry.decided_at),
                entry.decision_status,
                entry.superseded_by,
            ),
        )
        return
    conn.execute(
        """
        INSERT INTO view_fields(entry_id, generator, generated_at, source_query)
        VALUES(?, ?, ?, ?)
        """,
        (
            entry.id,
            entry.generator,
            _date_to_text_or_none(entry.generated_at),
            entry.source_query,
        ),
    )
