"""P0.1：CLI 默认数据根路径惰性解析测试。

验收口径（派单卡 + spec §5.1/§5.2）：
- 不设 ``EK_DATA_ROOT`` 时，默认根落 ``~/.ego-knowledge/data``；
- 设置 ``EK_DATA_ROOT`` 时覆盖默认值；
- 默认值由惰性函数 ``default_data_root()`` 运行时求值，不是模块级常量，
  避免同进程 import 时 ``Path.home()`` 固化导致 HOME 隔离测试脆弱失效。

HOME 隔离方式：用子进程承载被测代码（``subprocess`` + 显式 ``env``），
不依赖同进程 monkeypatch，以真正验证惰性语义在干净环境下成立。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from ego_knowledge.cli import main


def _run_isolated(
    code: str,
    *,
    home: Path,
) -> subprocess.CompletedProcess[str]:
    """在 HOME 隔离的子进程中执行 ``code``，返回 stdout。

    故意从父进程环境复制必要变量（PATH / venv 相关），仅覆盖 ``HOME`` 并
    移除 ``EK_DATA_ROOT``，确保被测代码读到的是隔离 HOME、无覆盖变量。
    """
    env = dict(os.environ)
    env["HOME"] = str(home)
    env.pop("EK_DATA_ROOT", None)  # 模拟「未设置」，而非设空字符串
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_cli_default_root_without_env_under_isolated_home(tmp_path: Path) -> None:
    """不设 EK_DATA_ROOT 时，default_data_root() 落 <HOME>/.ego-knowledge/data。"""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()

    code = (
        "from ego_knowledge.paths import default_data_root; "
        "print(default_data_root())"
    )
    result = _run_isolated(code, home=fake_home)

    resolved = Path(result.stdout.strip())
    assert resolved == fake_home / ".ego-knowledge" / "data"


def test_cli_default_root_is_lazy_not_module_constant(tmp_path: Path) -> None:
    """default_data_root() 运行时求值：同进程内改 HOME 后再次调用返回新值。

    若退化为模块级常量（import 时固化 Path.home()），本用例会失败——
    这是 P0.1 惰性语义的回归保护。
    """
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()

    code = (
        "import json, os\n"
        "from ego_knowledge.paths import default_data_root\n"
        "os.environ['HOME'] = '/tmp/ego-p0-lazy-1'\n"
        "first = str(default_data_root())\n"
        "os.environ['HOME'] = '/tmp/ego-p0-lazy-2'\n"
        "second = str(default_data_root())\n"
        "print(json.dumps({'first': first, 'second': second}))\n"
    )
    result = _run_isolated(code, home=fake_home)

    payload = json.loads(result.stdout)
    assert payload["first"] == "/tmp/ego-p0-lazy-1/.ego-knowledge/data"
    assert payload["second"] == "/tmp/ego-p0-lazy-2/.ego-knowledge/data"
    assert payload["first"] != payload["second"]


def test_cli_default_root_excludes_egoentity(tmp_path: Path) -> None:
    """默认根不得再含开源抽离前的旧品牌硬编码片段（净化回归保护）。"""
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()

    code = (
        "from ego_knowledge.paths import default_data_root; "
        "print(default_data_root())"
    )
    result = _run_isolated(code, home=fake_home)

    resolved = result.stdout.strip()
    old_repo_name = "Ego" + "Entity"
    old_data_dir = "Ego" + "Knowledge"
    assert old_repo_name not in resolved
    assert old_data_dir not in resolved  # 默认目录名已改为 .ego-knowledge


def test_cli_default_root_env_override_build_registry(tmp_path: Path) -> None:
    """设置 EK_DATA_ROOT 时覆盖默认值：build-registry 落盘到 env 指定根。"""
    override_root = tmp_path / "override" / "data"
    override_root.mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(main, ["build-registry"], env={"EK_DATA_ROOT": str(override_root)})

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["entries_ok"] == 0
    # build-registry 在 data_root/registry/catalog.sqlite 落盘
    assert (override_root / "registry" / "catalog.sqlite").exists()
