"""FtsOps: FTS5 index sync, tokenization helpers, and jieba paths."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .._frontmatter_coercion import _string_list, _string_value, _tokenized_field
from ..errors import StorageError
from ..tokenizer import rebuild_custom_dict, sync_runtime_words
from ._typing import RegistryMixinBase

if TYPE_CHECKING:
    from . import Registry


class FtsOps(RegistryMixinBase):
    """FTS5 index sync helpers. No independent transaction entry point."""

    def index_entry_for_fts(self, entry_id: str, fields: dict[str, object]) -> None:
        """Index one entry into the three Phase 4 FTS tables."""

        aliases = _string_list(fields.get("aliases"))
        search_terms = _string_list(fields.get("search_terms"))
        tags = _string_list(fields.get("tags"))
        title = _string_value(fields.get("title"))
        body = _string_value(fields.get("body"))

        sync_runtime_words((*aliases, *tags))
        self._delete_fts_rows(entry_id)
        jieba_dict_dir = self._jieba_dict_dir()
        fallback_log_path = self._jieba_fallback_log_path()

        cn_values = (
            entry_id,
            _tokenized_field(title, jieba_dict_dir, fallback_log_path),
            _tokenized_field(" ".join(aliases), jieba_dict_dir, fallback_log_path),
            _tokenized_field(
                " ".join(search_terms),
                jieba_dict_dir,
                fallback_log_path,
            ),
            _tokenized_field(" ".join(tags), jieba_dict_dir, fallback_log_path),
            _tokenized_field(body, jieba_dict_dir, fallback_log_path),
        )
        en_values = (
            entry_id,
            _extract_ascii_text(title),
            _extract_ascii_text(" ".join(aliases)),
            _extract_ascii_text(" ".join(search_terms)),
            _extract_ascii_text(body),
        )
        tri_values = (
            entry_id,
            title,
            " ".join(aliases),
            " ".join(search_terms),
            body,
        )

        try:
            self.conn.execute(
                """
                INSERT INTO entries_fts_cn(id, title, aliases, search_terms, tags, body)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                cn_values,
            )
            self.conn.execute(
                """
                INSERT INTO entries_fts_en(id, title, aliases, search_terms, body)
                VALUES(?, ?, ?, ?, ?)
                """,
                en_values,
            )
            self.conn.execute(
                """
                INSERT INTO entries_fts_tri(id, title, aliases, search_terms, body)
                VALUES(?, ?, ?, ?, ?)
                """,
                tri_values,
            )
        except sqlite3.Error as exc:
            raise StorageError(f"写入 FTS 索引失败 {entry_id}: {exc}") from exc

    def _sync_fts_index(self, entry: Entry, body: str) -> None:  # type: ignore[name-defined]  # noqa: F821
        """Sync FTS index from entry + body. Called inside _upsert_entry transaction."""
        self.index_entry_for_fts(
            entry.id,
            {
                "title": entry.title,
                "aliases": list(entry.aliases),
                "search_terms": list(entry.search_terms),
                "tags": list(entry.tags),
                "body": body,
            },
        )

    def _delete_fts_rows(self, entry_id: str) -> None:
        self.conn.execute("DELETE FROM entries_fts_cn WHERE id = ?", (entry_id,))
        self.conn.execute("DELETE FROM entries_fts_en WHERE id = ?", (entry_id,))
        self.conn.execute("DELETE FROM entries_fts_tri WHERE id = ?", (entry_id,))

    def rebuild_custom_dictionary(self) -> None:
        """Rebuild the jieba custom dictionary for the current registry."""

        rebuild_custom_dict(cast("Registry", self), self._jieba_dict_dir())

    def _jieba_dict_dir(self) -> Path:
        return self._data_root / "registry" / "jieba"

    def _jieba_fallback_log_path(self) -> Path:
        return self._data_root / "logs" / "refresh" / "jieba-fallback.log"


# _extract_ascii_text is imported from the coercion module, but we keep it
# locally here since it was originally in registry.py and is used only by FTS.
from .._frontmatter_coercion import _extract_ascii_text as _extract_ascii_text  # noqa: E402
