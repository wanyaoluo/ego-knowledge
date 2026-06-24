"""jieba tokenizer wrapper with fallback to trigram."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import jieba  # type: ignore[import-untyped]

from .unicode_utils import to_nfc

if TYPE_CHECKING:
    from .registry import Registry

log = logging.getLogger(__name__)

_JIEBA_INITIALIZED = False
_LOADED_DICT_MARKERS: set[tuple[str, int]] = set()
_RUNTIME_WORDS: set[str] = set()
_DEFAULT_FREQ = 5

__all__ = [
    "init_jieba",
    "rebuild_custom_dict",
    "sync_runtime_words",
    "tokenize",
    "tokenize_cn",
]


def init_jieba(custom_dict_dir: Path | None = None) -> None:
    """Initialize jieba singleton with optional custom dictionaries."""

    global _JIEBA_INITIALIZED

    if not _JIEBA_INITIALIZED:
        jieba.initialize()
        _JIEBA_INITIALIZED = True

    if custom_dict_dir is not None:
        _load_custom_dicts(custom_dict_dir)


def rebuild_custom_dict(registry: Registry, output_dir: Path) -> None:
    """Extract aliases and tags into a jieba userdict file."""

    output_dir.mkdir(parents=True, exist_ok=True)
    dict_file = output_dir / "ek-auto.txt"
    words = sorted(
        {word for word in (*registry.all_aliases(), *registry.all_tags()) if _is_runtime_word(word)}
    )

    with dict_file.open("w", encoding="utf-8") as handle:
        for word in words:
            handle.write(f"{word} {_DEFAULT_FREQ}\n")

    init_jieba(output_dir)
    sync_runtime_words(words)


def sync_runtime_words(words: Iterable[str]) -> None:
    """Inject dynamic aliases/tags into the in-process jieba dictionary."""

    init_jieba()
    for word in words:
        normalized = _normalize_word(word)
        if normalized is None or normalized in _RUNTIME_WORDS:
            continue
        jieba.add_word(normalized, freq=_DEFAULT_FREQ)
        _RUNTIME_WORDS.add(normalized)


def tokenize(
    text: str,
    *,
    custom_dict_dir: Path | None = None,
    fallback_log_path: Path | None = None,
) -> list[str]:
    """Tokenize Chinese text with jieba and fallback to trigram when needed."""

    normalized = to_nfc(text).strip()
    if not normalized:
        return []

    init_jieba(custom_dict_dir)

    try:
        tokens = [token.strip() for token in jieba.cut(normalized) if token.strip()]
    except Exception:
        _append_fallback_record(fallback_log_path, normalized)
        return _trigram_fallback(normalized)

    if not tokens:
        _append_fallback_record(fallback_log_path, normalized)
        return _trigram_fallback(normalized)

    return [to_nfc(token) for token in tokens]


def tokenize_cn(
    text: str,
    *,
    custom_dict_dir: Path | None = None,
    fallback_log_path: Path | None = None,
) -> list[str]:
    """Compatibility alias for tokenize()."""

    return tokenize(
        text,
        custom_dict_dir=custom_dict_dir,
        fallback_log_path=fallback_log_path,
    )


def _trigram_fallback(text: str) -> list[str]:
    """Character trigrams for text too rare for jieba."""

    if len(text) < 3:
        return [text] if text else []
    return [text[index : index + 3] for index in range(len(text) - 2)]


def _load_custom_dicts(custom_dict_dir: Path) -> None:
    if not custom_dict_dir.exists():
        return

    for dict_file in sorted(custom_dict_dir.glob("*.txt")):
        try:
            marker = (str(dict_file.resolve()), dict_file.stat().st_mtime_ns)
        except OSError as exc:
            log.warning("读取 jieba 词典元数据失败 %s: %s", dict_file, exc)
            continue
        if marker in _LOADED_DICT_MARKERS:
            continue
        try:
            jieba.load_userdict(str(dict_file))
            _LOADED_DICT_MARKERS.add(marker)
        except Exception as exc:
            log.warning("加载 jieba 词典失败 %s: %s", dict_file, exc)


def _append_fallback_record(log_path: Path | None, token: str) -> None:
    if log_path is None:
        return

    log_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts_epoch": int(time.time()),
        "token": token,
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _normalize_word(word: str) -> str | None:
    normalized = to_nfc(word).strip()
    if not normalized:
        return None
    if any(char.isspace() for char in normalized):
        return None
    return normalized


def _is_runtime_word(word: str) -> bool:
    normalized = _normalize_word(word)
    return normalized is not None
