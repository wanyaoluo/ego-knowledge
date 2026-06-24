from __future__ import annotations

import pytest

from ego_knowledge.slug import generate_slug


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("单一真源", "单一真源"),
        ("C++ 内存管理", "cpp-内存管理"),
        ("Node.js 异步编程", "nodejs-异步编程"),
        (".NET Core 性能", "dotnet-Core-性能"),
        ("RAG (Retrieval-Augmented Generation)", "RAG-Retrieval-Augmented-Generation"),
        ("A" * 50, "A" * 40),
        ("中" * 50, "中" * 40),
        ("test--double--dash", "test-double-dash"),
        ("  leading trailing  ", "leading-trailing"),
        ("C/C++ 工程化", "c-cpp-工程化"),
    ],
)
def test_generate_slug(title: str, expected: str) -> None:
    assert generate_slug(title) == expected


def test_empty_slug_raises() -> None:
    with pytest.raises(ValueError, match="Empty slug"):
        generate_slug("!!!")
