"""Unit coverage for EgoKnowledge MCP server scaffolding."""

from __future__ import annotations

import json

from mcp.server.fastmcp.exceptions import ToolError

from ego_knowledge.errors import ValidationError
from ego_knowledge.mcp_server import mcp
from ego_knowledge.mcp_server._errors import wrap_core_errors
from ego_knowledge.mcp_server.mcp_server import _resolve_data_root


def test_mcp_registers_expected_11_tools_with_real_sdk_schema() -> None:
    expected_names = {
        "ek_search",
        "ek_get",
        "ek_related",
        "ek_review",
        "ek_ingest",
        "ek_update",
        "ek_promote",
        "ek_link",
        "ek_unlink",
        "ek_maintain",
        "ek_domains",
    }
    expected_schema_fields = {
        "ek_search": {
            "query",
            "kinds",
            "filters",
            "backends",
            "limit",
            "expand_graph",
            "include_archived",
        },
        "ek_get": {"id"},
        "ek_related": {"id", "depth", "rel_type", "include_archived"},
        "ek_review": {"overdue_only", "include_archived"},
        "ek_ingest": {"kind", "payload", "conflict_policy"},
        "ek_update": {"id", "changes"},
        "ek_promote": {"id", "target_kind", "freshness"},
        "ek_link": {"source_id", "target_id", "rel_type", "source"},
        "ek_unlink": {"source_id", "target_id"},
        "ek_maintain": {"action", "group_by"},
        "ek_domains": {"action", "name", "entries", "target_domain"},
    }

    assert set(mcp._tool_manager._tools) == expected_names
    for name, expected_fields in expected_schema_fields.items():
        tool = mcp._tool_manager.get_tool(name)
        assert tool.parameters is not None
        assert set(tool.parameters["properties"]) == expected_fields
        assert tool.fn_metadata is not None


def test_wrap_core_errors_emits_json_payload() -> None:
    @wrap_core_errors
    def raises_validation() -> None:
        raise ValidationError("test msg", details={"k": "v"})

    try:
        raises_validation()
    except ToolError as exc:
        payload = json.loads(exc.args[0])
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ToolError")

    assert payload == {
        "error_type": "validation_error",
        "message": "test msg",
        "details": {"k": "v"},
    }


def test_resolve_data_root_reads_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", str(tmp_path))
    assert _resolve_data_root() == tmp_path.resolve()
