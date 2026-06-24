"""Integration: write tools repair MCP-client list mis-serialization end-to-end.

Reproduces the OpenCode client bug where list fields nested inside the opaque
``payload`` / ``changes`` objects arrive as ``{"item": [...]}`` wrappers or
flattened JSON strings, and asserts the server normalizes them back to lists.
Includes the multi-layer wrapper regression (``{"item": {"item": [...]}}``)
observed in real session ses_10c6a285, where a single peel leaves the outer
``item`` key behind and trips schema validation.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.integration.conftest import MCPServerClient
from tests.unit.support import concept_payload, note_payload, source_payload

_UNIQUE_HASH = "sha256:" + "9" * 64
_SEED_HASH = "sha256:" + "7" * 64
_MULTILAYER_HASH = "sha256:" + "3" * 64
_REINGEST_HASH = "sha256:" + "1" * 64


def test_ingest_repairs_wrapped_and_stringified_lists(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    payload = source_payload(title="归一化修复来源", content_hash=_UNIQUE_HASH, tags=["bug", "mcp"])
    expected_tags = list(payload["tags"])
    expected_search_terms = list(payload["search_terms"])
    # Simulate two distinct client mis-serialization shapes for list fields,
    # preserving element counts so schema length rules still hold after repair.
    payload["tags"] = {"item": expected_tags}
    payload["search_terms"] = json.dumps(expected_search_terms, ensure_ascii=False)

    created = mcp_server.call("ek_ingest", {"kind": "source", "payload": payload})

    assert created["id"].startswith("ek_src_")
    fetched = mcp_server.call("ek_get", {"id": created["id"]})
    assert fetched["tags"] == expected_tags
    assert isinstance(fetched["search_terms"], list)
    assert set(fetched["search_terms"]) >= {"归一化修复来源", "source", "src"}
    assert (integration_data_root / created["file_path"]).exists()


def test_ingest_repairs_multilayer_wrapper_across_failure_fields(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    """Regression for ses_10c6a285: ``{"item": {"item": [...]}}`` multi-layer
    wrappers must collapse fully; the four real failure fields (tags /
    search_terms / source_refs / evidence_refs) are all covered.
    """
    seed = source_payload(title="多层包装依赖源", content_hash=_SEED_HASH)
    seed_id = mcp_server.call("ek_ingest", {"kind": "source", "payload": seed})["id"]

    # source kind: tags + search_terms both double-wrapped.
    source = source_payload(
        title="多层包装来源",
        content_hash=_MULTILAYER_HASH,
        tags=["bug", "mcp", "regression"],
        search_terms=["多层包装", "multilayer", "归一化", "normalize", "source"],
    )
    expected_tags = list(source["tags"])
    expected_search_terms = list(source["search_terms"])
    source["tags"] = {"item": {"item": expected_tags}}
    source["search_terms"] = {"items": {"item": expected_search_terms}}

    created_src = mcp_server.call("ek_ingest", {"kind": "source", "payload": source})
    assert created_src["id"].startswith("ek_src_")
    fetched_src = mcp_server.call("ek_get", {"id": created_src["id"]})
    assert fetched_src["tags"] == expected_tags
    assert fetched_src["search_terms"] == expected_search_terms

    # note kind: source_refs double-wrapped. (content_hash is source-only.)
    note = note_payload(
        source_id=seed_id,
        title="多层包装笔记",
    )
    expected_refs = list(note["source_refs"])
    note["source_refs"] = {"item": {"items": expected_refs}}

    created_note = mcp_server.call("ek_ingest", {"kind": "note", "payload": note})
    fetched_note = mcp_server.call("ek_get", {"id": created_note["id"]})
    assert fetched_note["source_refs"] == expected_refs

    # concept kind: evidence_refs double-wrapped.
    concept = concept_payload(
        source_id=seed_id,
        title="多层包装概念",
        evidence_status="partial",
    )
    expected_evidence = list(concept["evidence_refs"])
    concept["evidence_refs"] = {"items": {"items": expected_evidence}}

    created_con = mcp_server.call("ek_ingest", {"kind": "concept", "payload": concept})
    fetched_con = mcp_server.call("ek_get", {"id": created_con["id"]})
    assert fetched_con["evidence_refs"] == expected_evidence

    assert (integration_data_root / created_src["file_path"]).exists()
    assert (integration_data_root / created_note["file_path"]).exists()
    assert (integration_data_root / created_con["file_path"]).exists()


def test_update_repairs_multilayer_wrapper(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    seed = source_payload(title="多层包装更新基线", content_hash=_SEED_HASH)
    created = mcp_server.call("ek_ingest", {"kind": "source", "payload": seed})

    updated = mcp_server.call(
        "ek_update",
        {"id": created["id"], "changes": {"tags": {"items": {"item": ["patched", "ek"]}}}},
    )

    assert updated["tags"] == ["patched", "ek"]
    fetched = mcp_server.call("ek_get", {"id": created["id"]})
    assert fetched["tags"] == ["patched", "ek"]


def test_runtime_reingest_keeps_collapsed_payload_stable(
    mcp_server: MCPServerClient,
    integration_data_root: Path,
) -> None:
    """Runtime write path may pass the same payload through normalize_mapping
    twice (upstream adapter pre-repairs it, server normalizes again). The
    collapsed list shape must stay stable and not mutate on the second pass.
    """
    payload = source_payload(
        title="重复归一化来源",
        content_hash=_REINGEST_HASH,
        tags=["a", "b"],
    )
    expected_tags = list(payload["tags"])
    payload["tags"] = {"item": {"item": expected_tags}}

    first = mcp_server.call("ek_ingest", {"kind": "source", "payload": payload})
    fetched_first = mcp_server.call("ek_get", {"id": first["id"]})
    assert fetched_first["tags"] == expected_tags

    # Re-feed the already-repaired shape (as the client might in a follow-up
    # call) under a new title + content_hash so there is no slug/conflict.
    reingest = source_payload(
        title="重复归一化来源二",
        content_hash="sha256:" + "2" * 64,
        tags=expected_tags,
    )
    second = mcp_server.call("ek_ingest", {"kind": "source", "payload": reingest})
    fetched_second = mcp_server.call("ek_get", {"id": second["id"]})
    assert fetched_second["tags"] == expected_tags
    assert (integration_data_root / second["file_path"]).exists()

