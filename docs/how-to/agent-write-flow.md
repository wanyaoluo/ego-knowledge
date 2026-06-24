# 写入流

把 `ek_ingest`、`ek_update`、`ek_link`、`ek_promote` 串成可复核的 8 步落盘流程。

## 适用范围

- 新建 `source`/`note`/`dossier`/`concept`/`decision`/`view`
- 给现有条目补证据、补别名、补 `search_terms`
- 写入后补关系，再判断是否升格

## 8 步流程

### 1. 术语锚定

先用 [`search-contract.md`](../reference/search-contract.md) 的最小查询确认术语是否已存在。
命中后沿用已有 `title`/`aliases`/`search_terms`，不额外造同义词。

### 2. 选对动作

| 场景 | 动作 |
| --- | --- |
| 新对象首次落盘 | `ek_ingest(kind, payload)` |
| 旧条目补字段/补 alias | `ek_update(id, changes)` |
| 条目间补图关系 | `ek_link(source_id, target_id, rel_type, source)` |
| 达到升格条件 | `ek_promote(id, target_kind, freshness)` |

### 3. 补齐最小 payload

- `title` 必须非空
- `search_terms` 至少 5 条，覆盖中文主术语、英文/缩写、常见别称
- `note.source_refs`、`dossier.evidence_refs`、`concept.evidence_refs`、
  `decision.evidence_refs` 必须指向已存在条目
- `concept`/`dossier` 的 `domain` 由治理口径决定，见 [`governance-lifecycle.md`](governance-lifecycle.md)

#### source 类型专属：资产归化

`kind="source"` 写入前，须先判定内容归属并选择正确的 `source_url` 值。完整规则见 [`_validation.py`](../../src/ego_knowledge/_validation.py) 的 source_url 白名单校验。

| 内容归属 | source_url | body |
| --- | --- | --- |
| 外部资源（论文/文档/网页） | 原始 `http://`/`https://` 网址 | 写摘要或关键摘录 |
| 自有调研/分析内容 | `knowledge://` 相对标识 | 内联全文或先复制后引用 |

**禁止**：`source_url` 指向仓库路径，例如 `src/`、`tests/`、
`projects/` 等非 `knowledge://` 前缀。写入入口
（`ek_ingest` / `ek_update`）会在代码层硬闸拒绝，抛 `validation_error`。

### 4. 执行写入

典型顺序：

1. `ek_ingest(kind="source", ...)`
2. `ek_ingest(kind="note", payload={"source_refs": [...]})`
3. 如需合并旧 note → `ek_update(...)`
4. 关系确定后 → `ek_link(...)`
5. 最后才考虑 → `ek_promote(...)`

### 5. 处理冲突

`strict` 模式冲突时 `conflict_error.details.candidates` 给出候选列表。处理原则：

- 候选是旧条目的扩充 → `ek_update`
- 候选语义相同但需保留新对象 → 请求用户批准 `conflict_policy="allow"`
- 候选是不同概念 → 补更清晰的 `title`/`aliases`/`domain`

### 6. `ek_update` 的边界

不可改 `id`/`kind`/`file_path`/`metrics`。普通写入不改 `body`。
body 写入仅按 [`body-update-standard.md`](body-update-standard.md) 的单条原地入口执行。
`concept`/`dossier` 改 `domain` 触发 `请走 domains_migrate()`，
转去 [`governance-lifecycle.md`](governance-lifecycle.md)。

### 7. 关联建立

写完条目后用新条目的 `search_terms`/`tags` 再搜一遍，筛出需补边的对象。
`rel_type` 必须在 `RelationType` 枚举内（11 种）；`source` 取 `confirmed`、
`ai_suggested` 或 `ai_confirmed`。

类型专属字段通过 `ek_update` 修改对应 Entry 字段，不走 `ek_link`。
典型字段包括 `source_refs`、`evidence_refs`、`promotion_targets`、
`superseded_by`。

### 8. 写后回读 + 升格判断

本节覆盖 `ingest` / `update` / `link` / `promote` 的公共验证流程；`unlink` / `maintain` / `domains` 的验证路径类似。

最低验证按写入类型选择：

1. `ek_get(new_id)` — 新建或更新条目后确认字段可读
2. `ek_search(query=关键术语)` — 新建条目或改变检索字段后确认命中
3. `ek_related(source_id, rel_type=关系类型)` — 写入关系后确认 `target_id` 出现

任一验证失败 → 停止后续写动作，报告调用方，建议
`ek_maintain(action="doctor")`。

验证通过后才讨论 `ek_promote`。

## promote 前置条件

- `note → concept`
  - 前置：`source_refs` 非空。
  - 前置：`promotion_targets` 为空或含 `concept`。
  - 结果：新 concept 默认 `stable`，原 note 变 `legacy`。
- `note → dossier`
  - 前置：`source_refs` 非空。
  - 前置：`promotion_targets` 为空或含 `dossier`。
  - 结果：新 dossier 继承传入 freshness，生成 `review_due_at`。
  - 结果：原 note 不变。
- `dossier → concept`
  - 前置：`reviewed_at` 存在且距今 ≤30 天。
  - 前置：`evidence_refs` 非空。
  - 结果：新 concept 默认 `stable`，原 dossier 变 `legacy`。
- `concept → decision`
  - 前置：`status` 为 `active` 或 `authoritative`。
  - 前置：`evidence_refs` 非空。
  - 结果：新 decision 默认 `stable`。
  - 结果：旧 concept 补 `applied_in` 出边。

## 常见误区

- 一上来就 `allow` → 重复概念直接入库
- 用 `ek_update` 改 `domain` → 绕开路径重写与冲突检查
- 先 promote 再验证 → 失败时难回溯
- 只看 message 不看 JSON payload → 丢掉 `details.candidates`
