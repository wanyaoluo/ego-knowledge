from __future__ import annotations

from pathlib import Path

from tests.integration.conftest import (
    MCPServerClient,
    server_env,
    start_uv_entrypoint_server,
    wait_for_exit,
)


def test_startup_fails_without_data_root_env() -> None:
    proc = start_uv_entrypoint_server(server_env(None, include_data_root=False))

    returncode, stdout, stderr = wait_for_exit(proc)

    assert returncode != 0
    assert stdout == ""
    assert "EGOKNOWLEDGE_DATA_ROOT 环境变量未设置" in stderr


def test_startup_fails_when_data_root_path_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing-root"
    proc = start_uv_entrypoint_server(server_env(missing))

    returncode, stdout, stderr = wait_for_exit(proc)

    assert returncode != 0
    assert stdout == ""
    assert "EGOKNOWLEDGE_DATA_ROOT 指向的路径不存在" in stderr


def test_startup_succeeds_with_valid_data_root(integration_data_root: Path) -> None:
    client = MCPServerClient(integration_data_root)
    init = client.initialize()
    tools = client.list_tools()

    assert init["serverInfo"]["name"] == "ego-knowledge"
    assert len(tools["tools"]) == 11
