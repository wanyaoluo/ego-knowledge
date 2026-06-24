# tests/regression/

回归保护测试（Plan 2A P2 引入）。每个测试锚定一条长期不变的契约，防止未来改动悄悄破坏。

## 覆盖

`test_core_coverage.py`：锚定 Core 公开方法全部被显式归类——`EXPOSED_TOOLS`
（15 个走 MCP tool 暴露）+ `CLI_ONLY_METHODS`（CLI / 内部 facade）== 32。

当 Core 新增方法而未更新这两个集合时，测试立即失败。

## 运行

```bash
cd ego-knowledge
UV_NO_SYNC=1 .venv/bin/python -m pytest tests/regression/ -v
```
