# 治理与生命周期

把 domain 管理、freshness 周期、promote 审批和维护口径收成一份操作手册。

## domain 管理

### `domains_add` 流程

1. `ek_domains(action="list")` 看现有词表
2. 确认命名符合 slug 规范（NFC 规范化 + 去空白）
3. `ek_domains(action="add", name="...")`

可能遇到的 error：

- `domain 名不能为空`
- `domain 名含非法字符: {name}`
- `domain 已存在: {slug}` → `conflict_error`

### `domains_migrate` 流程

1. 确认目标 domain 已存在（`action="list"`）
2. 收齐要迁的 entry ID
3. `ek_domains(action="migrate", entries=["ek_con_..."], target_domain="新域")`
4. 返回 `MigrateResult`：`entry_ids` + `rewritten_paths` + `target_domain`

硬性口径：

- 空列表 → `validation_error`：`domains_migrate 需要至少一个 entry id`
- 目标不存在 → `validation_error`：`目标 domain 不存在: {slug}`
- 路径撞名 → `conflict_error`：`domain 迁移目标已存在: {name}`

### domain 推断逻辑

`concept`/`dossier` 的 domain 推断顺序：显式 `domain` → tags 匹配已知 domain → `unsorted`。

## freshness 生命周期

| freshness | 复核周期 | 默认 kind |
| --- | --- | --- |
| `stable` | 180 天 | `concept`、`decision` |
| `watch` | 30 天 | `source`、`note`、`dossier`、`view` |
| `volatile` | 7 天 | — |

`review_due_at` 由 `freshness` 值 × 天数从 `reviewed_at` 推算。

## promote 审批边界

4 条升格路径及其 freshness 行为：

| 路径 | freshness 结果 | 治理约束 |
| --- | --- | --- |
| `note → concept` | 固定 `stable` | 推断 domain；原 note 标 `legacy` |
| `note → dossier` | 使用传入值 | 立刻生成 `review_due_at` |
| `dossier → concept` | 固定 `stable` | `reviewed_at` 距今 ≤30 天 |
| `concept → decision` | 固定 `stable` | 旧 concept 补 `applied_in` 出边 |

**始终先让用户确认再执行 promote。**

## 什么时候升，什么时候迁

| 意图 | 正确操作 |
| --- | --- |
| 整理主题归属 | `domains_migrate`，不升格 |
| 更新 dossier 新鲜度 | `ek_update` 改 `freshness` |
| 调研笔记沉淀为稳定知识 | `ek_promote` |
| 旧 dossier 收束为概念 | 先确认 `reviewed_at` 够新，再 `ek_promote` |

## 维护口径

### `ek_maintain(action="diagnose")`

知识层红线检查。当前规则：

- `redline_9_source_reachability`：非 view 条目无法追溯到 source
- `redline_10_view_as_evidence`：view 被当成证据出边目标

副作用边界见 [运行 doctor 与 diagnose](run-doctor-and-diagnose.md)。

### `ek_maintain(action="doctor")`

文件/术语/路径层健康检查。默认不修复 catalog 或文件，但会写报告。
`doctor(repair=True)` 回放 recovery log。

报告路径：`logs/diagnose/doctor-*.json`。

### `ek_maintain(action="stats")`

规模分布快照。`group_by` 支持 `kind`/`status`/`freshness`/`domain`。

### `ek_review(overdue_only=True)`

dossier 复核队列，按 `review_due_at` 升序 → `id` 升序。

### 最短维护路径

1. `diagnose` — 看红线
2. `doctor` — 看文件层
3. `stats(group_by="kind")` — 看规模
4. `review` — 刷新 dossier

## 常见坑

- 想改 concept/dossier 的 domain 却用 `ek_update` → 路径不会重写
- 把 `watch` 当"先不管" → review queue 长期堆积
- 没先建 domain 就直接 migrate
- 以为 `freshness` 能覆盖升格路径本身的硬限制（如 `reviewed_at` ≤30 天）
