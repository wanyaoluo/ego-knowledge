# tests/adversarial/

对抗用例测试。验证 MCP tool 面对异常输入时错误契约的严格性。

## 覆盖

`test_error_contracts.py`：11 tool × 失败路径（约 18+ 条），断言 `error_type` 为 snake_case（`validation_error / conflict_error / not_found_error / storage_error / internal_error`）+ `message` + `details` 结构对齐 error contract 定义。

典型场景：

- `ek_ingest` 缺字段 / 非法 conflict_policy / aliases 冲突
- `ek_update` Core 禁止字段（id/kind/file_path/metrics）+ concept/dossier/decision 改 slug
- `ek_get` 不存在 id / 路径遍历
- `ek_link` target 不存在 / rel_type 非法
- `ek_maintain` / `ek_domains` 无效 action → validation_error（不是 internal_error）
- RuntimeError → internal_error 兜底

## 运行

```bash
cd ego-knowledge
UV_NO_SYNC=1 .venv/bin/python -m pytest tests/adversarial/ -v
```
