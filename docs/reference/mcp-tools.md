# ego-knowledge · MCP tools 参考

集中列出 11 个 MCP tool 的签名、返回值、`error_type` 与 `details` 结构。

> `error_type` 以 `mcp_server/_errors.py` 的 `_ERROR_TYPE_MAP` 为准。
> 四类 Core error 与 `internal_error` 共 5 种，所有 tool 统一载荷格式见文末。

## 11 tool 总表

| Tier | Tool | 返回 | 可能的 error_type | 关键 details |
| --- | --- | --- | --- | --- |
| 核心 | `ek_search` | `list[SearchResult]` | `storage_error` / `internal_error` | `internal_error.details.tool` |
| 核心 | `ek_get` | `Entry` | `not_found_error` / `storage_error` / `internal_error` | `internal_error.details.tool` |
| 核心 | `ek_related` | `list[Entry]` | `not_found_error` / `validation_error` / `storage_error` / `internal_error` | `internal_error.details.tool` |
| 核心 | `ek_ingest` | `Entry` | `validation_error` / `conflict_error` / `not_found_error` / `storage_error` / `internal_error` | `details.candidates` |
| 核心 | `ek_update` | `Entry` | `validation_error` / `conflict_error` / `not_found_error` / `storage_error` / `internal_error` | `details.candidates` |
| 核心 | `ek_link` | `Relation` | `validation_error` / `not_found_error` / `storage_error` / `internal_error` | `internal_error.details.tool` |
| 进阶 | `ek_promote` | `Entry` | `validation_error` / `conflict_error` / `not_found_error` / `storage_error` / `internal_error` | `details.candidates` |
| 进阶 | `ek_unlink` | `None` | `not_found_error` / `storage_error` / `internal_error` | `internal_error.details.tool` |
| 进阶 | `ek_review` | `list[Entry]` | `validation_error` / `storage_error` / `internal_error` | `internal_error.details.tool` |
| 维护 | `ek_maintain` | `DoctorReport` / `DiagnoseReport` / `dict` | `validation_error` / `storage_error` / `internal_error` | `details.valid_actions` |
| 维护 | `ek_domains` | `list[dict]` / `None` / `MigrateResult` | `validation_error` / `conflict_error` / `storage_error` / `internal_error` | `details.valid_actions` / `details.missing_fields` |

## 核心层

### `ek_search`

签名：`ek_search(query: str, kinds: list[str]|None=None, filters: dict|None=None, backends: list[str]|None=None, limit: int=20, expand_graph: bool=True, include_archived: bool=False)`

返回 `list[SearchResult]`。详见 [`search-contract.md`](search-contract.md)。

### `ek_get`

签名：`ek_get(id: str)`

返回 `Entry`。ID 不存在抛 `not_found_error`。

### `ek_related`

签名：`ek_related(id: str, depth: int=1, rel_type: str|None=None, include_archived: bool=False)`

返回 `list[Entry]`。`depth` 非正整数抛 `validation_error`。

### `ek_ingest`

签名：`ek_ingest(kind: str, payload: dict, conflict_policy: str="strict")`

返回 `Entry`。冲突时 `conflict_error.details.candidates` 给出候选列表。`source` kind 的 `source_url` 仅接受 `http://`/`https://`/`knowledge://` 前缀，否则抛 `validation_error`（见 [`error-types.md`](error-types.md)）。

> `payload` 的 JSON Schema 为 `additionalProperties: true`，部分 MCP 客户端会把其中的 list 字段错误序列化成 `{"item": [...]}` / `{"items": [...]}` 单 key 包装，或形如 `'["a", "b"]'` 的 JSON 数组字符串，真实会话中还观察到多层包装 `{"item": {"item": [...]}}`。`ek_ingest` 在进入 Core 前对 `payload` 做递归归一化，逐层拆解这类 list 字段序列化错误；其他包装 key、对象字符串或普通文本字符串不会自动还原（见 `mcp_server/_normalize.py` 与 `tests/integration/test_mcp_write_normalize.py`）。

### `ek_update`

签名：`ek_update(id: str, changes: dict)`

返回 `Entry`。不可改 `id`/`kind`/`file_path`/`metrics`。`body` 只允许按 [`body-update-standard.md`](../how-to/body-update-standard.md) 单条原地写入；`concept`/`dossier` 改 `domain` 会触发 `concept/dossier 改 domain 请走 domains_migrate()`。`source` kind 的 `source_url` 白名单规则同 `ek_ingest`。

> `changes` 同 `ek_ingest` 的 `payload`，进入 Core 前递归归一化（含多层 `item`/`items` 包装）list 字段的客户端序列化错误。

### `ek_link`

签名：`ek_link(source_id: str, target_id: str, rel_type: str, source: str="confirmed")`

返回 `Relation`。`rel_type` 必须在 `RelationType` 枚举内；`source` 仅接受 `confirmed`/`ai_suggested`/`ai_confirmed`。类型专属字段（`source_refs`/`evidence_refs`/`promotion_targets`/`superseded_by`）不能通过 `link` 操作。

## 进阶层

### `ek_promote`

签名：`ek_promote(id: str, target_kind: str, freshness: str="watch")`

返回 `Entry`。仅允许 4 条路径：`note→concept`、`note→dossier`、`dossier→concept`、`concept→decision`。前置条件见 [`agent-write-flow.md`](../how-to/agent-write-flow.md)。

### `ek_unlink`

签名：`ek_unlink(source_id: str, target_id: str)`

返回 `None`。静默跳过不存在的边。

### `ek_review`

签名：`ek_review(overdue_only: bool=False, include_archived: bool=False)`

返回 `list[Entry]`（dossier 的复核队列）。

## 维护层

### `ek_maintain`

签名：`ek_maintain(action: str, group_by: str|None=None)`

`action` 仅接受 `diagnose`/`doctor`/`stats`。无效时 `validation_error.details.valid_actions` 返回合法值列表。`stats` 的 `group_by` 支持 `kind`/`status`/`freshness`/`domain`。

### `ek_domains`

签名：`ek_domains(action: str, name: str|None=None, entries: list[str]|None=None, target_domain: str|None=None)`

`action` 仅接受 `add`/`list`/`migrate`。缺参时 `validation_error.details.missing_fields` 返回缺失字段。

## 通用错误载荷

```json
{
  "error_type": "validation_error | conflict_error | not_found_error | storage_error | internal_error",
  "message": "...",
  "details": {}
}
```

- `conflict_error` → 看 `details.candidates`
- `validation_error` → 看 `details.valid_actions` / `details.missing_fields`
- `internal_error` → 固定带 `details.tool`
