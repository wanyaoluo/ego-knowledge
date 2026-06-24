"""ReadOps: query and row-to-entity conversion."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from .._serde import _entry_from_frontmatter
from ..errors import NotFoundError, StorageError
from ..unicode_utils import to_nfc
from ._typing import RegistryMixinBase

type JsonMap = dict[str, object]
type RuntimeMeta = dict[str, object]


class ReadOps(RegistryMixinBase):
    """Query methods and row-to-entity conversion."""

    def has_entry(self, entry_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM entries WHERE id = ? LIMIT 1",
            (entry_id,),
        ).fetchone()
        return row is not None

    def get_entry(self, entry_id: str) -> Entry:  # type: ignore[name-defined]  # noqa: F821
        row = self.conn.execute(
            """
            SELECT id, file_path, body, frontmatter_json
              FROM entries
             WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"条目不存在: {entry_id}")
        return self._row_to_entry(row)

    def get_runtime_meta(self, entry_id: str) -> RuntimeMeta:
        row = self.conn.execute(
            """
            SELECT file_path, body
              FROM entries
             WHERE id = ?
            """,
            (entry_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"条目不存在: {entry_id}")
        meta: RuntimeMeta = {
            "file_path": cast(str, row["file_path"]),
            "body": cast(str, row["body"]),
        }
        metrics = self._fetch_metrics_map(entry_id)
        if metrics is not None:
            meta["metrics"] = metrics
        return meta

    def all_entries(self) -> list[Entry]:  # type: ignore[name-defined]  # noqa: F821
        rows = self.conn.execute(
            """
            SELECT id, file_path, body, frontmatter_json
              FROM entries
             ORDER BY id
            """
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def all_entries_by_kind(self, kind: str) -> list[Entry]:  # type: ignore[name-defined]  # noqa: F821
        from .._frontmatter_coercion import _normalize_kind

        kind_value = _normalize_kind(kind).value
        rows = self.conn.execute(
            """
            SELECT id, file_path, body, frontmatter_json
              FROM entries
             WHERE kind = ?
             ORDER BY id
            """,
            (kind_value,),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def all_entries_except_kind(self, kind: str) -> list[Entry]:  # type: ignore[name-defined]  # noqa: F821
        from .._frontmatter_coercion import _normalize_kind

        kind_value = _normalize_kind(kind).value
        rows = self.conn.execute(
            """
            SELECT id, file_path, body, frontmatter_json
              FROM entries
             WHERE kind != ?
             ORDER BY id
            """,
            (kind_value,),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def all_sources(self) -> list[SourceEntry]:  # type: ignore[name-defined]  # noqa: F821
        from ..models import Kind, SourceEntry

        entries = self.all_entries_by_kind(Kind.SOURCE.value)
        return [cast(SourceEntry, entry) for entry in entries]

    def all_entry_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT id FROM entries ORDER BY id").fetchall()
        return [cast(str, row["id"]) for row in rows]

    def dense_index_populated(self) -> bool:
        """Return whether at least one dense embedding is available."""

        try:
            row = self.conn.execute("SELECT 1 FROM dense_embeddings LIMIT 1").fetchone()
        except sqlite3.Error as exc:
            raise StorageError(f"读取 dense 索引状态失败: {exc}") from exc
        return row is not None

    def authority_score_map(self, entry_ids: list[str]) -> dict[str, float]:
        """Return persisted graph authority scores for a batch of entries."""

        if not entry_ids:
            return {}
        unique_ids = list(dict.fromkeys(entry_ids))
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self.conn.execute(
            f"""
            SELECT entry_id, authority_score
              FROM entry_metrics
             WHERE entry_id IN ({placeholders})
            """,
            tuple(unique_ids),
        ).fetchall()
        return {
            cast(str, row["entry_id"]): float(cast(float, row["authority_score"])) for row in rows
        }

    def find_by_aliases(self, aliases: list[str]) -> list[Entry]:  # type: ignore[name-defined]  # noqa: F821
        normalized = sorted({to_nfc(alias) for alias in aliases if alias})
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT e.id, e.file_path, e.body, e.frontmatter_json
              FROM entries AS e
              JOIN aliases AS a
                ON a.entry_id = e.id
             WHERE a.alias_nfc IN ({placeholders})
             ORDER BY e.id
            """,
            tuple(normalized),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def all_aliases(self) -> list[str]:
        rows = self.conn.execute("SELECT alias_nfc FROM aliases ORDER BY alias_nfc").fetchall()
        return [cast(str, row["alias_nfc"]) for row in rows]

    def all_tags(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT tag FROM entry_tags ORDER BY tag").fetchall()
        return [cast(str, row["tag"]) for row in rows]

    def all_titles_for_kind(self, kind: str) -> list[tuple[str, str]]:
        from .._frontmatter_coercion import _normalize_kind

        kind_value = _normalize_kind(kind).value
        rows = self.conn.execute(
            """
            SELECT id, title
              FROM entries
             WHERE kind = ?
             ORDER BY id
            """,
            (kind_value,),
        ).fetchall()
        return [(cast(str, row["id"]), cast(str, row["title"])) for row in rows]

    def all_terms_flat(self) -> list[tuple[str, str, str]]:
        terms: list[tuple[str, str, str]] = []
        for entry in self.all_entries():
            terms.append((entry.title, entry.id, "title"))
            for alias in dict.fromkeys(entry.aliases):
                terms.append((alias, entry.id, "aliases"))
            for tag in dict.fromkeys(entry.tags):
                terms.append((tag, entry.id, "tags"))
            for term in dict.fromkeys(entry.search_terms):
                terms.append((term, entry.id, "search_terms"))
        return sorted(terms, key=lambda item: (item[0], item[1], item[2]))

    def entry_aliases_map(self) -> dict[str, set[str]]:
        rows = self.conn.execute(
            "SELECT entry_id, alias_nfc FROM aliases ORDER BY entry_id, alias_nfc"
        ).fetchall()
        result: dict[str, set[str]] = {}
        for row in rows:
            entry_id = cast(str, row["entry_id"])
            alias = cast(str, row["alias_nfc"])
            result.setdefault(entry_id, set()).add(alias)
        return result

    def _row_to_entry(self, row: sqlite3.Row) -> Entry:  # type: ignore[name-defined]  # noqa: F821
        raw = cast(object, json.loads(cast(str, row["frontmatter_json"])))
        if not isinstance(raw, dict):
            raise StorageError(f"frontmatter_json 结构损坏: {row['id']}")
        frontmatter_map = cast(JsonMap, raw)
        return _entry_from_frontmatter(
            frontmatter_map,
            file_path=cast(str, row["file_path"]),
            body=cast(str, row["body"]),
            metrics=self._fetch_metrics_map(cast(str, row["id"])),
        )

    def _fetch_metrics_map(self, entry_id: str) -> JsonMap | None:
        row = self.conn.execute(
            """
            SELECT evidence_strength,
                   drift_score,
                   compression_ratio,
                   action_relevance,
                   retrieval_heat,
                   updated_at
              FROM entry_metrics
             WHERE entry_id = ?
            """,
            (entry_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "evidence_strength": float(cast(float, row["evidence_strength"])),
            "drift_score": float(cast(float, row["drift_score"])),
            "compression_ratio": float(cast(float, row["compression_ratio"])),
            "action_relevance": float(cast(float, row["action_relevance"])),
            "retrieval_heat": float(cast(float, row["retrieval_heat"])),
            "updated_at": cast(str, row["updated_at"]),
        }
