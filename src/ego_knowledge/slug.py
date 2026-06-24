"""Slug generation for EgoKnowledge entries."""

from __future__ import annotations

import re

from .unicode_utils import is_cjk_char, to_nfc

TERM_MAP: dict[str, str] = {
    "C++": "cpp",
    "C#": "csharp",
    ".NET": "dotnet",
    "Node.js": "nodejs",
    "F#": "fsharp",
    "T-SQL": "tsql",
    "C/C++": "c-cpp",
}

MAX_SLUG_LEN = 40
_MULTI_DASH_RE = re.compile(r"-+")


def _is_allowed_char(char: str) -> bool:
    """True when a character can be preserved in a slug."""

    return is_cjk_char(char) or (char.isascii() and (char.isalnum() or char == "-"))


def _normalize_terms(text: str) -> str:
    """Apply technical term substitutions before generic replacement."""

    normalized = text
    for term, replacement in sorted(TERM_MAP.items(), key=lambda item: len(item[0]), reverse=True):
        normalized = normalized.replace(term, replacement)
    return normalized


def generate_slug(title: str) -> str:
    """Generate a Chinese-first slug from a title."""

    normalized = _normalize_terms(to_nfc(title))
    slug_chars = [char if _is_allowed_char(char) else "-" for char in normalized]
    slug = _MULTI_DASH_RE.sub("-", "".join(slug_chars)).strip("-")

    if len(slug) > MAX_SLUG_LEN:
        slug = slug[:MAX_SLUG_LEN].rstrip("-")

    if not slug:
        raise ValueError(f"Empty slug after normalization from title: {title!r}")

    return slug
