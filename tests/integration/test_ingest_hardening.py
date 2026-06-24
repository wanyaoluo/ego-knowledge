"""Phase 5.5 ingest 链路加固——端到端集成测试。

覆盖四道护栏（slug 冲突拒绝 / search_terms 长度 / body 底线 / 合法通过）
通过 MCP stdio 真实子进程验证。
"""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from tests.integration.conftest import MCPServerClient
from tests.unit.support import concept_payload, note_payload, source_payload

# ---------------------------------------------------------------------------
# Fixture：预置 source
# ---------------------------------------------------------------------------


@pytest.fixture()
def preseed_source(mcp_server: MCPServerClient) -> dict:
    return mcp_server.call(
        "ek_ingest",
        {
            "kind": "source",
            "payload": source_payload(
                title="ingest-hardening-source",
                source_type="doc",
                source_url="https://example.com/hardening-source",
                captured_at="2026-04-10",
                content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1",
                search_terms=[
                    "ingest-hardening-source",
                    "hardening",
                    "加固来源",
                    "测试来源",
                    "source-ht",
                ],
            ),
            "conflict_policy": "strict",
        },
    )


# ---------------------------------------------------------------------------
# 1. MCP ek_ingest slug 冲突拒绝
# ---------------------------------------------------------------------------


def test_ingest_slug_conflict_rejected(mcp_server: MCPServerClient, preseed_source: dict) -> None:
    """同名 slug 的 concept 二次入库应被护栏①拦截。"""
    mcp_server.call(
        "ek_ingest",
        {
            "kind": "concept",
            "payload": concept_payload(
                preseed_source["id"],
                title="slug-冲突测试",
                search_terms=["slug-冲突测试", "slug-conflict", "冲突测试", "slug-test", "ht-sc"],
            ),
            "conflict_policy": "strict",
        },
    )

    # 用 allow 也无法绕过 slug 冲突（护栏①）
    with pytest.raises(ToolError) as exc_info:
        mcp_server.call(
            "ek_ingest",
            {
                "kind": "concept",
                "payload": concept_payload(
                    preseed_source["id"],
                    title="slug-冲突测试",
                    search_terms=[
                        "slug-冲突测试",
                        "slug-conflict-dup",
                        "冲突测试副本",
                        "slug-dup",
                        "ht-sc2",
                    ],
                ),
                "conflict_policy": "allow",
            },
        )

    payload = json.loads(exc_info.value.args[0])
    assert payload["error_type"] == "conflict_error"
    assert "slug" in payload["message"]


# ---------------------------------------------------------------------------
# 2. MCP ek_ingest body 过短拒绝
# ---------------------------------------------------------------------------


def test_ingest_body_too_short_rejected(mcp_server: MCPServerClient, preseed_source: dict) -> None:
    """非 source 类 body 不足 50 字符应被护栏④拦截。"""
    with pytest.raises(ToolError) as exc_info:
        mcp_server.call(
            "ek_ingest",
            {
                "kind": "concept",
                "payload": {
                    **concept_payload(
                        preseed_source["id"],
                        title="body-过短测试",
                        search_terms=[
                            "body-过短测试",
                            "body-short",
                            "过短测试",
                            "body-test",
                            "ht-bs",
                        ],
                    ),
                    "body": "太短了",
                },
                "conflict_policy": "strict",
            },
        )

    payload = json.loads(exc_info.value.args[0])
    assert payload["error_type"] == "validation_error"
    assert "body" in payload["message"]
    assert "过短" in payload["message"]


# ---------------------------------------------------------------------------
# 3. MCP ek_ingest search_terms 超长拒绝
# ---------------------------------------------------------------------------


def test_ingest_search_terms_oversize_rejected(
    mcp_server: MCPServerClient, preseed_source: dict
) -> None:
    """search_terms 单项超过 40 字符应被护栏③拦截。"""
    oversize_term = "这是一个故意超过四十个字符长度的搜索词条专门用来测试ingest链路加固后的长度限制"
    with pytest.raises(ToolError) as exc_info:
        mcp_server.call(
            "ek_ingest",
            {
                "kind": "note",
                "payload": note_payload(
                    preseed_source["id"],
                    title="terms-超长测试",
                    body="# terms 超长测试\n\n" + "x" * 50,
                    search_terms=[
                        oversize_term,
                        "terms-oversize",
                        "超长测试",
                        "terms-test",
                        "ht-to",
                    ],
                ),
                "conflict_policy": "strict",
            },
        )

    payload = json.loads(exc_info.value.args[0])
    assert payload["error_type"] == "validation_error"
    assert "search_terms" in payload["message"]
    assert "超长" in payload["message"]


# ---------------------------------------------------------------------------
# 4. MCP ek_ingest 合法 payload 通过（四道护栏不误杀）
# ---------------------------------------------------------------------------


def test_ingest_valid_payload_passes_all_gates(
    mcp_server: MCPServerClient, preseed_source: dict
) -> None:
    """完全合法的 payload 应顺利通过四道护栏。"""
    result = mcp_server.call(
        "ek_ingest",
        {
            "kind": "concept",
            "payload": concept_payload(
                preseed_source["id"],
                title="合法加固测试概念",
                body="# 合法概念\n\n" + "x" * 60,
                search_terms=[
                    "合法加固测试概念",
                    "valid-hardening",
                    "合法测试",
                    "hardening-ok",
                    "ht-valid",
                ],
            ),
            "conflict_policy": "strict",
        },
    )

    assert result["id"].startswith("ek_con_")
    fetched = mcp_server.call("ek_get", {"id": result["id"]})
    assert fetched["title"] == "合法加固测试概念"
