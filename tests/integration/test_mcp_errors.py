from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.integration.conftest import MCPServerClient
from tests.unit.support import note_payload, source_payload


def _assert_error(
    result: dict[str, object],
    *,
    error_type: str,
    message_fragment: str,
) -> dict[str, object]:
    assert result["isError"] is True
    content = result["content"]
    assert isinstance(content, list) and content
    payload = json.loads(content[0]["text"])
    assert payload["error_type"] == error_type
    assert message_fragment in payload["message"]
    return payload


def test_validation_error_over_real_stdio(mcp_server: MCPServerClient) -> None:
    payload = source_payload(
        title="缺标题来源",
        source_url="https://example.com/missing-title",
        captured_at="2026-04-10",
        content_hash="sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    payload.pop("title")

    result = mcp_server.call_raw(
        "ek_ingest",
        {"kind": "source", "payload": payload, "conflict_policy": "strict"},
    )
    payload = _assert_error(
        result,
        error_type="validation_error",
        message_fragment="ingest payload 缺少非空 title",
    )

    assert payload["details"] == {}


def test_conflict_error_over_real_stdio(mcp_server: MCPServerClient) -> None:
    source = mcp_server.call(
        "ek_ingest",
        {
            "kind": "source",
            "payload": source_payload(title="冲突来源"),
            "conflict_policy": "strict",
        },
    )
    existing = note_payload(
        source["id"],
        title="RAG 方案横评",
        aliases=["RAG 横评"],
        extracted_at="2026-04-17",
        promotion_targets=[],
        body="# 已存在\n\n" + "x" * 50,
        search_terms=["RAG", "检索增强生成", "retrieval-augmented", "RAG 横评", "rag-comparison"],
    )
    mcp_server.call("ek_ingest", {"kind": "note", "payload": existing, "conflict_policy": "strict"})

    candidate = note_payload(
        source["id"],
        title="RAG 方案横评 2",
        aliases=["RAG 横评"],
        slug="rag-方案横评-2",
        extracted_at="2026-04-17",
        promotion_targets=[],
        body="# 冲突候选\n\n" + "x" * 50,
        search_terms=[
            "RAG",
            "检索增强生成",
            "retrieval-augmented",
            "RAG 横评 2",
            "rag-comparison-2",
        ],
    )
    result = mcp_server.call_raw(
        "ek_ingest",
        {"kind": "note", "payload": candidate, "conflict_policy": "strict"},
    )
    payload = _assert_error(
        result,
        error_type="conflict_error",
        message_fragment="冲突：检测到候选重复条目",
    )

    assert payload["details"]["candidates"]
    assert all(set(candidate) == {"id", "title"} for candidate in payload["details"]["candidates"])


def test_not_found_error_over_real_stdio(mcp_server: MCPServerClient) -> None:
    result = mcp_server.call_raw("ek_get", {"id": "ek_src_missing"})
    payload = _assert_error(
        result,
        error_type="not_found_error",
        message_fragment="条目不存在: ek_src_missing",
    )

    assert payload["details"] == {}


def test_storage_error_over_real_stdio(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    assert mcp_server.call("ek_search", {"query": "warmup"}) == []
    db_path = integration_data_root / "registry" / "catalog.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO registry_meta(key, value)
            VALUES('domains', '{bad')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
        conn.commit()

    result = mcp_server.call_raw("ek_domains", {"action": "list"})
    payload = _assert_error(
        result,
        error_type="storage_error",
        message_fragment="registry_meta.domains 不是合法 JSON",
    )

    assert payload["details"] == {}
