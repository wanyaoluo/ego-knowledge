# 操作指南：AI 编排核心流程

> 本文展开 AI 编排层的 4 个核心工作流，供 AI 助手接入策略引用。

## 适用对象

AI Agent 操作 EgoKnowledge 时的标准动作序列。每个流程都假定能力层（Core API / MCP / CLI）已就绪，本文只规定**编排顺序**。

## 流程 1：调研落盘

外部主题进入知识库的标准路径。

```text
搜索现有知识库 → 有相关条目？
  ├→ 有：拉出来展示，问是否刷新
  └→ 无：开始调研
        → 采集原始素材 → ek.ingest(kind=source)
        → 结构化提取 → ek.ingest(kind=note)
        → 搜索候选关联 → ek.link（自动织网）
        → 判断是否值得升格 → 提议 ek.promote
```

关键点：

- `source` 是不可变证据，先落 source 再落 note，证据链方向与治理红线 9 一致
- 自动织网阶段调用 `ek.search` 找候选，不要让 AI 凭印象建关系
- 升格判定门槛见 [数据模型参考](../reference/data-model.md) 的 kind 与关系约束。

## 流程 2：决策前检索

任何"做决定 / 做选型 / 给方案"前的必经步骤。

```text
ek.search → 有命中？
  ├→ 有：引用知识辅助决策，标注出处（ID + 标题）
  └→ 无：正常工作，不强制入库
```

关键点：

- 命中后**必须**走出处规范：引用条目 ID 与标题。
- 未命中不强制入库，避免低价值决策被记录污染信号

## 流程 3：关联建立

每次 `ingest` 后立即执行，不延迟到 L4 诊断。

```text
ek.search（用新条目的 tags + search_terms）
→ 筛选候选关联
→ 提议关系类型
→ ek.link 写入
```

关键点：

- `search` 输入用 tags + search_terms 而非新条目正文（噪音少，召回准）
- 关系类型选择见 [reference/data-model.md](../reference/data-model.md) 的 `relation_type` 全集
- 图变化响应会做局部检查，AI 不需要重做全量诊断。

## 流程 4：维护响应

定时或手动触发 `ek.diagnose` 后的处理路径。

```text
ek.diagnose → 诊断报告 → AI 按问题类型处理或交给用户决策
```

问题类型 → 处理路径映射：

| 诊断输出 | AI 自主处理 | 转用户决策 |
| --- | --- | --- |
| `freshness` 降级（stable→watch） | ✅ | — |
| 升格提议（note→concept） | — | ✅ |
| 撤回提议（前提被推翻） | — | ✅ |
| 重复检测（候选合并） | — | ✅ |

完整规则见 [数据模型参考](../reference/data-model.md) 与诊断命令说明。

## 错误处理

所有流程遇到 Core 抛出的错误时，按 [reference/error-types.md](../reference/error-types.md) 的 4 类错误分别响应：

- `ValidationError`：检查输入，重试或上报用户
- `ConflictError`：走冲突解决（merge_suggest / allow），不直接重试 strict
- `NotFoundError`：检索 ID 是否拼错，或上游已删除
- `StorageError`：上报用户，不自动重试（可能涉及数据完整性）

## 参考

- [search-contract.md](../reference/search-contract.md)
- [data-model.md](../reference/data-model.md)
- [error-types.md](../reference/error-types.md)
