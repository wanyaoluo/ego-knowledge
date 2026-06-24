"""Unicode normalization utilities for EgoKnowledge."""

from __future__ import annotations

import unicodedata


def to_nfc(text: str) -> str:
    """Normalize text into NFC form."""

    return unicodedata.normalize("NFC", text)


def has_nfd_residual(text: str) -> bool:
    """Return True when NFC normalization would change the input."""

    return to_nfc(text) != text


def normalize_fullwidth(text: str) -> str:
    """Normalize fullwidth ASCII characters to their halfwidth equivalents.

    Uses NFKC normalization which converts fullwidth letters, digits, and
    punctuation to their ASCII counterparts.  This is idempotent — calling
    it on already-normalized text is a no-op.

    Note: NFKC compatibility decomposition also affects rare CJK
    compatibility ideographs (U+F900–FAFF → unified forms, e.g. 金→金)
    and circled/superscript digits (①→1).  Core CJK Unified Ideographs
    (U+4E00–9FFF) are unaffected.  This is acceptable for knowledge-base
    write/query paths where bilateral normalization guarantees consistency.
    """

    return unicodedata.normalize("NFKC", text)


_BODY_SPACING_MAP: dict[str, str] = {"\u3000": " "}


def normalize_body_spacing(text: str) -> str:
    """Convert fullwidth space (U+3000) to halfwidth space.

    Unlike ``normalize_fullwidth`` (NFKC), this preserves CJK fullwidth
    punctuation and fullwidth ASCII letters/digits — only the ideographic
    space is mapped, because it carries no semantic value in Markdown body.

    Idempotent: calling on already-normalized text is a no-op. Markdown
    structure awareness (code blocks, inline code) is the caller's
    responsibility; this function only performs character-level mapping.
    """

    if not text:
        return text
    return "".join(_BODY_SPACING_MAP.get(ch, ch) for ch in text)


def is_cjk_char(char: str) -> bool:
    """Return True when a character is a CJK ideograph."""

    if len(char) != 1:
        raise ValueError("is_cjk_char() expects a single character")

    codepoint = ord(char)
    return (
        0x4E00 <= codepoint <= 0x9FFF
        or 0x3400 <= codepoint <= 0x4DBF
        or 0x20000 <= codepoint <= 0x2EBEF
        or 0xF900 <= codepoint <= 0xFAFF
    )
