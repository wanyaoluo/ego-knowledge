"""EgoKnowledge MCP server entrypoint."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import anyio

# jsonschema has no bundled PEP 561 stubs; keep runtime dependency without adding types-jsonschema.
import jsonschema  # type: ignore[import-untyped]
import mcp.types as mcp_types
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS

from .mcp_tools import register_all_tools

if TYPE_CHECKING:
    from ..core import EgoKnowledge

mcp = FastMCP("ego-knowledge")
_core: EgoKnowledge | None = None


def _resolve_data_root() -> Path:
    """Resolve data_root from the required environment variable."""

    raw = os.environ.get("EGOKNOWLEDGE_DATA_ROOT")
    if not raw:
        raise RuntimeError(
            "EGOKNOWLEDGE_DATA_ROOT 环境变量未设置。"
            "请设置该变量指向 ego-knowledge 数据目录。"
        )
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"EGOKNOWLEDGE_DATA_ROOT 指向的路径不存在：{path}")
    if not path.is_dir():
        raise RuntimeError(f"EGOKNOWLEDGE_DATA_ROOT 必须指向目录：{path}")
    return path


def _get_core() -> EgoKnowledge:
    global _core
    if _core is None:
        from ..core import EgoKnowledge

        _core = EgoKnowledge(data_root=_resolve_data_root())
    return _core


register_all_tools(mcp, _get_core)


def _write_response(rpc_id: int | str, result: dict[str, object]) -> None:
    response = mcp_types.JSONRPCResponse(jsonrpc="2.0", id=rpc_id, result=result)
    sys.stdout.write(response.model_dump_json(by_alias=True, exclude_none=True) + "\n")
    sys.stdout.flush()


def _initialize_result(requested_version: str | int) -> dict[str, object]:
    init_options = mcp._mcp_server.create_initialization_options()
    protocol_version = (
        requested_version
        if isinstance(requested_version, str) and requested_version in SUPPORTED_PROTOCOL_VERSIONS
        else mcp_types.LATEST_PROTOCOL_VERSION
    )
    result = mcp_types.InitializeResult(
        protocolVersion=protocol_version,
        capabilities=init_options.capabilities,
        serverInfo=mcp_types.Implementation(
            name=init_options.server_name,
            version=init_options.server_version,
            websiteUrl=init_options.website_url,
            icons=init_options.icons,
        ),
        instructions=init_options.instructions,
    )
    return result.model_dump(mode="json", by_alias=True, exclude_none=True)


def _list_tools_result() -> dict[str, object]:
    tools = [
        mcp_types.Tool(
            name=tool.name,
            title=tool.title,
            description=tool.description,
            inputSchema=tool.parameters,
            outputSchema=tool.output_schema,
            annotations=tool.annotations,
            icons=tool.icons,
            _meta=tool.meta,
        )
        for tool in mcp._tool_manager.list_tools()
    ]
    return mcp_types.ListToolsResult(tools=tools).model_dump(
        mode="json", by_alias=True, exclude_none=True
    )


async def _invoke_tool(tool_name: str, arguments: dict[str, object]) -> mcp_types.CallToolResult:
    tool = mcp._tool_manager.get_tool(tool_name)
    if tool is None:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=f"Unknown tool: {tool_name}")],
            isError=True,
        )
    try:
        jsonschema.validate(instance=arguments, schema=tool.parameters)
    except jsonschema.ValidationError as exc:
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=f"Input validation error: {exc.message}",
                )
            ],
            isError=True,
        )

    try:
        result = await tool.fn_metadata.call_fn_with_arg_validation(
            tool.fn,
            tool.is_async,
            arguments,
            {tool.context_kwarg: None} if tool.context_kwarg is not None else None,
        )
        converted = tool.fn_metadata.convert_result(result)
    except ToolError as exc:
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(exc))],
            isError=True,
        )
    except Exception as exc:  # noqa: BLE001
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text=str(exc))],
            isError=True,
        )

    if isinstance(converted, mcp_types.CallToolResult):
        return converted
    if isinstance(converted, tuple) and len(converted) == 2:
        unstructured, structured = converted
        return mcp_types.CallToolResult(
            content=list(unstructured),
            structuredContent=structured,
            isError=False,
        )
    return mcp_types.CallToolResult(content=list(converted), isError=False)


def main() -> None:
    """Run the FastMCP app over stdio."""

    _resolve_data_root()
    for line in sys.stdin:
        message = mcp_types.JSONRPCMessage.model_validate_json(line)
        root = message.root
        if isinstance(root, mcp_types.JSONRPCRequest) and root.method == "initialize":
            params = root.params or {}
            _write_response(
                root.id,
                _initialize_result(
                    params.get(
                        "protocolVersion",
                        mcp_types.LATEST_PROTOCOL_VERSION,
                    )
                ),
            )
            continue
        if (
            isinstance(root, mcp_types.JSONRPCNotification)
            and root.method == "notifications/initialized"
        ):
            continue
        if isinstance(root, mcp_types.JSONRPCRequest) and root.method == "tools/list":
            _write_response(root.id, _list_tools_result())
            continue
        if isinstance(root, mcp_types.JSONRPCRequest) and root.method == "tools/call":
            params = root.params or {}
            result = anyio.run(
                _invoke_tool,
                params.get("name", ""),
                params.get("arguments", {}) or {},
            )
            _write_response(
                root.id,
                result.model_dump(mode="json", by_alias=True, exclude_none=True),
            )
