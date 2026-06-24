"""Query preprocessing for EgoKnowledge retrieval backends."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .unicode_utils import is_cjk_char, normalize_fullwidth, to_nfc

_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)+$")
_SYMBOL_CHARS = set("+-./#$%^&*_=")
_ALNUM_RUN_RE = re.compile(r"[a-zA-Z0-9]+")


class SegmentType(StrEnum):
    CJK = "cjk"
    ASCII_WORD = "ascii"
    ASCII_SHORT = "ascii_short"
    SYMBOL_TOKEN = "symbol"
    VERSION = "version"
    NUMBER = "number"
    MIXED = "mixed"
    FULLWIDTH = "fullwidth"
    EMOJI = "emoji"


@dataclass(slots=True)
class Segment:
    type: SegmentType
    text: str


def parse_query(query: str) -> list[Segment]:
    """Split a query into typed search segments."""

    normalized = normalize_fullwidth(to_nfc(query).strip())
    if not normalized:
        return []

    segments: list[Segment] = []
    for chunk in re.split(r"\s+", normalized):
        if not chunk:
            continue
        for token in _split_chunk(chunk):
            if token:
                segments.append(_classify(token))
    return segments


def _split_chunk(chunk: str) -> list[str]:
    parts: list[str] = []
    current = ""
    previous_bucket: str | None = None

    for char in chunk:
        bucket = "cjk" if is_cjk_char(char) else "other"
        if previous_bucket is None or bucket == previous_bucket:
            current += char
        else:
            parts.append(current)
            current = char
        previous_bucket = bucket

    if current:
        parts.append(current)
    return parts


def _classify(token: str) -> Segment:
    compat = normalize_fullwidth(token)

    if any(_is_emoji(char) for char in token):
        return Segment(SegmentType.EMOJI, token)
    if any(0xFF00 <= ord(char) <= 0xFFEF for char in token):
        return Segment(SegmentType.FULLWIDTH, token)
    if _VERSION_RE.fullmatch(compat):
        return Segment(SegmentType.VERSION, token)
    if compat.isdigit():
        return Segment(SegmentType.NUMBER, token)
    if any(is_cjk_char(char) for char in token):
        return Segment(SegmentType.CJK, token)

    has_ascii_alpha = any(char.isascii() and char.isalpha() for char in compat)
    has_digit = any(char.isdigit() for char in compat)
    has_symbol = any(char in _SYMBOL_CHARS for char in compat)
    ascii_letters = "".join(char for char in compat if char.isascii() and char.isalpha())

    if has_ascii_alpha and has_digit:
        return Segment(SegmentType.MIXED, token)
    if has_symbol and has_ascii_alpha:
        return Segment(SegmentType.SYMBOL_TOKEN, token)
    if has_symbol:
        return Segment(SegmentType.SYMBOL_TOKEN, token)
    if ascii_letters:
        segment_type = SegmentType.ASCII_SHORT if len(ascii_letters) < 3 else SegmentType.ASCII_WORD
        return Segment(segment_type, token)
    return Segment(SegmentType.CJK, token)


def _expand_mixed_segments(segments: list[Segment]) -> list[Segment]:
    """Expand MIXED segments into independent alphanumeric sub-segments."""

    expanded: list[Segment] = []
    for seg in segments:
        if seg.type == SegmentType.MIXED:
            sub_parts = _ALNUM_RUN_RE.findall(seg.text)
            if len(sub_parts) > 1:
                for part in sub_parts:
                    expanded.append(_classify(part))
            else:
                expanded.append(seg)
        else:
            expanded.append(seg)
    return expanded


def _generate_symbol_variants(token: str) -> list[str]:
    """Generate symbol-stripped search variants for SYMBOL_TOKEN."""

    parts = _ALNUM_RUN_RE.findall(token)
    if not parts:
        return []
    if len(parts) == 1 and parts[0] == token:
        return []
    return parts


def _quote_fts(text: str) -> str:
    escaped = text.replace('"', '""')
    return f'"{escaped}"'


def _ascii_match_expr(text: str) -> str:
    normalized = normalize_fullwidth(text)
    ascii_text = "".join(char if char.isascii() else " " for char in normalized)
    collapsed = " ".join(part for part in ascii_text.split() if part)
    if not collapsed:
        return ""
    if " " not in collapsed:
        return _quote_fts(collapsed)
    return " AND ".join(_quote_fts(part) for part in collapsed.split())


def _is_emoji(char: str) -> bool:
    codepoint = ord(char)
    return (0x1F300 <= codepoint <= 0x1FAFF) or (0x2600 <= codepoint <= 0x27BF)
