"""Shared helpers for doctor checks and main shell."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import cast

import yaml  # type: ignore[import-untyped]

from ..unicode_utils import is_cjk_char

_FRONTMATTER_FIELDS: tuple[str, ...] = (
    "title",
    "aliases",
    "tags",
    "search_terms",
    "slug",
    "domain",
)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _to_naive_utc(dt: _dt.datetime) -> _dt.datetime:
    """Convert any datetime to naive UTC for safe comparison."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_dt.UTC).replace(tzinfo=None)
    return dt


def _iter_all_entry_files(data_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in (data_root / "entries", data_root / "sources"):
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.md"), key=lambda item: item.as_posix()))
    return files


def _read_raw_markdown(path: Path) -> tuple[dict[str, object], str]:
    from ..frontmatter import FRONTMATTER_BOUNDARY

    raw = path.read_text(encoding="utf-8")
    if not raw.startswith(FRONTMATTER_BOUNDARY):
        return {}, raw
    parts = raw.split(FRONTMATTER_BOUNDARY, 2)
    if len(parts) != 3:
        return {}, raw
    _, fm_raw, body = parts
    try:
        loaded = yaml.safe_load(fm_raw)
    except yaml.YAMLError:
        return {}, body
    if not isinstance(loaded, dict):
        return {}, body
    return cast(dict[str, object], loaded), body.lstrip("\n")


def _coerce_string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _has_cjk_and_ascii(values: set[str]) -> bool:
    has_cjk = any(any(is_cjk_char(char) for char in item) for item in values)
    has_ascii = any(any(char.isascii() and char.isalpha() for char in item) for item in values)
    return has_cjk and has_ascii


def _now_epoch() -> int:
    from time import time

    return int(time())
