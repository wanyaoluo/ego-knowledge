from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from ego_knowledge.errors import ConflictError, NotFoundError, StorageError, ValidationError
from ego_knowledge.mcp_server import mcp
from ego_knowledge.mcp_server._errors import wrap_core_errors
from ego_knowledge.mcp_server.mcp_server import _resolve_data_root

mcp_server_mod = importlib.import_module("ego_knowledge.mcp_server.mcp_server")


class StubCore:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def review_queue(
        self, overdue_only: bool = False, *, include_archived: bool = False
    ) -> list[dict[str, object]]:
        self.calls.append(("review_queue", overdue_only))
        return [{"id": "ek_dos_review"}]

    def stats(self, group_by: str | None = None) -> dict[str, object]:
        self.calls.append(("stats", group_by))
        return {"group_by": group_by}

    def domains_migrate(
        self,
        entries: list[str],
        target_domain: str,
    ) -> dict[str, object]:
        self.calls.append(("domains_migrate", (entries, target_domain)))
        return {
            "entry_ids": entries,
            "rewritten_paths": [],
            "target_domain": target_domain,
        }


@pytest.fixture()
def stub_core(monkeypatch: pytest.MonkeyPatch) -> StubCore:
    core = StubCore()
    monkeypatch.setattr(mcp_server_mod, "_core", core)
    return core


@pytest.mark.parametrize(
    ("core_exc", "expected_type"),
    [
        (ValidationError("bad input"), "validation_error"),
        (
            ConflictError("duplicate", details={"candidates": [{"id": "ek_con_1", "title": "A"}]}),
            "conflict_error",
        ),
        (NotFoundError("missing"), "not_found_error"),
        (StorageError("disk failed"), "storage_error"),
    ],
)
def test_wrap_core_errors_maps_core_exceptions(core_exc: Exception, expected_type: str) -> None:
    @wrap_core_errors
    def raises_core_error() -> None:
        raise core_exc

    with pytest.raises(ToolError) as tool_err:
        raises_core_error()

    payload = json.loads(tool_err.value.args[0])
    expected_details = (
        {"candidates": [{"id": "ek_con_1", "title": "A"}]}
        if expected_type == "conflict_error"
        else {}
    )
    assert payload == {
        "error_type": expected_type,
        "message": core_exc.args[0],
        "details": expected_details,
    }


def test_wrap_core_errors_maps_unexpected_exception_to_internal_error() -> None:
    @wrap_core_errors
    def raises_runtime_error() -> None:
        raise RuntimeError("boom")

    with pytest.raises(ToolError) as tool_err:
        raises_runtime_error()

    payload = json.loads(tool_err.value.args[0])
    assert payload == {
        "error_type": "internal_error",
        "message": "boom",
        "details": {"tool": "raises_runtime_error"},
    }


def test_tool_manager_routes_ek_review_by_name(stub_core: StubCore) -> None:
    result = mcp._tool_manager.get_tool("ek_review").fn(overdue_only=True)

    assert result == [{"id": "ek_dos_review"}]
    assert stub_core.calls == [("review_queue", True)]


def test_tool_manager_routes_ek_maintain_stats_action(stub_core: StubCore) -> None:
    result = mcp._tool_manager.get_tool("ek_maintain").fn(action="stats", group_by="kind")

    assert result == {"group_by": "kind"}
    assert stub_core.calls == [("stats", "kind")]


def test_tool_manager_routes_ek_domains_migrate_action(stub_core: StubCore) -> None:
    result = mcp._tool_manager.get_tool("ek_domains").fn(
        action="migrate",
        entries=["ek_con_123"],
        target_domain="新领域",
    )

    assert result == {
        "entry_ids": ["ek_con_123"],
        "rewritten_paths": [],
        "target_domain": "新领域",
    }
    assert stub_core.calls == [("domains_migrate", (["ek_con_123"], "新领域"))]


def test_resolve_data_root_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EGOKNOWLEDGE_DATA_ROOT", raising=False)

    with pytest.raises(RuntimeError, match="EGOKNOWLEDGE_DATA_ROOT 环境变量未设置"):
        _resolve_data_root()


def test_resolve_data_root_requires_existing_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", str(missing))

    with pytest.raises(RuntimeError, match="路径不存在"):
        _resolve_data_root()


def test_resolve_data_root_requires_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "not-a-dir.txt"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", str(file_path))

    with pytest.raises(RuntimeError, match="必须指向目录"):
        _resolve_data_root()


def test_resolve_data_root_expands_and_resolves_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("EGOKNOWLEDGE_DATA_ROOT", str(tmp_path))

    assert _resolve_data_root() == tmp_path.resolve()


def test_mcp_registers_expected_11_tools_via_tool_manager() -> None:
    tools = list(mcp._tool_manager.list_tools())
    names = {tool.name for tool in tools}

    assert len(names) == 11
    assert names == {
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
    for tool in tools:
        assert tool.parameters is not None
        assert tool.fn_metadata is not None
