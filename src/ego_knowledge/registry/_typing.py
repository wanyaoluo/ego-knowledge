"""Shared typing surface for registry mixins."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..models import Entry


class RegistryMixinBase:
    """Attributes and cross-mixin hooks supplied by ``Registry``."""

    conn: sqlite3.Connection
    path: Path
    _data_root: Path

    def commit(self) -> None:
        raise NotImplementedError

    def rebuild_custom_dictionary(self) -> None:
        raise NotImplementedError

    def has_entry(self, entry_id: str) -> bool:
        raise NotImplementedError

    def get_entry(self, entry_id: str) -> Entry:
        raise NotImplementedError

    def all_aliases(self) -> list[str]:
        raise NotImplementedError

    def all_tags(self) -> list[str]:
        raise NotImplementedError

    def _refresh_relations(
        self,
        entry: Entry,
        *,
        allow_missing_targets: bool,
    ) -> None:
        raise NotImplementedError
