"""Domain vocabulary repository for EgoKnowledge."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ._entry_store import validate_explicit_slug
from .errors import ConflictError, StorageError, ValidationError
from .paths import _UNSORTED_DOMAIN
from .registry import Registry
from .unicode_utils import to_nfc


@dataclass(slots=True)
class MigrateResult:
    entry_ids: list[str]
    rewritten_paths: list[str]
    target_domain: str


class DomainRegistry:
    """Registry-backed domain vocabulary and normalization helpers."""

    def __init__(self, data_root: Path, registry: Registry) -> None:
        self._data_root = data_root
        self._registry = registry

    def list_domains(self) -> list[dict[str, object]]:
        domains = set(self.load_vocab())
        rows = self._registry.conn.execute(
            """
            SELECT COALESCE(NULLIF(domain, ''), ?) AS name,
                   COUNT(*) AS entry_count,
                   MAX(updated_at) AS last_updated
              FROM entries
             GROUP BY COALESCE(NULLIF(domain, ''), ?)
            """,
            (_UNSORTED_DOMAIN, _UNSORTED_DOMAIN),
        ).fetchall()
        indexed: dict[str, dict[str, object]] = {}
        for row in rows:
            name = cast(str, row["name"])
            indexed[name] = {
                "name": name,
                "entry_count": int(cast(int, row["entry_count"])),
                "last_updated": cast(str | None, row["last_updated"]),
            }
            domains.add(name)
        domains.add(_UNSORTED_DOMAIN)
        result: list[dict[str, object]] = []
        for name in sorted(domains):
            result.append(
                indexed.get(
                    name,
                    {"name": name, "entry_count": 0, "last_updated": None},
                )
            )
        return result

    def add(self, name: str) -> None:
        normalized = self.normalize_name(name)
        domains = self.load_vocab()
        if normalized in domains:
            raise ConflictError(f"domain 已存在: {normalized}")
        domains.append(normalized)
        self.save_vocab(domains)

    def infer_domain(self, payload: dict[str, object]) -> str:
        domain = payload.get("domain")
        if isinstance(domain, str) and domain:
            return self.normalize_name(domain)
        known_domains: set[str] = set(self.load_vocab())
        tags = payload.get("tags", [])
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag in known_domains:
                    return tag
        return _UNSORTED_DOMAIN

    def load_vocab(self) -> list[str]:
        row = self._registry.conn.execute(
            "SELECT value FROM registry_meta WHERE key = 'domains'"
        ).fetchone()
        if row is None:
            return []
        try:
            raw = json.loads(cast(str, row["value"]))
        except json.JSONDecodeError as exc:
            raise StorageError("registry_meta.domains 不是合法 JSON") from exc
        if not isinstance(raw, list):
            raise StorageError("registry_meta.domains 必须是 JSON 数组")
        result: list[str] = []
        for item in raw:
            if isinstance(item, str) and item:
                result.append(to_nfc(item))
        return sorted(set(result))

    def save_vocab(self, domains: list[str]) -> None:
        serialized = json.dumps(sorted(set(domains)), ensure_ascii=False)
        self._registry.conn.execute(
            """
            INSERT INTO registry_meta(key, value)
            VALUES('domains', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (serialized,),
        )
        self._registry.commit()

    def normalize_name(self, name: str) -> str:
        normalized = to_nfc(name).strip()
        if not normalized:
            raise ValidationError("domain 名不能为空")
        if validate_explicit_slug(normalized) != normalized:
            raise ValidationError(f"domain 名含非法字符: {name}")
        return normalized
