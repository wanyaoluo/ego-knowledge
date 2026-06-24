from __future__ import annotations

from pathlib import Path

from tests.integration.conftest import EXPECTED_TOOL_FIELDS, MCPServerClient
from tests.unit.support import source_payload


def test_initialize_handshake(mcp_server: MCPServerClient) -> None:
    result = mcp_server.initialize()

    assert result["serverInfo"]["name"] == "ego-knowledge"
    assert result["protocolVersion"]
    assert "capabilities" in result


def test_initialize_list_tools_and_call_tool(mcp_server: MCPServerClient) -> None:
    tools = mcp_server.list_tools()
    raw = mcp_server.call_raw("ek_search", {"query": "不存在"})

    assert {tool["name"] for tool in tools["tools"]} == set(EXPECTED_TOOL_FIELDS)
    for tool in tools["tools"]:
        assert tool["inputSchema"]["type"] == "object"
        assert set(tool["inputSchema"].get("properties", {})) == EXPECTED_TOOL_FIELDS[tool["name"]]

    assert raw["isError"] is False
    assert all(block["type"] == "text" for block in raw["content"])
    assert mcp_server.decode_result(raw) == []


def test_ingest_then_get_over_stdio(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    payload = source_payload(
        title="MCP stdio 来源",
        source_type="doc",
        source_url="https://example.com/mcp-stdio-source",
        captured_at="2026-04-10",
        content_hash="sha256:1111111111111111111111111111111111111111111111111111111111111111",
        body="# MCP stdio\n",
    )

    created_raw = mcp_server.call_raw(
        "ek_ingest",
        {"kind": "source", "payload": payload, "conflict_policy": "strict"},
    )
    created = mcp_server.decode_result(created_raw)
    fetched_raw = mcp_server.call_raw("ek_get", {"id": created["id"]})
    fetched = mcp_server.decode_result(fetched_raw)

    assert created_raw["isError"] is False
    assert fetched_raw["isError"] is False
    assert created["id"].startswith("ek_src_")
    assert fetched["title"] == "MCP stdio 来源"
    assert (integration_data_root / created["file_path"]).exists()
