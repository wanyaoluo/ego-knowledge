"""Frontmatter read/write helpers for Markdown entries."""

from __future__ import annotations

import re
from enum import Enum
from typing import cast

import yaml  # type: ignore[import-untyped]

from .errors import StorageError, ValidationError
from .models import Entry
from .unicode_utils import normalize_body_spacing, to_nfc

FRONTMATTER_BOUNDARY = "---\n"
_RUNTIME_META_FIELDS = frozenset({"file_path", "body", "metrics"})

# Code-span guard for body normalization (spec 决策 1).
# Matches standard fenced code blocks (```…```) and inline code (`…`).
# Inline code is constrained to a single line to avoid greedy cross-paragraph
# matches; fenced blocks may span multiple lines via [\s\S]*?.
_CODE_BLOCK_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]+`)", re.MULTILINE)

FULLWIDTH_TO_HALFWIDTH: dict[str, str] = {
    "\u3000": " ",
    "\uff1a": ":",
    "\uff0c": ",",
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
}


def _normalize_value(value: object) -> object:
    """Recursively normalize strings to NFC and unwrap Enum to plain str."""

    # Enum 要放在 str 检查前：StrEnum 实例同时是 str 子类，但 yaml.safe_dump
    # 没有 Enum representer，必须转成裸 str
    if isinstance(value, Enum):
        inner = value.value
        return to_nfc(inner) if isinstance(inner, str) else inner
    if isinstance(value, str):
        return to_nfc(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized[to_nfc(str(key))] = _normalize_value(item)
        return normalized
    return value


def _normalize_payload_map(payload: dict[str, object]) -> dict[str, object]:
    normalized = _normalize_value(payload)
    if not isinstance(normalized, dict):
        raise ValidationError("payload 必须是对象")
    return cast(dict[str, object], normalized)


def _extract_body(payload: dict[str, object]) -> str:
    """Extract and layer-normalize the body field of a payload.

    Pipeline (spec 决策 1): NFC → split by code spans → normalize only
    non-code parts via ``normalize_body_spacing`` → re-assemble. Code
    spans (fenced blocks and inline code) are passed through unchanged
    so that U+3000 inside them is preserved.
    """

    raw = payload.get("body", "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValidationError("body 必须是字符串")
    text = to_nfc(raw)
    parts = _CODE_BLOCK_RE.split(text)
    result: list[str] = []
    for part in parts:
        if part and part.startswith("`"):
            result.append(part)  # 代码块/行内代码保持原样
        else:
            result.append(normalize_body_spacing(part))
    return "".join(result)


def _fm_to_entry(
    fm: dict[str, object],
    *,
    file_path: str | None = None,
    body: str | None = None,
    metrics: dict[str, object] | None = None,
) -> Entry:
    from ._serde import _entry_from_frontmatter

    clean = {key: value for key, value in fm.items() if key not in {"file_path", "body", "metrics"}}
    return _entry_from_frontmatter(
        clean,
        file_path=file_path,
        body=body,
        metrics=metrics,
    )


def _fix_fullwidth_punctuation(fm_raw: str) -> str:
    """Fix fullwidth punctuation in frontmatter by mapping to halfwidth.

    Replaces the former reject-only ``_check_fullwidth_punctuation``.
    Returns the fixed string; caller proceeds to YAML parse.
    """

    fixed = fm_raw
    for fullwidth, halfwidth in FULLWIDTH_TO_HALFWIDTH.items():
        fixed = fixed.replace(fullwidth, halfwidth)
    return fixed


def _load_frontmatter(fm_raw: str, path: str) -> dict[str, object]:
    """Parse YAML frontmatter into a normalized mapping."""

    fm_raw = _fix_fullwidth_punctuation(fm_raw)
    try:
        loaded: object = yaml.safe_load(fm_raw)
    except yaml.YAMLError as exc:
        raise ValidationError(f"文件 {path} YAML 解析失败: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ValidationError(f"文件 {path} frontmatter 不是字典类型")

    normalized = cast(dict[object, object], loaded)
    result: dict[str, object] = {}
    for key, value in normalized.items():
        if not isinstance(key, str):
            raise ValidationError(f"文件 {path} frontmatter 键必须是字符串: {key!r}")
        result[to_nfc(key)] = _normalize_value(value)
    return result


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Split ``text`` into ``(frontmatter_raw, body)`` on ``FRONTMATTER_BOUNDARY``.

    Returns ``(frontmatter_raw, body)`` where ``frontmatter_raw`` is the YAML
    segment between the two ``---\\n`` boundaries (boundaries excluded) and
    ``body`` is the trailing segment **as-is**. Returns ``None`` when:

    - ``text`` does not start with ``FRONTMATTER_BOUNDARY`` (no opening
      marker), or
    - ``text`` cannot be split into exactly three segments on
      ``FRONTMATTER_BOUNDARY`` (missing closing marker).

    Body is intentionally not leading-newline stripped: ``read_file`` strips
    for its public contract, while scan callers (``normalize_legacy``,
    ``cleanup_broken_relations``) need the raw segment to round-trip write-back
    verbatim. ``read_file`` raises structured ``ValidationError`` for the same
    two failure conditions; scan callers treat ``None`` as skip-and-continue.
    The helper itself stays tolerant on purpose.
    """

    if not text.startswith(FRONTMATTER_BOUNDARY):
        return None
    parts = text.split(FRONTMATTER_BOUNDARY, 2)
    if len(parts) != 3:
        return None
    _, frontmatter_raw, body = parts
    return frontmatter_raw, body


def read_file(path: str) -> tuple[dict[str, object], str]:
    """Read a Markdown file and return normalized frontmatter and body."""

    try:
        with open(path, encoding="utf-8") as handle:
            content = to_nfc(handle.read())
    except OSError as exc:
        raise StorageError(f"无法读取文件 {path}: {exc}") from exc

    if not content.startswith(FRONTMATTER_BOUNDARY):
        raise ValidationError(f"文件 {path} 缺少 frontmatter 开始标记 '---'")

    parsed = split_frontmatter(content)
    if parsed is None:
        raise ValidationError(f"文件 {path} frontmatter 格式不完整")

    fm_raw, body = parsed
    frontmatter = _load_frontmatter(fm_raw, path)
    return frontmatter, body.lstrip("\n")


def write_file(path: str, frontmatter: dict[str, object], body: str) -> None:
    """Write frontmatter and body back to disk."""

    content = _render_markdown(frontmatter, body)
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
    except OSError as exc:
        raise StorageError(f"无法写入文件 {path}: {exc}") from exc


def _render_markdown(frontmatter: dict[str, object], body: str) -> str:
    filtered_frontmatter = {
        to_nfc(key): _normalize_value(value)
        for key, value in frontmatter.items()
        if key not in _RUNTIME_META_FIELDS
    }
    normalized_body = to_nfc(body)
    try:
        fm_raw = yaml.safe_dump(
            filtered_frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except yaml.YAMLError as exc:
        raise StorageError(f"frontmatter 序列化失败: {exc}") from exc
    return f"{FRONTMATTER_BOUNDARY}{fm_raw}{FRONTMATTER_BOUNDARY}\n{normalized_body}"
