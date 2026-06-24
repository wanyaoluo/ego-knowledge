"""Mutation services for file rewrites and entry moves."""

from __future__ import annotations

import datetime as _dt
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ._entry_store import STABLE_SLUG_KINDS, EntryStore, validate_explicit_slug
from ._validation import ensure_reference_targets
from .errors import ConflictError, NotFoundError, StorageError, ValidationError
from .frontmatter import _fm_to_entry, read_file, write_file
from .models import Entry, entry_to_frontmatter
from .paths import file_path_of
from .registry import Registry

MoveFile = Callable[[Path, Path], None]

_RELATION_LINK_RE = re.compile(r"(?P<prefix>\[[^\]]+\]\()(?P<target>[^)]+)(?P<suffix>\))")


@dataclass(slots=True)
class PrimaryUpdate:
    old_path: Path | None
    new_path: Path
    entry: Entry
    body: str


@dataclass(slots=True)
class FileMutation:
    old_path: Path | None
    new_path: Path
    entry: Entry
    frontmatter: dict[str, object]
    body: str


class MutationService:
    """Batch path mutation planner and executor."""

    def __init__(
        self,
        data_root: Path,
        registry: Registry,
        entries: EntryStore,
        *,
        move_file: MoveFile,
    ) -> None:
        self._data_root = data_root
        self._registry = registry
        self._entries = entries
        self._move_file = move_file

    def rename(self, id: str, new_slug: str) -> Entry:
        entry = self._entries.load(id)
        if entry.kind not in STABLE_SLUG_KINDS:
            raise ValidationError("只有 concept/dossier/decision 支持 rename()")
        normalized_slug = validate_explicit_slug(new_slug)
        old_path = file_path_of(entry)
        new_path = old_path.with_name(f"{normalized_slug}.md")
        if new_path != old_path and new_path.exists():
            raise ConflictError(f"slug 冲突：{normalized_slug}")

        entry.slug = normalized_slug
        entry.updated_at = _dt.date.today()
        self.apply_primary_updates(
            [
                PrimaryUpdate(
                    old_path=old_path,
                    new_path=new_path,
                    entry=entry,
                    body=entry.body or "",
                )
            ]
        )
        return self._entries.enrich_runtime_meta(entry)

    def apply_moved_entry(
        self,
        old_path: Path,
        new_path: Path,
        entry: Entry,
        body: str,
    ) -> None:
        self.apply_primary_updates(
            [PrimaryUpdate(old_path=old_path, new_path=new_path, entry=entry, body=body)]
        )

    def apply_primary_updates(self, updates: list[PrimaryUpdate]) -> list[FileMutation]:
        mutations = self.plan_mutations(updates)
        self.apply_mutations(mutations)
        return mutations

    def plan_mutations(self, updates: list[PrimaryUpdate]) -> list[FileMutation]:
        move_map: dict[Path, Path] = {
            update.old_path.resolve(strict=False): update.new_path.resolve(strict=False)
            for update in updates
            if update.old_path is not None and update.old_path != update.new_path
        }
        update_paths = {
            update.old_path.resolve(strict=False)
            for update in updates
            if update.old_path is not None
        } | {update.new_path.resolve(strict=False) for update in updates}
        mutations: list[FileMutation] = []

        for update in updates:
            source_path = update.old_path or update.new_path
            rewritten_body = self._rewrite_body_links(
                update.body,
                current_path=source_path,
                new_current_path=update.new_path,
                move_map=move_map,
            )
            mutations.append(
                FileMutation(
                    old_path=update.old_path,
                    new_path=update.new_path,
                    entry=update.entry,
                    frontmatter=entry_to_frontmatter(update.entry),
                    body=rewritten_body,
                )
            )

        for path in self._collect_markdown_files():
            resolved = path.resolve(strict=False)
            if resolved in update_paths:
                continue
            frontmatter, body = read_file(str(path))
            rewritten_body = self._rewrite_body_links(
                body,
                current_path=path,
                new_current_path=path,
                move_map=move_map,
            )
            if rewritten_body == body:
                continue
            entry = _fm_to_entry(frontmatter, file_path=str(path), body=rewritten_body)
            mutations.append(
                FileMutation(
                    old_path=path,
                    new_path=path,
                    entry=entry,
                    frontmatter=entry_to_frontmatter(entry),
                    body=rewritten_body,
                )
            )

        mutations.sort(key=lambda item: (item.old_path is not None, item.new_path.as_posix()))
        return mutations

    def apply_mutations(self, mutations: list[FileMutation]) -> None:
        if not mutations:
            return
        existing_old_paths = {
            mutation.old_path.resolve(strict=False)
            for mutation in mutations
            if mutation.old_path is not None
        }
        for mutation in mutations:
            if mutation.old_path != mutation.new_path and mutation.new_path.exists():
                if mutation.new_path.resolve(strict=False) not in existing_old_paths:
                    raise ConflictError(f"目标路径已存在: {mutation.new_path}")

        allowed_ids = {mutation.entry.id for mutation in mutations}
        for mutation in mutations:
            ensure_reference_targets(self._registry, mutation.entry, extra_ids=allowed_ids)

        backups: dict[Path, str] = {}
        cursor = self._registry.conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE")
            for mutation in mutations:
                if mutation.old_path is not None and mutation.old_path not in backups:
                    backups[mutation.old_path] = mutation.old_path.read_text(encoding="utf-8")
                mutation.new_path.parent.mkdir(parents=True, exist_ok=True)
                if mutation.old_path is not None and mutation.old_path != mutation.new_path:
                    self._move_file(mutation.old_path, mutation.new_path)
                write_file(str(mutation.new_path), mutation.frontmatter, mutation.body)
                self._registry.upsert_entry(mutation.entry, mutation.new_path, mutation.body)
            self._registry.conn.commit()
        except Exception as exc:
            self._registry.conn.rollback()
            self._restore_mutations(mutations, backups)
            if isinstance(exc, (ValidationError, ConflictError, NotFoundError, StorageError)):
                raise
            if isinstance(exc, OSError):
                raise StorageError(f"批量文件操作失败: {exc}") from exc
            raise StorageError(f"批量事务失败: {exc}") from exc
        finally:
            cursor.close()

    def _restore_mutations(self, mutations: list[FileMutation], backups: dict[Path, str]) -> None:
        for mutation in reversed(mutations):
            if mutation.old_path is None:
                mutation.new_path.unlink(missing_ok=True)
                continue
            original = backups.get(mutation.old_path)
            if original is None:
                continue
            mutation.old_path.parent.mkdir(parents=True, exist_ok=True)
            mutation.old_path.write_text(original, encoding="utf-8")
            if mutation.old_path != mutation.new_path:
                mutation.new_path.unlink(missing_ok=True)

    def _collect_markdown_files(self) -> list[Path]:
        return sorted(self._data_root.rglob("*.md"), key=lambda path: path.as_posix())

    def _rewrite_body_links(
        self,
        body: str,
        *,
        current_path: Path,
        new_current_path: Path,
        move_map: dict[Path, Path],
    ) -> str:
        current_base = current_path.resolve(strict=False).parent
        new_base = new_current_path.resolve(strict=False).parent

        def replace(match: re.Match[str]) -> str:
            raw_target = match.group("target").strip()
            if not raw_target:
                return match.group(0)
            wrapped = raw_target.startswith("<") and raw_target.endswith(">")
            target_value = raw_target[1:-1] if wrapped else raw_target
            path_part, fragment = self._split_fragment(target_value)
            if not path_part or self._is_external_link(path_part):
                return match.group(0)

            absolute_target = (current_base / path_part).resolve(strict=False)
            moved_current = current_base != new_base
            if absolute_target in move_map:
                absolute_target = move_map[absolute_target]
            elif not moved_current:
                return match.group(0)

            relative_target = os.path.relpath(absolute_target, start=new_base).replace(os.sep, "/")
            if fragment:
                relative_target = f"{relative_target}#{fragment}"
            if wrapped:
                relative_target = f"<{relative_target}>"
            return f"{match.group('prefix')}{relative_target}{match.group('suffix')}"

        return _RELATION_LINK_RE.sub(replace, body)

    def _split_fragment(self, target: str) -> tuple[str, str | None]:
        if "#" not in target:
            return target, None
        path_part, fragment = target.split("#", 1)
        return path_part, fragment

    def _is_external_link(self, target: str) -> bool:
        return target.startswith(("http://", "https://", "mailto:", "#", "/")) or "://" in target
