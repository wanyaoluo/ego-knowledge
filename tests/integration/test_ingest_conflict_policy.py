from __future__ import annotations

import json

import pytest

from tests.integration.conftest import MCPServerClient
from tests.unit.support import note_payload, source_payload


@pytest.mark.parametrize(
    ("conflict_policy", "should_error"),
    [
        ("strict", True),
        ("merge_suggest", False),
        ("allow", False),
    ],
)
def test_ingest_conflict_policy_behaviour(
    mcp_server: MCPServerClient,
    conflict_policy: str,
    should_error: bool,
) -> None:
    source = mcp_server.call(
        "ek_ingest",
        {
            "kind": "source",
            "payload": source_payload(title=f"{conflict_policy}-来源"),
            "conflict_policy": "strict",
        },
    )
    existing = note_payload(
        source["id"],
        title="冲突策略基线",
        aliases=["策略别名"],
        extracted_at="2026-04-17",
        promotion_targets=[],
        body="# 基线\n\n" + "x" * 50,
        search_terms=["冲突策略基线", "conflict policy", "冲突策略", "策略别名", "policy-baseline"],
    )
    mcp_server.call("ek_ingest", {"kind": "note", "payload": existing, "conflict_policy": "strict"})

    candidate = note_payload(
        source["id"],
        title=f"冲突策略候选-{conflict_policy}",
        aliases=["策略别名"],
        slug=f"policy-candidate-{conflict_policy.replace('_', '-')}",
        extracted_at="2026-04-17",
        promotion_targets=[],
        body="# 候选\n\n" + "x" * 50,
        search_terms=[
            f"冲突策略候选-{conflict_policy}",
            "conflict policy",
            "冲突策略",
            "策略别名",
            f"policy-{conflict_policy}",
        ],
    )
    raw = mcp_server.call_raw(
        "ek_ingest",
        {"kind": "note", "payload": candidate, "conflict_policy": conflict_policy},
    )

    assert raw["isError"] is should_error
    if should_error:
        payload = json.loads(raw["content"][0]["text"])
        assert payload["error_type"] == "conflict_error"
        assert payload["details"]["candidates"]
    else:
        created = mcp_server.decode_result(raw)
        fetched = mcp_server.call("ek_get", {"id": created["id"]})
        assert created["id"].startswith("ek_note_")
        assert fetched["id"] == created["id"]
