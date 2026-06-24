from __future__ import annotations

import pytest

from ego_knowledge.unicode_utils import (
    has_nfd_residual,
    is_cjk_char,
    normalize_body_spacing,
    to_nfc,
)


def test_to_nfc_and_residual_detection() -> None:
    decomposed = "Cafe\u0301"
    assert to_nfc(decomposed) == "Café"
    assert has_nfd_residual(decomposed)
    assert not has_nfd_residual("著")


def test_is_cjk_char_detection() -> None:
    assert is_cjk_char("中")
    assert is_cjk_char("𠀀")
    assert not is_cjk_char("a")
    assert not is_cjk_char("あ")
    assert not is_cjk_char("한")


def test_is_cjk_char_rejects_multi_char_input() -> None:
    with pytest.raises(ValueError, match="single character"):
        is_cjk_char("中文")


def test_normalize_body_spacing_converts_fullwidth_space():
    assert normalize_body_spacing("全角\u3000空格") == "全角 空格"


def test_normalize_body_spacing_preserves_cjk_punctuation():
    text = "中文标点，。：；！？""''应保留"
    assert normalize_body_spacing(text) == text


def test_normalize_body_spacing_preserves_fullwidth_letters():
    # 不做 NFKC，全角字母保留
    assert normalize_body_spacing("ＡＢＣ") == "ＡＢＣ"


def test_normalize_body_spacing_empty():
    assert normalize_body_spacing("") == ""


def test_normalize_body_spacing_idempotent():
    text = "混合\u3000内容，。"
    once = normalize_body_spacing(text)
    twice = normalize_body_spacing(once)
    assert once == twice
