from __future__ import annotations

from pathlib import Path

from tests.integration.conftest import MCPServerClient
from tests.unit.support import source_payload


def test_ingest_get_search_write_verify_loop(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    created = mcp_server.call(
        "ek_ingest",
        {
            "kind": "source",
            "payload": source_payload(
                title="写后验证来源",
                source_type="doc",
                source_url="https://example.com/write-verify",
                captured_at="2026-04-10",
                content_hash="sha256:2222222222222222222222222222222222222222222222222222222222222222",
                body="# 写后验证\n",
                search_terms=[
                    "写后验证来源",
                    "write verify",
                    "来源验证",
                    "写后验证",
                    "verify-source",
                ],
            ),
            "conflict_policy": "strict",
        },
    )
    fetched = mcp_server.call("ek_get", {"id": created["id"]})
    results = mcp_server.call("ek_search", {"query": "写后验证"})
    if isinstance(results, dict):
        results = [results]

    assert created["id"].startswith("ek_src_")
    assert fetched["id"] == created["id"]
    assert fetched["title"] == "写后验证来源"
    assert any(result["id"] == created["id"] for result in results)
    assert (integration_data_root / created["file_path"]).exists()
