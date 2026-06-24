"""Tests for Phase 2 validation hardening (F6 + F5 body floor) + source_url whitelist."""

from __future__ import annotations

import pytest

from ego_knowledge._validation import (
    _validate_body_length,
    _validate_source_url,
    validate_search_terms,
)
from ego_knowledge.errors import ValidationError
from ego_knowledge.models import Kind

# ---------------------------------------------------------------------------
# F6.1 search_terms 单项长度上限
# ---------------------------------------------------------------------------


class TestSearchTermsHardening:
    """F6 三项新校验：单项长度、空白拦截、NFC 去重。"""

    def test_oversize_term_rejected(self) -> None:
        terms = ["合理词", "ok term", "alias-1", "alias-2", "x" * 41]
        with pytest.raises(ValidationError, match="单项超长"):
            validate_search_terms(terms, "test")

    def test_boundary_40_chars_accepted(self) -> None:
        terms = ["合理词", "ok term", "alias-1", "alias-2", "x" * 40]
        validate_search_terms(terms, "test")  # 不抛

    def test_whitespace_only_rejected(self) -> None:
        terms = ["合理词", "ok term", "   ", "alias-1", "alias-2"]
        with pytest.raises(ValidationError, match="纯空白项"):
            validate_search_terms(terms, "test")

    def test_multiple_empty_rejected(self) -> None:
        terms = ["合理词", "", "", "alias-1", "alias-2"]
        with pytest.raises(ValidationError, match="多个空字符串"):
            validate_search_terms(terms, "test")

    def test_single_empty_accepted(self) -> None:
        terms = ["合理词", "", "alias-1", "alias-2", "其他"]
        validate_search_terms(terms, "test")  # 不抛

    def test_nfc_duplicate_rejected(self) -> None:
        # café NFD vs NFC: 组合字符 vs 预组合字符
        # 加中文词"测试"以满足三桶（中文/英文/别称）前置检查，让执行流到达 F6.3 NFC 去重
        terms = ["caf\u00e9", "cafe\u0301", "alias-1", "alias-2", "测试"]
        with pytest.raises(ValidationError, match="NFC 等价重复"):
            validate_search_terms(terms, "test")


# ---------------------------------------------------------------------------
# F5 body floor (护栏④)
# ---------------------------------------------------------------------------


class TestBodyFloor:
    """_validate_body_length 校验。"""

    def test_body_49_chars_rejected(self) -> None:
        with pytest.raises(ValidationError, match="body 过短"):
            _validate_body_length(Kind.CONCEPT, "x" * 49)

    def test_body_50_chars_accepted(self) -> None:
        _validate_body_length(Kind.CONCEPT, "x" * 50)  # 不抛

    def test_source_body_empty_exempt(self) -> None:
        _validate_body_length(Kind.SOURCE, "")  # 豁免，不抛

    def test_body_whitespace_only_rejected(self) -> None:
        """纯空白 body strip 后长度为 0，应被拒绝。"""
        with pytest.raises(ValidationError, match="body 过短"):
            _validate_body_length(Kind.CONCEPT, "   \n\t   ")


# ---------------------------------------------------------------------------
# source_url 白名单硬闸
# ---------------------------------------------------------------------------


class TestValidateSourceUrl:
    """_validate_source_url 白名单前缀硬闸测试。"""

    # --- 合法前缀放行 ---

    def test_https_prefix_accepted(self) -> None:
        _validate_source_url("https://arxiv.org/pdf/2401.0001")  # 不抛

    def test_http_prefix_accepted(self) -> None:
        _validate_source_url("http://internal.doc/README")  # 不抛

    def test_knowledge_scheme_accepted(self) -> None:
        _validate_source_url("knowledge://papers/survey-2026.pdf")  # 不抛

    def test_empty_string_accepted(self) -> None:
        """空字符串由 schema minLength 约束处理，此处早返回放行。"""
        _validate_source_url("")  # 不抛

    # --- 非法前缀拒绝 ---

    def test_local_scheme_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不在白名单内"):
            _validate_source_url("local://something")

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不在白名单内"):
            _validate_source_url("/tmp/bad.pdf")

    def test_relative_path_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不在白名单内"):
            _validate_source_url("tmp/x.md")

    def test_whitespace_only_rejected(self) -> None:
        with pytest.raises(ValidationError, match="不在白名单内"):
            _validate_source_url("   ")

    # --- 边界 ---

    def test_data_relative_path_rejected(self) -> None:
        """data/ 相对路径不是公开 source_url scheme，应拒绝。"""
        with pytest.raises(ValidationError, match="不在白名单内"):
            _validate_source_url("data/other-project/file.md")

    def test_https_case_sensitive_rejected(self) -> None:
        """HTTPS:// 大写不应放行（startswith 区分大小写）。"""
        with pytest.raises(ValidationError, match="不在白名单内"):
            _validate_source_url("HTTPS://example.com")
