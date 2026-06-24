from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from mcp import types as mcp_types
from mcp.server.fastmcp.exceptions import ToolError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VENV_BIN = PROJECT_ROOT / ".venv" / "bin"
EK_MCP_ENTRYPOINT = VENV_BIN / "ek-mcp"
VENV_PYTHON = VENV_BIN / "python"
DEFAULT_TIMEOUT = 5.0
EXPECTED_TOOL_FIELDS = {
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


def server_env(
    data_root: Path | str | None,
    *,
    include_data_root: bool = True,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "UV_NO_SYNC": "1",
        "UV_CACHE_DIR": "/tmp/uv-cache",
    }
    if include_data_root and data_root is not None:
        env["EGOKNOWLEDGE_DATA_ROOT"] = str(data_root)
    else:
        env.pop("EGOKNOWLEDGE_DATA_ROOT", None)
    if extra:
        env.update(extra)
    return env


def _server_popen(
    command: list[str] | tuple[str, ...],
    *,
    env: dict[str, str],
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        list(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=PROJECT_ROOT,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )


def start_module_server(data_root: Path) -> subprocess.Popen[str]:
    return _server_popen(
        [str(VENV_PYTHON), "-m", "ego_knowledge.mcp_server"],
        env=server_env(data_root),
    )


def start_uv_entrypoint_server(env: dict[str, str]) -> subprocess.Popen[str]:
    return _server_popen([str(EK_MCP_ENTRYPOINT)], env=env)


def terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        proc.communicate()
        return
    proc.terminate()
    try:
        proc.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()


def wait_for_exit(
    proc: subprocess.Popen[str], timeout: float = DEFAULT_TIMEOUT
) -> tuple[int, str, str]:
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
        terminate_process(proc)
        raise AssertionError("MCP process did not exit within timeout") from exc
    return proc.returncode or 0, stdout, stderr


@dataclass
class MCPServerClient:
    data_root: Path
    command: tuple[str, ...] = (str(EK_MCP_ENTRYPOINT),)
    timeout: float = DEFAULT_TIMEOUT
    extra_env: dict[str, str] = field(default_factory=dict)
    _next_id: int = 1

    def initialize(self) -> dict[str, Any]:
        return self.run_batch([])[0]["result"]

    def list_tools(self) -> dict[str, Any]:
        return self.run_batch([("tools/list", {})])[-1]["result"]

    def call_raw(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return self.run_batch([("tools/call", {"name": tool_name, "arguments": args})])[-1][
            "result"
        ]

    def call(self, tool_name: str, args: dict[str, Any]) -> Any:
        result = self.call_raw(tool_name, args)
        if result.get("isError"):
            text_blocks = self._text_blocks(result.get("content", []))
            payload = text_blocks[0] if text_blocks else json.dumps(result, ensure_ascii=False)
            raise ToolError(payload)
        return self.decode_result(result)

    def decode_result(self, result: dict[str, Any]) -> Any:
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        texts = self._text_blocks(result.get("content", []))
        if not texts:
            return result.get("content", [])
        if len(texts) == 1:
            return self._decode_text(texts[0])
        return [self._decode_text(text) for text in texts]

    def run_batch(self, requests: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        expected_ids: list[int] = []
        init_request = self._request_message(
            "initialize",
            {
                "protocolVersion": mcp_types.LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest-integration", "version": "1.0"},
            },
        )
        messages.append(init_request)
        expected_ids.append(init_request["id"])
        messages.append(self._notification_message("notifications/initialized", {}))
        for method, params in requests:
            request = self._request_message(method, params)
            messages.append(request)
            expected_ids.append(request["id"])
        responses = self._run_messages(messages, expected_ids)
        return [responses[rpc_id] for rpc_id in expected_ids]

    def _request_message(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        rpc_id = self._next_id
        self._next_id += 1
        return {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}

    @staticmethod
    def _notification_message(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "method": method, "params": params}

    def _run_messages(
        self,
        messages: list[dict[str, Any]],
        expected_ids: list[int],
    ) -> dict[int, dict[str, Any]]:
        proc = _server_popen(
            self.command,
            env=server_env(self.data_root, extra=self.extra_env),
        )
        payload = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        try:
            stdout, stderr = proc.communicate(input=payload, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process(proc)
            raise AssertionError("MCP process did not finish within timeout") from exc
        if proc.returncode not in {0, None}:
            raise RuntimeError(f"MCP server exited with code {proc.returncode}: {stderr.strip()}")
        responses: dict[int, dict[str, Any]] = {}
        for line in stdout.splitlines():
            if not line.strip():
                continue
            response = json.loads(line)
            if "error" in response:
                raise RuntimeError(json.dumps(response["error"], ensure_ascii=False))
            responses[response["id"]] = response
        missing = [rpc_id for rpc_id in expected_ids if rpc_id not in responses]
        if missing:
            raise RuntimeError(
                f"MCP server missing responses {missing}: stderr={stderr.strip()} stdout={stdout!r}"
            )
        return responses

    @staticmethod
    def _text_blocks(content: list[dict[str, Any]]) -> list[str]:
        return [
            block["text"] for block in content if block.get("type") == "text" and "text" in block
        ]

    @staticmethod
    def _decode_text(text: str) -> Any:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


@pytest.fixture()
def integration_data_root(tmp_path: Path) -> Path:
    data_root = tmp_path / "data" / "EgoKnowledge"
    (data_root / "registry").mkdir(parents=True)
    return data_root


@pytest.fixture()
def mcp_server(integration_data_root: Path) -> MCPServerClient:
    return MCPServerClient(integration_data_root)
