# reference/error-types.md

> 4 类错误的完整契约。改任何字段必须同步 transport 适配层（CLI / MCP）。

真源：[`errors.py`](../../src/ego_knowledge/errors.py)。

## 类层级

```text
EgoKnowledgeError(Exception)
├── ValidationError    code="EK_VALIDATION"  exit=1  http=400
├── ConflictError      code="EK_CONFLICT"    exit=2  http=409
├── NotFoundError      code="EK_NOT_FOUND"   exit=3  http=404
└── StorageError       code="EK_STORAGE"     exit=4  http=500
```

`exit_code=99` 与 `code="EK_UNKNOWN"` 是基类默认（`http_status=500`），正常不直接抛。

## 字段

每个错误实例有：

- `message: str` — 必填，中文用户消息
- `code: str` — 类级常量（`EK_*` 前缀，stable English），不可改写
- `details: dict | None` — 结构化补充（如 `ConflictError.details["candidates"]`）
- `exit_code: int` — CLI 进程退出码
- `http_status: int` — HTTP/MCP transport 状态码

## 触发场景

### ValidationError（exit=1 / 400）

- schema 校验失败（jsonschema）
- search_terms 三桶不覆盖
- update 改了不允许字段（`id/kind/file_path/metrics`）；body 仅按 [`body-update-standard.md`](../how-to/body-update-standard.md) 单条原地写入
- update 改稳定 slug（concept/dossier/decision 必须走 `rename()`）
- 字段不属于该 kind
- 不支持的 conflict_policy / relation direction / group_by
- promote 路径非法（如 concept→note 等"降级"，或未定义路径）
- promote 缺少 `source_ref`（v1 note→concept 路径已强制要求）
- source_url 白名单校验失败（ingest/update source 类型时，`source_url` 前缀不在 `http://`、`https://`、`knowledge://` 白名单内）

### ConflictError（exit=2 / 409）

- `conflict_policy="strict"` 下 alias/title 命中候选（`details["candidates"]` 列表）
- `domain` 迁移目标文件已存在

### NotFoundError（exit=3 / 404）

- `Registry.get_entry(id)` 找不到
- `ensure_reference_targets` 检测到 relations/refs 指向不存在条目（缺失的 id 在 message 字符串里，如 `引用目标不存在: ek_xxx, ek_yyy`；当前实现未填 `details`）

### StorageError（exit=4 / 500）

- SQLite OperationalError（包括无法获取 IMMEDIATE 写锁、COMMIT 失败、关闭失败）
- 临时文件 fsync 失败
- `os.rename` 失败
- FTS 写入失败

body 写入的 commit 后失败会优先按 `.txn-snapshots/` 还原；快照缺失或还原失败时抛 `body_recovery_failed_snapshot_missing`，并写入 `logs/refresh/recovery.log` 供人工排查。

## body 写入 error_code

| error_code | 根类 | 含义 |
| --- | --- | --- |
| `body_invalid_utf8` | ValidationError | body 不是合法 UTF-8 文本或不是文本值。 |
| `body_length_below_min` | ValidationError | NFC 后 body 字节数低于下限。 |
| `body_length_above_max` | ValidationError | NFC 后 body 字节数高于 40960。 |
| `body_frontmatter_mismatch` | ValidationError | body 内 frontmatter 与目标条目不一致。 |
| `body_batch_not_supported` | ValidationError | body 批量或同批路径迁移不受支持。 |
| `body_recovery_failed_snapshot_missing` | StorageError | body 事务恢复失败，快照缺失或不可用。 |

## Transport 序列化

`to_transport(exc)` 返回**扁平** dict（无 `error` 包装层、不含 `http_status`）：

```json
{
  "code": "EK_VALIDATION",
  "message": "...",
  "details": {...}
}
```

`http_status` 仅在 HTTP/MCP 适配层使用（作为 response status），不进 payload。

CLI 把这个扁平 JSON 一行写到 stderr 并 `Exit(exit_code)`；HTTP 直接做 response body 并把 `http_status` 写到响应头。

### MCP 层的字段重塑

MCP 层**不直接透传 Core 错误 payload**。`mcp_server/_errors.py:wrap_core_errors` 会把 Core 异常重包成 ToolError，字段映射如下：

| Core 异常 | MCP `error_type`（snake_case） |
| --- | --- |
| `ValidationError` (`EK_VALIDATION`) | `validation_error` |
| `ConflictError` (`EK_CONFLICT`) | `conflict_error` |
| `NotFoundError` (`EK_NOT_FOUND`) | `not_found_error` |
| `StorageError` (`EK_STORAGE`) | `storage_error` |
| 其他未识别异常 | `internal_error`（`details` 至少含 `tool` 名） |

ToolError 的 payload 形状是 `{"error_type", "message", "details"}`，**丢弃 `code` 与 `http_status`**，由 MCP 客户端按 `error_type` 匹配。AI 调用方读 MCP 错误时，必须解析这个 snake_case 字段，不要用 `EK_*` 大写常量。

## 改字段的影响面

任何错误新增/字段改名，需同步：

- `cli.py::_run_json`：stderr JSON 形状（当前直接 `json.dumps(to_transport(exc))`）
- `mcp_server/_errors.py::wrap_core_errors`：ToolError 形状
- 测试：`tests/adversarial/` 错误契约用例

不改这三处就会出现"CLI 报错正确但 MCP 客户端崩溃"之类的漂移，进入 `ek doctor` 检查范围。
