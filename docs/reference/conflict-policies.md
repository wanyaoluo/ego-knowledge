# reference/conflict-policies.md

> `ingest()` 的 `conflict_policy` 三种取值的完整语义。

真源：[`_validation.check_conflicts`](../../src/ego_knowledge/_validation.py)（83-136 行）。

## 三种策略

| 值 | 行为 | 何时用 |
| --- | --- | --- |
| `strict`（默认） | 命中候选直接抛 `ConflictError` | 人工新增、要求查重 |
| `merge_suggest` | 检测候选但**不抛**，由调用方决定（当前实现回 silently，候选信息已写日志） | AI 半自动入库，先看候选再决策 |
| `allow` | 完全跳过冲突检测 | 已知重复但确需写入（迁移、重建） |

任何其他值抛 `ValidationError("不支持的 conflict_policy: ...")`。

## 检测维度

`strict` 与 `merge_suggest` 都跑两轮：

### 1. Alias 命中

对新条目 `aliases + title` 全部 NFC 后，查 `aliases` 表是否已存在。命中 → 候选。

### 2. Title 模糊匹配（同 kind）

对新 title 与同 kind 的所有现有 title：

- `Levenshtein.ratio(a, b) >= 0.85` → 候选
- 或 `max(len) >= 4 且 Levenshtein.distance <= 2` → 候选

`ratio` 做相似度（归一化），`distance` 做绝对编辑距离，二者任一命中即触发。

## ignore_ids

`update()` 内部会传 `ignore_ids={当前 entry.id}`，避免自己跟自己冲突。CLI/MCP 不直接暴露此参数。

## strict 异常 payload

```json
{
  "code": "EK_CONFLICT",
  "message": "冲突：检测到候选重复条目 ['ek_con_01HX...', 'ek_con_01HY...']",
  "details": {
    "candidates": [
      {"id": "ek_con_01HX...", "title": "..."},
      {"id": "ek_con_01HY...", "title": "..."}
    ]
  }
}
```

候选按 id 排序，去重。`details.candidates` 是稳定可机读的。

## merge_suggest 当前实现

当前 `merge_suggest` 与 `allow` 行为接近，检测过程跑了但不抛。要走真正的"建议合并"，调用方需自己读 candidates 决定走 link/unlink 还是 update。后续 Plan 可能扩展为返回候选列表。

## allow 风险

`allow` 跳过两轮检测，可能造成：

- 重复 alias（NFC 后冲突）
- 同 kind 高度相似 title

修复路径：`ek doctor` → `ek diagnose`。
