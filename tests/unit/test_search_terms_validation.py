from __future__ import annotations

import pytest

from ego_knowledge.errors import ValidationError


def test_search_terms_require_chinese_bucket(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="缺少中文主术语"):
        fresh_ek._validate_search_terms(
            ["single source", "ssot", "", "governance", "alias"],
            "单一真源",
        )


def test_search_terms_require_english_bucket_or_placeholder(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="缺少英文术语或缩写"):
        fresh_ek._validate_search_terms(
            ["单一真源", "唯一真源", "知识治理", "真源体系", "别名"],
            "单一真源",
        )


def test_search_terms_require_alias_bucket_even_with_empty_placeholder(fresh_ek) -> None:
    with pytest.raises(ValidationError, match="缺少常见别称或误写"):
        fresh_ek._validate_search_terms(["单一真源", "", "单一", "真源", "单一真源"], "单一真源")


def test_search_terms_accept_empty_string_as_english_placeholder(fresh_ek) -> None:
    fresh_ek._validate_search_terms(["单一真源", "", "知识治理", "唯一真源", "ssot"], "单一真源")


def test_search_terms_title_substring_not_counted_as_alias(fresh_ek) -> None:
    """标题包含关系不算别称——term 是 title 的子串不满足 alias 桶。"""
    with pytest.raises(ValidationError, match="缺少常见别称或误写"):
        fresh_ek._validate_search_terms(
            ["知识图谱", "", "知识", "图谱", "知识图谱"],
            "知识图谱",
        )


def test_search_terms_count_met_but_all_empty_still_fails(fresh_ek) -> None:
    """term 为空但总数达标仍按桶判断——5 个空字符串不满足中文和别称桶。"""
    with pytest.raises(ValidationError, match="缺少中文主术语"):
        fresh_ek._validate_search_terms(["", "", "", "", ""], "测试标题")


def test_search_terms_error_message_lists_all_missing_buckets(fresh_ek) -> None:
    """当多个桶同时缺失时，错误信息必须列出所有缺失桶名。"""
    with pytest.raises(ValidationError) as exc_info:
        fresh_ek._validate_search_terms(["", "", "", "", ""], "测试标题")
    msg = str(exc_info.value)
    # 空字符串满足英文桶占位，但缺少中文和别称
    assert "缺少中文主术语" in msg
    assert "缺少常见别称或误写" in msg


def test_search_terms_happy_path_all_three_buckets(fresh_ek) -> None:
    """三桶全覆盖 + 总数 >= 5 应成功。"""
    fresh_ek._validate_search_terms(
        ["知识图谱", "knowledge-graph", "KG", "语义网络", "知识网络"],
        "知识图谱",
    )
